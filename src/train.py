"""
train.py
========
스펙트로그램 이미지/명시적 특징으로 SVM(Linear/RBF) + Random Forest 분류기를 학습·비교합니다.

특징 방식(--mode): 픽셀 / 특징 / 둘다
- 픽셀(기본): data/spectrogram/<상태>/*.png 이미지를 grayscale 224x224 -> flatten(50,176차원)
  픽셀 특징으로 사용, PCA로 차원 축소.
- 특징: data/features.csv (preprocess.py가 함께 생성하는 명시적 통계/주파수 특징 14개), PCA 없이 StandardScaler만.
- 둘다: 픽셀+특징 모두 학습 후 비교.

비교 과제(--task): **기본은 3종(정상/수분부족/자극)** 이고, 정상을 반드시 포함한 2종 비교도 지원합니다.
- 3종(기본): 정상 vs 수분부족 vs 자극  -> models/best_model.joblib (기본 실시간 모델)
- 정상-수분부족: 정상 vs 수분부족       -> models/best_model_정상-수분부족.joblib
- 정상-자극:     정상 vs 자극           -> models/best_model_정상-자극.joblib
- 전체: 위 세 과제를 모두 학습하고 비교 표 출력

- GridSearchCV로 하이퍼파라미터 최적화, Accuracy/Precision/Recall 출력(3종은 macro 평균)
- Confusion Matrix 이미지 저장, 최적 모델 저장(bundle에 classes/feature_mode 포함)

실행:
    python train.py                          # 3종, 픽셀 (기본)
    python train.py --mode 둘다               # 3종, 픽셀+특징
    python train.py --task 정상-자극 --mode 둘다
    python train.py --task 전체 --mode 둘다    # 3과제 × 2방식 전부 학습·비교
"""

import argparse
import glob
import json
import os
import sys
import math
import time

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
from joblib import Memory
import shutil
import tempfile

_CACHE_DIR = None
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

# 비교 과제 정의. 첫 원소가 label 0.
TASKS = {
    "3종": ["정상", "수분부족", "자극"],   # 기본(정상/수분부족=지속 상태, 자극=순간 이벤트)
    "정상-수분부족": ["정상", "수분부족"],
    "정상-자극": ["정상", "자극"],
}
DEFAULT_TASK = "3종"
ALL_TASKS = "전체"

# preprocess.py가 사용한 필터 대역. 모델 번들에 저장해 inference가 동일 필터를 쓰게 한다.
# preprocess.py의 기본값과 같아야 한다(선행연구 기준 0.5~20Hz, 한국 전원 60Hz).
DEFAULT_FILTER = {"lowcut": 0.5, "highcut": 20.0, "notch_freq": 60.0, "notch_q": 30.0}
FILTER_META = dict(DEFAULT_FILTER)

# 창 길이도 학습·추론이 반드시 같아야 한다. 예전에는 이 값이 모델에 저장되지 않아
# inference.py가 자기 상수(2초)를 쓰는 바람에, preprocess의 창을 바꾸면 학습과 추론이
# 조용히 어긋났다. 이제 메타에서 읽어 모델 번들에 함께 저장한다.
DEFAULT_WINDOW = {"sample_rate": 250.0, "window_sec": 10.0, "step_sec": 2.0}
WINDOW_META = dict(DEFAULT_WINDOW)



def _pipeline_cache_dir():
    """GridSearchCV가 파이프라인 앞단(표준화·PCA)을 재사용하도록 둘 캐시 폴더.

    학습이 끝나면 지운다(다음 학습은 데이터가 달라 어차피 못 쓴다). 임시 폴더를 쓰므로
    저장소나 data/ 를 더럽히지 않는다."""
    global _CACHE_DIR
    if _CACHE_DIR is None:
        _CACHE_DIR = tempfile.mkdtemp(prefix="gml_train_cache_")
    return _CACHE_DIR


def _clear_pipeline_cache():
    global _CACHE_DIR
    if _CACHE_DIR is not None:
        shutil.rmtree(_CACHE_DIR, ignore_errors=True)
        _CACHE_DIR = None


def _load_meta(features_csv):
    """preprocess.py가 남긴 preprocess_meta.json을 읽는다(없거나 깨졌으면 빈 dict)."""
    p = os.path.join(os.path.dirname(features_csv) or ".", "preprocess_meta.json")
    if os.path.exists(p):
        try:
            with open(p, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def load_filter_meta(features_csv):
    """preprocess.py가 남긴 메타에서 필터 대역을 읽는다(없으면 기본값)."""
    m = _load_meta(features_csv)
    try:
        return {k: float(m.get(k, DEFAULT_FILTER[k])) for k in DEFAULT_FILTER}
    except Exception:
        return dict(DEFAULT_FILTER)


def load_window_meta(features_csv):
    """전처리에 쓴 창 길이/샘플레이트를 읽는다. 추론이 같은 창을 쓰도록 모델에 저장한다."""
    m = _load_meta(features_csv)
    try:
        return {k: float(m.get(k, DEFAULT_WINDOW[k])) for k in DEFAULT_WINDOW}
    except Exception:
        return dict(DEFAULT_WINDOW)


HISTORY_CAP = 50


def _append_history(models_dir, record):
    """학습할 때마다 결과를 models_dir/train_history.json에 누적한다.
    웹 관리자 패널의 '최근 학습 결과' 목록이 이 파일을 읽는다."""
    path = os.path.join(models_dir, "train_history.json")
    try:
        with open(path, encoding="utf-8") as f:
            hist = json.load(f)
    except Exception:
        hist = []
    hist.append(record)
    hist = hist[-HISTORY_CAP:]
    try:
        os.makedirs(models_dir, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(hist, f, ensure_ascii=False)
    except OSError as e:
        print(f"[train] 학습 기록 저장 실패: {e}")

# --mode 한글 CLI 값 -> 내부 처리용 값. 파일명/표시에는 다시 한글(_특징 등)을 쓴다.
MODE_CHOICES = {"픽셀": "pixel", "특징": "features", "둘다": "both"}
MODE_KO = {"pixel": "픽셀", "features": "특징"}  # 내부값 -> 표시/파일명용 한글

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
    """픽셀 모드: data/spectrogram/<상태>/*.png -> grayscale flatten 특징.
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
    """특징 모드: data/features.csv -> 명시적 통계/주파수 특징 14개.
    classes에 포함된 상태만 골라 라벨을 classes 순서로 매핑한다(픽셀 로더와 순서 공유)."""
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

    preprocess.py가 창을 겹쳐 가며 슬라이딩하므로 인접 윈도우는 서로 겹친다.
    무작위 분할을 쓰면 겹치는 윈도우가 train과 test에 동시에 들어가 데이터 누수가 생겨
    정확도가 부풀려진다. 여기서는 그룹별로 시간축을 따라 나누고, train의 마지막 guard개
    윈도우를 버려 train/test 경계 윈도우가 시간적으로 겹치지 않게 한다.

    guard 는 반드시 창 길이와 스텝에서 계산해야 한다. 창 W초를 S초씩 밀면 거리가
    W/S 미만인 윈도우끼리 겹치므로, 버려야 할 개수는 ceil(W/S) - 1 이다.
      2초 창 / 1초 스텝 -> 1개    (예전 설정)
      10초 창 / 2초 스텝 -> 4개   (현재 설정)
    이 값을 1로 고정해 두면 창을 늘렸을 때 경계에서 겹침이 그대로 남아 누수가 생긴다.
    실제로 창을 2초에서 10초로 바꾸면서 한동안 그 상태였다.

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


def overlap_guard(window_sec=None, step_sec=None):
    """train/test 경계에서 버려야 할 윈도우 수. 창 W초를 S초씩 밀면 거리 ceil(W/S)-1 까지 겹친다."""
    w = float(window_sec if window_sec is not None else WINDOW_META["window_sec"])
    st = float(step_sec if step_sec is not None else WINDOW_META["step_sec"])
    if st <= 0:
        return 1
    return max(1, int(math.ceil(w / st)) - 1)


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
                   cv_folds=CV_FOLDS_DEFAULT, task=DEFAULT_TASK):
    results = {}
    # 2종은 양성 클래스(정상 아닌 상태) 기준 binary, 3종 이상은 macro 평균.
    avg = "binary" if len(classes) == 2 else "macro"
    mode_file = "" if mode == "pixel" else "_특징"
    task_file = "" if task == DEFAULT_TASK else f"_{task}"
    file_suffix = mode_file + task_file        # 파일명용 (예: _특징_정상-자극)
    tag = f"{MODE_KO[mode]}·{task}"            # 화면 표시용 (예: 특징·정상-자극)

    if mode == "pixel":
        # PCA로 차원 축소 (224*224=50176차원 -> 계산 효율화). 특징은 여전히 "이미지 픽셀 기반".
        smallest_fold_train = int(X_train.shape[0] * (cv_folds - 1) / cv_folds)
        n_components = max(2, min(30, smallest_fold_train - 1, X_train.shape[1]))
        pca_step = [("pca", PCA(n_components=n_components, random_state=42))]
    else:
        # 명시적 특징(14차원)은 이미 저차원이라 PCA로 더 축소하면 해석성만 잃는다.
        pca_step = []

    # ---- SVM (Linear vs RBF) via GridSearchCV ----
    # 전처리 단계(표준화·PCA)는 분류기 하이퍼파라미터와 무관한데도, 캐시가 없으면
    # GridSearchCV가 후보를 바꿀 때마다 처음부터 다시 계산한다. 픽셀 방식은 이 앞단이
    # 50,176차원 PCA라 전체 학습 시간의 대부분을 차지한다(후보 12개 x 5겹이면 PCA를
    # 60번 계산). memory= 를 주면 같은 폴드의 앞단 결과를 재사용해 5번만 계산한다.
    cache = Memory(location=_pipeline_cache_dir(), verbose=0)
    svm_pipe = Pipeline([("scaler", StandardScaler())] + pca_step + [("svm", SVC(probability=True))],
                        memory=cache)
    svm_param_grid = {
        "svm__kernel": ["linear", "rbf"],
        "svm__C": [0.1, 1, 10],
        "svm__gamma": ["scale", "auto"],
    }
    n_svm = np.prod([len(v) for v in svm_param_grid.values()])
    print(f"[train] ({tag}) SVM GridSearchCV 시작: 후보 {n_svm}개 x {cv_folds}겹 = {n_svm*cv_folds}회 학습…")
    # verbose=1: 학습기가 fold 하나 끝날 때마다 한 줄씩 찍는다. 이게 없으면 GridSearchCV가
    # 몇 분씩 아무 출력 없이 도는 동안 웹 로그가 멈춘 것처럼 보인다.
    svm_grid = GridSearchCV(svm_pipe, svm_param_grid, cv=cv_folds, n_jobs=-1, verbose=1)
    svm_grid.fit(X_train, y_train)
    svm_best = svm_grid.best_estimator_
    y_pred_svm = svm_best.predict(X_test)
    print(f"[train] ({tag}) SVM 완료 -> 최적 파라미터 {svm_grid.best_params_}")

    results["SVM"] = {
        "model": svm_best,
        "best_params": svm_grid.best_params_,
        "accuracy": accuracy_score(y_test, y_pred_svm),
        "precision": precision_score(y_test, y_pred_svm, average=avg, zero_division=0),
        "recall": recall_score(y_test, y_pred_svm, average=avg, zero_division=0),
        "y_pred": y_pred_svm,
    }

    # ---- Random Forest via GridSearchCV ----
    rf_pipe = Pipeline([("scaler", StandardScaler())] + pca_step + [("rf", RandomForestClassifier(random_state=42))],
                       memory=cache)
    rf_param_grid = {
        "rf__n_estimators": [100, 200],
        "rf__max_depth": [None, 10, 20],
    }
    n_rf = np.prod([len(v) for v in rf_param_grid.values()])
    print(f"[train] ({tag}) RandomForest GridSearchCV 시작: 후보 {n_rf}개 x {cv_folds}겹 = {n_rf*cv_folds}회 학습…")
    rf_grid = GridSearchCV(rf_pipe, rf_param_grid, cv=cv_folds, n_jobs=-1, verbose=1)
    rf_grid.fit(X_train, y_train)
    rf_best = rf_grid.best_estimator_
    y_pred_rf = rf_best.predict(X_test)
    print(f"[train] ({tag}) RandomForest 완료 -> 최적 파라미터 {rf_grid.best_params_}")

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

    return results, file_suffix, tag


def run_pipeline(X, y, groups, order, models_dir, mode, classes, task):
    t0 = time.time()
    tag_prefix = f"{MODE_KO[mode]}·{task}"
    counts = {c: int(np.sum(y == i)) for i, c in enumerate(classes)}
    dist = ", ".join(f"{c}={n}" for c, n in counts.items())
    print(f"[train] ({tag_prefix}) 총 샘플 수: {len(y)}  [{dist}]")
    validate_classes(y, classes)

    # 무작위 분할 대신 상태별 시간순 분할로 윈도우 겹침에 의한 데이터 누수를 차단한다.
    guard = overlap_guard()
    X_train, X_test, y_train, y_test = chronological_group_split(X, y, groups, order,
                                                                 test_size=0.3, guard=guard)
    print(f"[train] ({tag_prefix}) Train={len(y_train)}, Test={len(y_test)} "
          f"(시간순 그룹 분할, 경계에서 겹치는 {guard}창 제거)")

    cv_folds = cv_folds_for(y_train, classes)
    results, file_suffix, tag = train_and_eval(X_train, X_test, y_train, y_test, models_dir,
                                                classes, mode=mode, cv_folds=cv_folds, task=task)

    best_name = max(results, key=lambda k: results[k]["accuracy"])
    best_res = results[best_name]
    best_model = best_res["model"]
    best_acc = best_res["accuracy"]

    feature_mode = "pixel" if mode == "pixel" else "explicit"
    os.makedirs(models_dir, exist_ok=True)

    bundle = {"model": best_model, "classes": classes, "name": best_name,
              "feature_mode": feature_mode, "filter": FILTER_META,
              # 추론이 같은 길이의 창으로 잘라야 특징이 일치한다.
              "window_sec": WINDOW_META["window_sec"],
              "sample_rate": WINDOW_META["sample_rate"]}
    if mode == "pixel":
        bundle["img_size"] = IMG_SIZE
    else:
        bundle["feature_names"] = FEATURE_NAMES

    best_path = os.path.join(models_dir, f"best_model{file_suffix}.joblib")
    joblib.dump(bundle, best_path)
    joblib.dump(results["SVM"]["model"], os.path.join(models_dir, f"svm_model{file_suffix}.joblib"))
    joblib.dump(results["RandomForest"]["model"], os.path.join(models_dir, f"rf_model{file_suffix}.joblib"))

    duration_sec = time.time() - t0
    print(f"\n[train] ({tag}) 최적 모델: {best_name} (Accuracy={best_acc:.4f}, {duration_sec:.1f}초) -> {best_path}")
    if best_acc >= 0.70:
        print(f"[train] ({tag}) ✅ 검증 요구사항 충족: Accuracy 70% 이상 달성")
    else:
        print(f"[train] ({tag}) ⚠️ Accuracy가 70% 미만입니다. 데이터/파라미터를 조정하세요.")

    _append_history(models_dir, {
        "task": task, "mode": MODE_KO[mode], "trained_at": time.time(),
        "best_name": best_name, "accuracy": best_acc,
        "precision": best_res["precision"], "recall": best_res["recall"],
        "classes": classes, "counts": counts,
        "train_n": len(y_train), "test_n": len(y_test),
        "duration_sec": round(duration_sec, 1),
        "model_file": os.path.basename(best_path),
    })

    return results, best_name, best_acc


def run_task(task, mode, args):
    """하나의 비교 과제(task)에 대해 mode(pixel/features/both)를 학습한다."""
    classes = TASKS[task]
    print(f"\n########## 과제: {task}  (클래스: {' vs '.join(classes)}) ##########")

    summary = {}
    if mode in ("pixel", "both"):
        print(f"[train] 데이터 로딩 중 (픽셀, {task})...")
        X, y, groups, order = load_dataset(args.spectrogram_dir, classes)
        _, best_name, best_acc = run_pipeline(X, y, groups, order, args.models_dir, "pixel", classes, task)
        summary[("픽셀", task)] = (best_name, best_acc)

    if mode in ("features", "both"):
        if not os.path.exists(args.features_csv):
            print(f"❌ {args.features_csv} 가 없습니다. 먼저 preprocess.py를 실행해 생성하세요.")
            sys.exit(1)
        print(f"[train] 데이터 로딩 중 (특징, {task})...")
        X, y, groups, order = load_feature_dataset(args.features_csv, classes)
        _, best_name, best_acc = run_pipeline(X, y, groups, order, args.models_dir, "features", classes, task)
        summary[("특징", task)] = (best_name, best_acc)
    return summary


def main():
    parser = argparse.ArgumentParser(description="스펙트로그램 이미지/명시적 특징 기반 식물 상태 분류 학습")
    parser.add_argument("--spectrogram_dir", default="../data/spectrogram")
    parser.add_argument("--features_csv", default="../data/features.csv")
    parser.add_argument("--models_dir", default="../models")
    parser.add_argument("--mode", choices=list(MODE_CHOICES), default="픽셀",
                         help="픽셀(기본) / 특징(명시적 특징) / 둘다")
    parser.add_argument("--task", choices=list(TASKS) + [ALL_TASKS], default=DEFAULT_TASK,
                         help="3종(기본) / 정상-수분부족 / 정상-자극 / 전체. 정상은 항상 포함")
    args = parser.parse_args()

    global FILTER_META, WINDOW_META
    FILTER_META = load_filter_meta(args.features_csv)
    WINDOW_META = load_window_meta(args.features_csv)
    print(f"[train] 필터 대역: 대역통과 {FILTER_META['lowcut']}~{FILTER_META['highcut']}Hz, "
          f"노치 {FILTER_META['notch_freq']}Hz (모델에 저장 -> 추론과 자동 일치)")
    print(f"[train] 분석 창: {WINDOW_META['window_sec']}초 @ {WINDOW_META['sample_rate']:.0f}Hz "
          f"(모델에 저장 -> 추론과 자동 일치)")

    mode = MODE_CHOICES[args.mode]  # 내부 처리용(pixel/features/both)
    tasks = list(TASKS) if args.task == ALL_TASKS else [args.task]

    summary = {}
    try:
        for task in tasks:
            summary.update(run_task(task, mode, args))
    finally:
        # 앞단 캐시는 이번 학습에서만 쓸모가 있다(데이터가 바뀌면 무효).
        _clear_pipeline_cache()

    if len(summary) > 1:
        print("\n=== 전체 비교 (과제 x 방식) ===")
        for (mode_ko, task), (name, acc) in summary.items():
            print(f"  [{task:10s}] {mode_ko:4s}: 최적 모델={name:14s} Accuracy={acc:.4f}")


if __name__ == "__main__":
    main()
