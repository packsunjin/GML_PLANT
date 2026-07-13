"""
train.py
========
두 가지 특징 방식으로 SVM(Linear/RBF) + Random Forest 분류기를 학습하고 비교합니다.

- pixel 모드(기본, 기존 방식): data/spectrogram/{정상,스트레스}/*.png 이미지를
  grayscale 224x224 -> flatten(50,176차원) 픽셀 특징으로 사용, PCA로 차원 축소.
- features 모드(신규): data/features.csv (preprocess.py가 함께 생성하는 명시적
  통계/주파수 특징 14개)를 그대로 사용, PCA 없이 StandardScaler만 적용.

- GridSearchCV로 하이퍼파라미터 최적화
- Accuracy / Precision / Recall 출력
- Confusion Matrix 이미지 저장 (models/confusion_matrix_*.png, features 모드는 _features 접미사)
- 가장 성능이 좋은 모델을 models/best_model.joblib (pixel) / best_model_features.joblib (features)로 저장
  - bundle에 "feature_mode" 키("pixel" 또는 "explicit")가 들어가 inference.py가 자동으로
    올바른 특징 추출 방식을 선택한다. 이 키가 없는 예전 joblib 파일은 전부 pixel 모드로 학습된
    것이므로 "pixel"로 취급한다.

실행:
    python train.py                  # pixel 모드만 (기존과 동일)
    python train.py --mode features  # 명시적 특징 모드만
    python train.py --mode both      # 둘 다 학습하고 비교 표 출력
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
import matplotlib.pyplot as _plt_font_setup
_plt_font_setup.rcParams["font.family"] = "Noto Sans KR"
_plt_font_setup.rcParams["axes.unicode_minus"] = False
import matplotlib.pyplot as plt
from PIL import Image

from sklearn.model_selection import train_test_split, GridSearchCV
from sklearn.svm import SVC
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import accuracy_score, precision_score, recall_score, confusion_matrix, ConfusionMatrixDisplay
from sklearn.decomposition import PCA
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from feature_extraction import FEATURE_NAMES

IMG_SIZE = 224
CLASSES = ["정상", "스트레스"]  # label 0, 1 (features 모드 로더도 이 순서를 그대로 공유한다)
CLASSES_EN = ["Normal", "Stress"]  # confusion matrix 표시용 (한글 폰트 미설치 환경 경고 방지)
CV_FOLDS_DEFAULT = 3


def load_dataset(spectrogram_dir):
    """pixel 모드: data/spectrogram/{정상,스트레스}/*.png -> grayscale flatten 특징"""
    X, y = [], []
    for label_idx, cls in enumerate(CLASSES):
        cls_dir = os.path.join(spectrogram_dir, cls)
        paths = sorted(glob.glob(os.path.join(cls_dir, "*.png")))
        for p in paths:
            img = Image.open(p).convert("L")  # grayscale, 고정 크기
            if img.size != (IMG_SIZE, IMG_SIZE):  # 저장 시 이미 IMG_SIZE로 렌더링되므로 보통 그대로 통과
                img = img.resize((IMG_SIZE, IMG_SIZE))
            arr = np.asarray(img, dtype=np.float32).flatten() / 255.0
            X.append(arr)
            y.append(label_idx)
    return np.array(X), np.array(y)


def load_feature_dataset(features_csv_path):
    """features 모드: data/features.csv -> 명시적 통계/주파수 특징 14개.
    라벨은 train.py의 CLASSES 리스트로 매핑해 pixel 로더와 순서를 공유한다."""
    df = pd.read_csv(features_csv_path)
    label_to_idx = {cls: i for i, cls in enumerate(CLASSES)}
    X = df[FEATURE_NAMES].to_numpy(dtype=np.float64)
    y = df["label"].map(label_to_idx).to_numpy()
    return X, y


def validate_classes(y, context="전체"):
    """클래스별 샘플 수를 검증한다. 학습이 근본적으로 불가능한 경우(클래스 누락/
    샘플 극소)는 알아보기 힘든 sklearn 예외 대신 명확한 한국어 메시지로 즉시 종료한다."""
    counts = {cls: int(np.sum(y == i)) for i, cls in enumerate(CLASSES)}
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


def cv_folds_for(y_train, cv_default=CV_FOLDS_DEFAULT):
    """GridSearchCV(cv=...)에 실제로 전달할 폴드 수를, y_train(학습 분할 이후) 기준
    클래스별 샘플 수에 맞춰 정한다. 전체 데이터가 아니라 반드시 분할 이후의
    y_train으로 계산해야 한다 (GridSearchCV가 내부적으로 나누는 것도 y_train이므로)."""
    validate_classes(y_train, context="학습 분할(train split)")
    counts = {cls: int(np.sum(y_train == i)) for i, cls in enumerate(CLASSES)}
    smallest_n = min(counts.values())
    cv_folds = min(cv_default, smallest_n)
    if cv_folds < cv_default:
        print(f"⚠️  학습 분할 내 클래스별 최소 샘플 수({smallest_n})가 적어 GridSearchCV 폴드를 "
              f"{cv_default} -> {cv_folds}로 자동 축소합니다.")
    return cv_folds


def train_and_eval(X_train, X_test, y_train, y_test, out_dir, mode="pixel", cv_folds=CV_FOLDS_DEFAULT):
    results = {}
    suffix = "" if mode == "pixel" else "_features"

    if mode == "pixel":
        # PCA로 차원 축소 (224*224=50176차원 -> 계산 효율화). 특징은 여전히 "이미지 픽셀 기반".
        # GridSearchCV(cv=cv_folds) 내부적으로 학습 폴드 크기가 더 작아지므로 그 크기 이하로 제한한다.
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
        "precision": precision_score(y_test, y_pred_svm, zero_division=0),
        "recall": recall_score(y_test, y_pred_svm, zero_division=0),
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
        "precision": precision_score(y_test, y_pred_rf, zero_division=0),
        "recall": recall_score(y_test, y_pred_rf, zero_division=0),
        "y_pred": y_pred_rf,
    }

    # ---- 리포트 출력 + confusion matrix 저장 ----
    os.makedirs(out_dir, exist_ok=True)
    for name, res in results.items():
        print(f"\n=== {name} ({mode}) ===")
        print(f"Best params: {res['best_params']}")
        print(f"Accuracy : {res['accuracy']:.4f}")
        print(f"Precision: {res['precision']:.4f}")
        print(f"Recall   : {res['recall']:.4f}")

        cm = confusion_matrix(y_test, res["y_pred"])
        disp = ConfusionMatrixDisplay(confusion_matrix=cm, display_labels=CLASSES_EN)
        fig, ax = plt.subplots(figsize=(4, 4))
        disp.plot(ax=ax, cmap="viridis", colorbar=False)
        ax.set_title(f"{name} Confusion Matrix ({mode})")
        fig.tight_layout()
        cm_path = os.path.join(out_dir, f"confusion_matrix_{name}{suffix}.png")
        fig.savefig(cm_path)
        plt.close(fig)
        print(f"Confusion matrix saved -> {cm_path}")

    return results


def run_pipeline(X, y, models_dir, mode):
    print(f"[train] ({mode}) 총 샘플 수: {len(y)}, 정상={np.sum(y == 0)}, 스트레스={np.sum(y == 1)}")
    validate_classes(y)

    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.3, random_state=42, stratify=y
    )
    print(f"[train] ({mode}) Train={len(y_train)}, Test={len(y_test)}")

    # GridSearchCV가 실제로 나누는 대상은 y_train이므로, cv 폴드 수도 y_train 기준으로 정한다.
    cv_folds = cv_folds_for(y_train)

    results = train_and_eval(X_train, X_test, y_train, y_test, models_dir, mode=mode, cv_folds=cv_folds)

    best_name = max(results, key=lambda k: results[k]["accuracy"])
    best_model = results[best_name]["model"]
    best_acc = results[best_name]["accuracy"]

    suffix = "" if mode == "pixel" else "_features"
    feature_mode = "pixel" if mode == "pixel" else "explicit"
    os.makedirs(models_dir, exist_ok=True)

    bundle = {"model": best_model, "classes": CLASSES, "name": best_name, "feature_mode": feature_mode}
    if mode == "pixel":
        bundle["img_size"] = IMG_SIZE
    else:
        bundle["feature_names"] = FEATURE_NAMES

    best_path = os.path.join(models_dir, f"best_model{suffix}.joblib")
    joblib.dump(bundle, best_path)
    joblib.dump(results["SVM"]["model"], os.path.join(models_dir, f"svm_model{suffix}.joblib"))
    joblib.dump(results["RandomForest"]["model"], os.path.join(models_dir, f"rf_model{suffix}.joblib"))

    print(f"\n[train] ({mode}) 최적 모델: {best_name} (Accuracy={best_acc:.4f}) -> {best_path}")
    if best_acc >= 0.70:
        print(f"[train] ({mode}) ✅ 검증 요구사항 충족: Accuracy 70% 이상 달성")
    else:
        print(f"[train] ({mode}) ⚠️ Accuracy가 70% 미만입니다. 데이터/파라미터를 조정하세요.")

    return results, best_name, best_acc


def main():
    parser = argparse.ArgumentParser(description="스펙트로그램 이미지/명시적 특징 기반 식물 상태 분류 학습")
    parser.add_argument("--spectrogram_dir", default="../data/spectrogram")
    parser.add_argument("--features_csv", default="../data/features.csv")
    parser.add_argument("--models_dir", default="../models")
    parser.add_argument("--mode", choices=["pixel", "features", "both"], default="pixel",
                         help="pixel(기본, 기존 방식) / features(명시적 특징) / both(둘 다 학습 후 비교)")
    args = parser.parse_args()

    summary = {}

    if args.mode in ("pixel", "both"):
        print("[train] 데이터 로딩 중 (pixel)...")
        X, y = load_dataset(args.spectrogram_dir)
        _, best_name, best_acc = run_pipeline(X, y, args.models_dir, mode="pixel")
        summary["pixel"] = (best_name, best_acc)

    if args.mode in ("features", "both"):
        if not os.path.exists(args.features_csv):
            print(f"❌ {args.features_csv} 가 없습니다. 먼저 preprocess.py를 실행해 생성하세요.")
            sys.exit(1)
        print("[train] 데이터 로딩 중 (features)...")
        X, y = load_feature_dataset(args.features_csv)
        _, best_name, best_acc = run_pipeline(X, y, args.models_dir, mode="features")
        summary["features"] = (best_name, best_acc)

    if len(summary) > 1:
        print("\n=== pixel vs features 비교 ===")
        for mode, (name, acc) in summary.items():
            print(f"  {mode:10s}: 최적 모델={name:14s} Accuracy={acc:.4f}")


if __name__ == "__main__":
    main()
