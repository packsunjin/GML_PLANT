"""
train.py
========
스펙트로그램 이미지/명시적 특징으로 SVM(Linear/RBF) + Random Forest 분류기를 학습·비교합니다.

특징 방식(--mode):
- pixel(기본): data/spectrogram/<상태>/*.png 이미지를 grayscale 224x224 -> flatten(50,176차원)
  픽셀 특징으로 사용, PCA로 차원 축소.
- features: data/features.csv (preprocess.py가 함께 생성하는 명시적 통계/주파수 특징 14개), PCA 없이 StandardScaler만.
- both: 둘 다 학습 후 비교.

비교 과제(--task): **기본은 3-class(정상/수분부족/자극)** 이고, 정상을 반드시 포함한 2-class 비교도 지원합니다.
- 3class(기본): 정상 vs 수분부족 vs 자극  -> models/best_model.joblib (기본 실시간 모델)
- 정상-수분부족: 정상 vs 수분부족          -> models/best_model_정상-수분부족.joblib
- 정상-자극:     정상 vs 자극              -> models/best_model_정상-자극.joblib
- all: 위 세 과제를 모두 학습하고 비교 표 출력

- GridSearchCV로 하이퍼파라미터 최적화, Accuracy/Precision/Recall 출력(3-class는 macro 평균)
- Confusion Matrix 이미지 저장, 최적 모델 저장(bundle에 classes/feature_mode 포함)

실행:
    python train.py                          # 3-class, pixel
    python train.py --mode both              # 3-class, pixel+features
    python train.py --task 정상-자극 --mode both
    python train.py --task all --mode both   # 3과제 x 2방식 전부 학습·비교
"""

import argparse
import glob
import os
import sys

import numpy as np
import pandas as pd
import joblib
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from PIL import Image

from font_utils import setup_korean_font
# 설치된 한글 폰트를 자동 지정(없으면 None). confusion matrix 라벨을 한글로 쓸지 여부에 사용.
_KOREAN_FONT = setup_korean_font()

from sklearn.model_selection import GridSearchCV
from sklearn.svm import SVC
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import accuracy_score, precision_score, recall_score, confusion_matrix, ConfusionMatrixDisplay
from sklearn.decomposition import PCA
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from feature_extraction import FEATURE_NAMES

IMG_SIZE = 224

# 원시 상태(=preprocess.py가 만드는 폴더/라벨). 한글 폰트가 없을 때 confusion matrix용 영어 대체 라벨.
STATE_EN = {"정상": "Normal", "수분부족": "WaterDeficit", "자극": "Stimulus"}

# 비교 과제 정의. 정상은 모든 과제에 반드시 포함된다. 첫 원소가 label 0.
TASKS = {
    "3class": ["정상", "수분부족", "자극"],   # 기본
    "정상-수분부족": ["정상", "수분부족"],
    "정상-자극": ["정상", "자극"],
}
DEFAULT_TASK = "3class"
CV_FOLDS_DEFAULT = 3


def _parse_img_name(img_name):
    """'정상_000123.png' -> ('정상', 123): 원본 소스 상태(그룹)와 윈도우 시작 오프셋.
    preprocess.py가 img_name을 f'{base_name}_{start:06d}.png'로 만들므로 마지막 '_' 기준으로 나눈다."""
    base = os.path.splitext(img_name)[0]
    src, _, off = base.rpartition("_")
    try:
        return src, int(off)
    except ValueError:
        return base, 0


def load_dataset(spectrogram_dir, classes):
    """pixel 모드: data/spectrogram/<상태>/*.png -> grayscale flatten 특징.
    classes에 포함된 상태 폴더만 읽고, 라벨은 classes에서의 순서(index)로 매긴다.
    시간순 그룹 분할을 위해 (원본 상태, 윈도우 오프셋)도 함께 반환한다."""
    X, y, groups, order = [], [], [], []
    for label_idx, cls in enumerate(classes):
        cls_dir = os.path.join(spectrogram_dir, cls)
        paths = sorted(glob.glob(os.path.join(cls_dir, "*.png")))
        for p in paths:
            img = Image.open(p).convert("L")  # grayscale, 고정 크기
            if img.size != (IMG_SIZE, IMG_SIZE):  # 저장 시 이미 IMG_SIZE로 렌더링되므로 보통 그대로 통과
                img = img.resize((IMG_SIZE, IMG_SIZE))
            arr = np.asarray(img, dtype=np.float32).flatten() / 255.0
            src, off = _parse_img_name(os.path.basename(p))
            X.append(arr)
            y.append(label_idx)
            groups.append(src)
            order.append(off)
    return np.array(X), np.array(y), np.array(groups), np.array(order)


def load_feature_dataset(features_csv_path, classes):
    """features 모드: data/features.csv -> 명시적 통계/주파수 특징 14개.
    classes에 포함된 상태만 골라 라벨을 classes 순서로 매핑한다(pixel 로더와 순서 공유)."""
    df = pd.read_csv(features_csv_path)
    df = df[df["label"].isin(classes)].reset_index(drop=True)
    label_to_idx = {cls: i for i, cls in enumerate(classes)}
    X = df[FEATURE_NAMES].to_numpy(dtype=np.float64)
    y = df["label"].map(label_to_idx).to_numpy()
    groups = df["source_file"].to_numpy()
    order = df["img_name"].map(lambda n: _parse_img_name(n)[1]).to_numpy()
    return X, y, groups, order


def chronological_group_split(X, y, groups, order, test_size=0.3, guard=1):
    """원본 상태(그룹)별로 윈도우를 시간순 정렬한 뒤, 앞부분을 train, 뒷부분을 test로 나눈다.

    preprocess.py가 2초 윈도우 / 1초 스텝으로 슬라이딩하므로 인접 윈도우는 50%가 겹친다.
    무작위 분할을 쓰면 겹치는 윈도우가 train과 test에 동시에 들어가 데이터 누수가 생겨
    정확도가 부풀려진다. 여기서는 그룹별로 시간축을 따라 나누고, train의 마지막 guard개
    윈도우를 버려 train/test 경계 윈도우가 시간적으로 겹치지 않게 한다.

    각 상태(소스)는 단일 클래스라, 상태마다 앞/뒤로 나누면 모든 클래스가 train과 test에
    비례적으로 포함된다(stratify 없이도 클래스 균형 유지)."""
    train_idx, test_idx = [], []
    for g in np.unique(groups):
        g_idx = np.where(groups == g)[0]
        g_idx = g_idx[np.argsort(order[g_idx])]  # 시간순
        n = len(g_idx)
        n_test = min(max(1, int(round(n * test_size))), n - 1)  # train도 최소 1개 확보
        split = n - n_test
        train_end = max(1, split - guard)  # guard 윈도우 제거(겹침 차단)
        train_idx.extend(g_idx[:train_end].tolist())
        test_idx.extend(g_idx[split:].tolist())
    train_idx = np.array(train_idx)
    test_idx = np.array(test_idx)
    return X[train_idx], X[test_idx], y[train_idx], y[test_idx]


def validate_classes(y, classes, context="전체"):
    """클래스별 샘플 수를 검증한다. 학습이 근본적으로 불가능한 경우(클래스 누락/샘플 극소)는
    알아보기 힘든 sklearn 예외 대신 명확한 한국어 메시지로 즉시 종료한다."""
    counts = {cls: int(np.sum(y == i)) for i, cls in enumerate(classes)}
    missing = [cls for cls, c in counts.items() if c == 0]
    if missing:
        print(f"❌ '{', '.join(missing)}' 클래스에 샘플이 없습니다.")
        print("   data/raw/ 에 해당 상태의 CSV를 먼저 수집하고 preprocess.py를 실행한 뒤 다시 시도하세요.")
        sys.exit(1)

    smallest_cls = min(counts, key=counts.get)
    smallest_n = counts[smallest_cls]
    if smallest_n < 2:
        print(f"❌ '{smallest_cls}' 클래스 샘플이 {context} 기준 {smallest_n}개뿐이라 학습이 불가능합니다.")
        print("   최소 2개 이상 필요합니다. 데이터를 더 수집한 뒤 다시 시도하세요.")
        sys.exit(1)
    return counts


def cv_folds_for(y_train, classes, cv_default=CV_FOLDS_DEFAULT):
    """GridSearchCV(cv=...)에 실제로 전달할 폴드 수를, y_train(학습 분할 이후) 기준
    클래스별 샘플 수에 맞춰 정한다."""
    validate_classes(y_train, classes, context="학습 분할(train split)")
    counts = {cls: int(np.sum(y_train == i)) for i, cls in enumerate(classes)}
    smallest_n = min(counts.values())
    cv_folds = min(cv_default, smallest_n)
    if cv_folds < cv_default:
        print(f"⚠️  학습 분할 내 클래스별 최소 샘플 수({smallest_n})가 적어 GridSearchCV 폴드를 "
              f"{cv_default} -> {cv_folds}로 자동 축소합니다.")
    return cv_folds


def train_and_eval(X_train, X_test, y_train, y_test, out_dir, classes, mode="pixel",
                   cv_folds=CV_FOLDS_DEFAULT, task_suffix=""):
    results = {}
    # 2-class는 양성 클래스(정상 아닌 상태) 기준 binary, 3-class 이상은 macro 평균.
    avg = "binary" if len(classes) == 2 else "macro"
    mode_suffix = "" if mode == "pixel" else "_features"
    file_suffix = mode_suffix + task_suffix   # 파일명용 (예: _features_정상-자극)
    tag = f"{mode}{task_suffix}"              # 화면 표시용 (예: features_정상-자극)

    if mode == "pixel":
        # PCA로 차원 축소 (224*224=50176차원 -> 계산 효율화). 특징은 여전히 "이미지 픽셀 기반".
        smallest_fold_train = int(X_train.shape[0] * (cv_folds - 1) / cv_folds)
        n_components = max(2, min(30, smallest_fold_train - 1, X_train.shape[1]))
        pca_step = [("pca", PCA(n_components=n_components, random_state=42))]
    else:
        # 명시적 특징(14차원)은 이미 저차원이라 PCA로 더 축소하면 해석성만 잃는다.
        pca_step = []

    # ---- SVM (Linear vs RBF) via GridSearchCV ----
    svm_pipe = Pipeline([("scaler", StandardScaler())] + pca_step + [("svm", SVC(probability=True))])
    svm_param_grid = {
        "svm__kernel": ["linear", "rbf"],
        "svm__C": [0.1, 1, 10],
        "svm__gamma": ["scale", "auto"],
    }
    svm_grid = GridSearchCV(svm_pipe, svm_param_grid, cv=cv_folds, n_jobs=-1)
    svm_grid.fit(X_train, y_train)
    svm_best = svm_grid.best_estimator_
    y_pred_svm = svm_best.predict(X_test)

    results["SVM"] = {
        "model": svm_best,
        "best_params": svm_grid.best_params_,
        "accuracy": accuracy_score(y_test, y_pred_svm),
        "precision": precision_score(y_test, y_pred_svm, average=avg, zero_division=0),
        "recall": recall_score(y_test, y_pred_svm, average=avg, zero_division=0),
        "y_pred": y_pred_svm,
    }

    # ---- Random Forest via GridSearchCV ----
    rf_pipe = Pipeline([("scaler", StandardScaler())] + pca_step + [("rf", RandomForestClassifier(random_state=42))])
    rf_param_grid = {
        "rf__n_estimators": [100, 200],
        "rf__max_depth": [None, 10, 20],
    }
    rf_grid = GridSearchCV(rf_pipe, rf_param_grid, cv=cv_folds, n_jobs=-1)
    rf_grid.fit(X_train, y_train)
    rf_best = rf_grid.best_estimator_
    y_pred_rf = rf_best.predict(X_test)

    results["RandomForest"] = {
        "model": rf_best,
        "best_params": rf_grid.best_params_,
        "accuracy": accuracy_score(y_test, y_pred_rf),
        "precision": precision_score(y_test, y_pred_rf, average=avg, zero_division=0),
        "recall": recall_score(y_test, y_pred_rf, average=avg, zero_division=0),
        "y_pred": y_pred_rf,
    }

    # ---- 리포트 출력 + confusion matrix 저장 ----
    os.makedirs(out_dir, exist_ok=True)
    display_labels = classes if _KOREAN_FONT else [STATE_EN.get(c, c) for c in classes]
    for name, res in results.items():
        print(f"\n=== {name} ({tag}) ===")
        print(f"Best params: {res['best_params']}")
        print(f"Accuracy : {res['accuracy']:.4f}")
        print(f"Precision: {res['precision']:.4f}  ({avg})")
        print(f"Recall   : {res['recall']:.4f}  ({avg})")

        cm = confusion_matrix(y_test, res["y_pred"], labels=list(range(len(classes))))
        title = (f"{name} 혼동행렬" if _KOREAN_FONT else f"{name} Confusion Matrix") + f" ({tag})"
        disp = ConfusionMatrixDisplay(confusion_matrix=cm, display_labels=display_labels)
        fig, ax = plt.subplots(figsize=(4.5, 4.5))
        disp.plot(ax=ax, cmap="viridis", colorbar=False)
        ax.set_title(title)
        fig.tight_layout()
        cm_path = os.path.join(out_dir, f"confusion_matrix_{name}{file_suffix}.png")
        fig.savefig(cm_path)
        plt.close(fig)
        print(f"Confusion matrix saved -> {cm_path}")

    return results


def run_pipeline(X, y, groups, order, models_dir, mode, classes, task_suffix):
    dist = ", ".join(f"{c}={int(np.sum(y == i))}" for i, c in enumerate(classes))
    print(f"[train] ({mode}{task_suffix}) 총 샘플 수: {len(y)}  [{dist}]")
    validate_classes(y, classes)

    # 무작위 분할 대신 상태별 시간순 분할로 윈도우 겹침에 의한 데이터 누수를 차단한다.
    X_train, X_test, y_train, y_test = chronological_group_split(X, y, groups, order, test_size=0.3)
    print(f"[train] ({mode}{task_suffix}) Train={len(y_train)}, Test={len(y_test)} (시간순 그룹 분할, 겹침 제거)")

    cv_folds = cv_folds_for(y_train, classes)

    mode_suffix = "" if mode == "pixel" else "_features"
    file_suffix = mode_suffix + task_suffix  # 예: _features_정상-자극 (모델/이미지 파일명용)
    results = train_and_eval(X_train, X_test, y_train, y_test, models_dir, classes,
                             mode=mode, cv_folds=cv_folds, task_suffix=task_suffix)

    best_name = max(results, key=lambda k: results[k]["accuracy"])
    best_model = results[best_name]["model"]
    best_acc = results[best_name]["accuracy"]

    feature_mode = "pixel" if mode == "pixel" else "explicit"
    os.makedirs(models_dir, exist_ok=True)

    bundle = {"model": best_model, "classes": classes, "name": best_name, "feature_mode": feature_mode}
    if mode == "pixel":
        bundle["img_size"] = IMG_SIZE
    else:
        bundle["feature_names"] = FEATURE_NAMES

    best_path = os.path.join(models_dir, f"best_model{file_suffix}.joblib")
    joblib.dump(bundle, best_path)
    joblib.dump(results["SVM"]["model"], os.path.join(models_dir, f"svm_model{file_suffix}.joblib"))
    joblib.dump(results["RandomForest"]["model"], os.path.join(models_dir, f"rf_model{file_suffix}.joblib"))

    print(f"\n[train] ({mode}{task_suffix}) 최적 모델: {best_name} (Accuracy={best_acc:.4f}) -> {best_path}")
    if best_acc >= 0.70:
        print(f"[train] ({mode}{task_suffix}) ✅ 검증 요구사항 충족: Accuracy 70% 이상 달성")
    else:
        print(f"[train] ({mode}{task_suffix}) ⚠️ Accuracy가 70% 미만입니다. 데이터/파라미터를 조정하세요.")

    return results, best_name, best_acc


def run_task(task, args):
    """하나의 비교 과제(task)에 대해 --mode(pixel/features/both)를 학습한다."""
    classes = TASKS[task]
    # 기본 과제(3class)는 접미사 없이 canonical 파일명(best_model.joblib)으로 저장 -> main.py 기본 모델.
    task_suffix = "" if task == DEFAULT_TASK else f"_{task}"
    print(f"\n########## 과제: {task}  (클래스: {' vs '.join(classes)}) ##########")

    summary = {}
    if args.mode in ("pixel", "both"):
        print(f"[train] 데이터 로딩 중 (pixel, {task})...")
        X, y, groups, order = load_dataset(args.spectrogram_dir, classes)
        _, best_name, best_acc = run_pipeline(X, y, groups, order, args.models_dir, "pixel", classes, task_suffix)
        summary[("pixel", task)] = (best_name, best_acc)

    if args.mode in ("features", "both"):
        if not os.path.exists(args.features_csv):
            print(f"❌ {args.features_csv} 가 없습니다. 먼저 preprocess.py를 실행해 생성하세요.")
            sys.exit(1)
        print(f"[train] 데이터 로딩 중 (features, {task})...")
        X, y, groups, order = load_feature_dataset(args.features_csv, classes)
        _, best_name, best_acc = run_pipeline(X, y, groups, order, args.models_dir, "features", classes, task_suffix)
        summary[("features", task)] = (best_name, best_acc)
    return summary


def main():
    parser = argparse.ArgumentParser(description="스펙트로그램 이미지/명시적 특징 기반 식물 상태 분류 학습")
    parser.add_argument("--spectrogram_dir", default="../data/spectrogram")
    parser.add_argument("--features_csv", default="../data/features.csv")
    parser.add_argument("--models_dir", default="../models")
    parser.add_argument("--mode", choices=["pixel", "features", "both"], default="pixel",
                         help="pixel(기본) / features(명시적 특징) / both(둘 다)")
    parser.add_argument("--task", choices=list(TASKS) + ["all"], default=DEFAULT_TASK,
                         help="3class(기본) / 정상-수분부족 / 정상-자극 / all(전부). 정상은 항상 포함")
    args = parser.parse_args()

    tasks = list(TASKS) if args.task == "all" else [args.task]
    summary = {}
    for task in tasks:
        summary.update(run_task(task, args))

    if len(summary) > 1:
        print("\n=== 전체 비교 (과제 x 방식) ===")
        for (mode, task), (name, acc) in summary.items():
            print(f"  [{task:10s}] {mode:8s}: 최적 모델={name:14s} Accuracy={acc:.4f}")


if __name__ == "__main__":
    main()
