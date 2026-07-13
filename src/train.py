"""
train.py
========
data/spectrogram/{정상,스트레스}/*.png 이미지를 픽셀 flatten 특징으로 사용하여
Scikit-learn만으로 SVM(Linear/RBF) + Random Forest 분류기를 학습합니다.

- GridSearchCV로 하이퍼파라미터 최적화
- Accuracy / Precision / Recall 출력
- Confusion Matrix 이미지 저장 (models/confusion_matrix_*.png)
- 가장 성능이 좋은 모델을 models/best_model.joblib 로 저장

실행:
    python train.py
"""

import argparse
import glob
import os

import numpy as np
import joblib
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as _plt_font_setup
_plt_font_setup.rcParams["font.family"] = "Noto Sans CJK JP"
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

IMG_SIZE = 224
CLASSES = ["정상", "스트레스"]  # label 0, 1
CLASSES_EN = ["Normal", "Stress"]  # confusion matrix 표시용 (한글 폰트 미설치 환경 경고 방지)


def load_dataset(spectrogram_dir):
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


def train_and_eval(X_train, X_test, y_train, y_test, out_dir):
    results = {}

    # PCA로 차원 축소 (224*224=50176차원 -> 계산 효율화). 특징은 여전히 "이미지 픽셀 기반".
    # GridSearchCV(cv=3) 내부적으로 학습 폴드 크기가 더 작아지므로 그 크기 이하로 제한한다.
    cv_folds = 3
    smallest_fold_train = int(X_train.shape[0] * (cv_folds - 1) / cv_folds)
    n_components = max(2, min(30, smallest_fold_train - 1, X_train.shape[1]))

    # ---- SVM (Linear vs RBF) via GridSearchCV ----
    svm_pipe = Pipeline([
        ("scaler", StandardScaler()),
        ("pca", PCA(n_components=n_components, random_state=42)),
        ("svm", SVC(probability=True)),
    ])
    svm_param_grid = {
        "svm__kernel": ["linear", "rbf"],
        "svm__C": [0.1, 1, 10],
        "svm__gamma": ["scale", "auto"],
    }
    svm_grid = GridSearchCV(svm_pipe, svm_param_grid, cv=3, n_jobs=-1)
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
    rf_pipe = Pipeline([
        ("scaler", StandardScaler()),
        ("pca", PCA(n_components=n_components, random_state=42)),
        ("rf", RandomForestClassifier(random_state=42)),
    ])
    rf_param_grid = {
        "rf__n_estimators": [100, 200],
        "rf__max_depth": [None, 10, 20],
    }
    rf_grid = GridSearchCV(rf_pipe, rf_param_grid, cv=3, n_jobs=-1)
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
        print(f"\n=== {name} ===")
        print(f"Best params: {res['best_params']}")
        print(f"Accuracy : {res['accuracy']:.4f}")
        print(f"Precision: {res['precision']:.4f}")
        print(f"Recall   : {res['recall']:.4f}")

        cm = confusion_matrix(y_test, res["y_pred"])
        disp = ConfusionMatrixDisplay(confusion_matrix=cm, display_labels=CLASSES_EN)
        fig, ax = plt.subplots(figsize=(4, 4))
        disp.plot(ax=ax, cmap="viridis", colorbar=False)
        ax.set_title(f"{name} Confusion Matrix")
        fig.tight_layout()
        cm_path = os.path.join(out_dir, f"confusion_matrix_{name}.png")
        fig.savefig(cm_path)
        plt.close(fig)
        print(f"Confusion matrix saved -> {cm_path}")

    return results


def main():
    parser = argparse.ArgumentParser(description="스펙트로그램 이미지 기반 식물 상태 분류 학습")
    parser.add_argument("--spectrogram_dir", default="../data/spectrogram")
    parser.add_argument("--models_dir", default="../models")
    parser.add_argument("--test_size", type=float, default=0.3)
    args = parser.parse_args()

    print("[train] 데이터 로딩 중...")
    X, y = load_dataset(args.spectrogram_dir)
    print(f"[train] 총 샘플 수: {len(y)}, 정상={np.sum(y==0)}, 스트레스={np.sum(y==1)}")

    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=args.test_size, random_state=42, stratify=y
    )
    print(f"[train] Train={len(y_train)}, Test={len(y_test)}")

    results = train_and_eval(X_train, X_test, y_train, y_test, args.models_dir)

    # 정확도가 더 높은 모델을 best_model로 저장
    best_name = max(results, key=lambda k: results[k]["accuracy"])
    best_model = results[best_name]["model"]
    best_acc = results[best_name]["accuracy"]

    os.makedirs(args.models_dir, exist_ok=True)
    best_path = os.path.join(args.models_dir, "best_model.joblib")
    joblib.dump({"model": best_model, "classes": CLASSES, "img_size": IMG_SIZE, "name": best_name}, best_path)

    # 개별 모델도 저장
    joblib.dump(results["SVM"]["model"], os.path.join(args.models_dir, "svm_model.joblib"))
    joblib.dump(results["RandomForest"]["model"], os.path.join(args.models_dir, "rf_model.joblib"))

    print(f"\n[train] 최적 모델: {best_name} (Accuracy={best_acc:.4f}) -> {best_path}")
    if best_acc >= 0.70:
        print("[train] ✅ 검증 요구사항 충족: Accuracy 70% 이상 달성")
    else:
        print("[train] ⚠️ Accuracy가 70% 미만입니다. 데이터/파라미터를 조정하세요.")


if __name__ == "__main__":
    main()
