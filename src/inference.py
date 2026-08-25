"""
inference.py
============
저장된 최적 모델(models/best_model.joblib)을 로드하여
ADS1115(또는 시뮬레이션)로부터 들어오는 실시간 신호를 일정 버퍼 단위로 모아
대역통과필터 -> 스펙트로그램 -> 모델 추론을 반복 수행합니다.

gui.py 및 main.py에서 import하여 사용합니다.
"""

import os
import time
from collections import deque
from functools import lru_cache

import numpy as np
import joblib
from scipy.signal import butter, filtfilt, iirnotch

from sensor_control import read_single_realtime, HARDWARE_AVAILABLE
from spectro_render import render_gray_array, compute_spectrogram
from feature_extraction import extract_features

SAMPLE_RATE_HZ = 250.0
# 창 길이 기본값. 실제로는 모델 번들에 저장된 값(학습 때 쓴 창)을 우선한다 —
# 여기 상수를 그대로 쓰면 preprocess의 창을 바꿨을 때 학습/추론이 조용히 어긋난다.
WINDOW_SEC = 10.0
IMG_SIZE = 224
PREDICT_HZ = 5.0  # 실시간 루프에서 무거운 특징추출+추론을 반복할 최대 빈도
SMOOTH_SEC = 1.0  # 예측 스무딩 구간(초). 개수가 아니라 시간으로 지정한다(아래 설명)

# 상태 이모지 매핑 (정상/수분부족/자극 + 구버전 스트레스)
STATE_EMOJI = {"정상": "🌱", "수분부족": "💧", "자극": "⚡", "스트레스": "😵"}


@lru_cache(maxsize=8)
def _design_bandpass(fs, low, high, order):
    """Butterworth 계수는 (fs, low, high, order)가 고정이면 항상 동일하므로 캐시하여 재사용한다."""
    nyq = 0.5 * fs
    return butter(order, [low / nyq, high / nyq], btype="band")


@lru_cache(maxsize=8)
def _design_notch(fs, notch_freq, notch_q):
    """50Hz 노치필터 계수도 고정 파라미터에 대해 캐시하여 재사용한다."""
    return iirnotch(notch_freq, notch_q, fs)


def bandpass_filter(signal, fs, low=0.5, high=20.0, order=4, notch_freq=60.0, notch_q=30.0):
    """preprocess.py의 bandpass_filter와 동일한 대역통과 + 노치 체인(기본 0.5~20Hz, 60Hz 노치).
    학습(preprocess)과 추론(inference)의 필터가 반드시 일치해야 하므로 두 곳을 동일하게 유지한다.
    실제 값은 모델 번들의 filter 항목에서 넘어온다(기본값은 안전망일 뿐)."""
    b, a = _design_bandpass(fs, low, high, order)
    filtered = filtfilt(b, a, signal)
    if notch_freq and notch_freq < 0.5 * fs:
        bn, an = _design_notch(fs, notch_freq, notch_q)
        filtered = filtfilt(bn, an, filtered)
    return filtered


def signal_to_feature(signal, fs=SAMPLE_RATE_HZ, img_size=IMG_SIZE, feature_mode="pixel",
                      low=0.5, high=20.0, notch_freq=60.0, notch_q=30.0):
    """실시간 신호 버퍼 -> 필터링 -> 스펙트로그램 -> 학습 때와 동일한 특징 벡터로 변환.

    low/high/notch_freq/notch_q는 학습 때(preprocess.py) 쓴 필터 대역과 반드시 같아야 한다.
    RealtimeClassifier가 모델 번들의 filter 값을 그대로 넘겨 학습/추론 필터를 일치시킨다.

    filtered/Sxx_db 계산은 feature_mode와 무관하게 공유하고, 마지막 특징 변환
    단계만 분기한다 (preprocess.py와 정확히 같은 갈림길):
    - feature_mode="pixel"(기본): spectro_render.render_gray_array() — 컬러맵 룩업
      테이블 기반 grayscale flatten 벡터. matplotlib Figure 렌더링 없이 빠르다.
    - feature_mode="explicit": feature_extraction.extract_features() — 통계/주파수
      특징 14개."""
    filtered = bandpass_filter(signal, fs, low=low, high=high, notch_freq=notch_freq, notch_q=notch_q)
    # 스펙트로그램 계산은 preprocess.py와 같은 함수를 쓴다(두 곳이 갈라지면 학습/추론 불일치).
    Sxx_db, f, t = compute_spectrogram(filtered, fs, high=high)

    if feature_mode == "explicit":
        arr = extract_features(filtered, Sxx_db, f, t, fs=fs)
    else:
        arr = render_gray_array(Sxx_db, img_size=img_size)
    return arr, filtered, Sxx_db, f, t


def _fold_linear_head(model):
    """StandardScaler(+PCA)로 이어지는 앞단을 단일 아핀변환 y = x @ W + b 로 접는다.

    픽셀 방식 모델의 앞단은 50,176차원을 30차원으로 줄이는 선형 변환인데,
    sklearn 파이프라인은 이를 (표준화 → 평균 빼기 → 행렬곱) 세 단계로 나눠 수행하며
    매 단계 50,176개짜리 임시 배열을 만든다. 세 단계 모두 선형이므로 하나로 합칠 수 있다.

        z = (x - mean) / scale
        y = (z - pca_mean) @ components.T
          = x @ [(components / scale).T] - [(mean/scale + pca_mean) @ components.T]

    W를 float32 + 열 우선(F-contiguous)으로 저장하는 것이 핵심이다. (1,50176) @ (50176,30)
    에서는 출력 열마다 연속된 메모리를 훑게 되어 캐시 적중률이 크게 오른다 — 실측에서
    같은 연산이 행 우선 대비 10배 이상 빨랐다.

    접을 수 없는 모델(파이프라인이 아니거나 구성이 다름)이면 None을 돌려주고, 호출부는
    원래 경로를 그대로 쓴다."""
    steps = getattr(model, "named_steps", None)
    if not steps:
        return None
    scaler = steps.get("scaler")
    pca = steps.get("pca")
    if scaler is None or not hasattr(scaler, "scale_"):
        return None

    if pca is not None and hasattr(pca, "components_"):
        comp = pca.components_
        W = (comp / scaler.scale_).T
        b = -((scaler.mean_ / scaler.scale_) @ comp.T) - (pca.mean_ @ comp.T)
        tail = [st for name, st in model.steps if name not in ("scaler", "pca")]
    else:
        # PCA가 없는 구성(명시적 특징 14차원)은 표준화만 접는다.
        n = scaler.scale_.shape[0]
        W = np.diag(1.0 / scaler.scale_)
        b = -scaler.mean_ / scaler.scale_
        tail = [st for name, st in model.steps if name != "scaler"]

    if len(tail) != 1:
        return None
    # 열 우선 float32로 굳혀 둔다(위 주석 참고).
    return np.asfortranarray(W, dtype=np.float32), np.asarray(b, dtype=np.float32), tail[0]


class _FoldedModel:
    """접은 아핀변환 + 원래 분류기. predict/predict_proba 인터페이스는 그대로 유지한다."""

    __slots__ = ("W", "b", "clf", "classes_")

    def __init__(self, W, b, clf):
        self.W, self.b, self.clf = W, b, clf
        self.classes_ = getattr(clf, "classes_", None)

    def _head(self, X):
        X = np.asarray(X, dtype=np.float32)
        if X.ndim == 1:
            X = X[None, :]
        return X @ self.W + self.b

    def predict(self, X):
        return self.clf.predict(self._head(X))

    def predict_proba(self, X):
        return self.clf.predict_proba(self._head(X))


class RealtimeClassifier:
    """
    실시간 버퍼 관리 + 모델 추론을 담당하는 클래스.
    GUI 없이도(main.py --no-gui) 그대로 재사용 가능.
    """

    # 데모용 순환 모드(sim_state="순환")에서 순서대로 생성할 상태와 상태별 지속 시간(초)
    SIM_CYCLE_STATES = ["정상", "수분부족", "자극"]
    SIM_CYCLE_SEC = 8.0

    def __init__(self, model_path="../models/best_model.joblib",
                 sample_rate=SAMPLE_RATE_HZ, window_sec=None,
                 sim_source_csv=None, predict_hz=PREDICT_HZ,
                 sim_state="정상", smooth_sec=SMOOTH_SEC, smooth_window=None):
        self.model_path = model_path
        # window_sec=None이면 모델이 학습 때 쓴 창 길이를 그대로 따른다(권장).
        # 값을 직접 주면 그 값이 우선한다(창 길이를 실험할 때만 쓸 것).
        self._window_sec_override = None if window_sec is None else float(window_sec)
        self._install_bundle(joblib.load(model_path))

        self.sample_rate = sample_rate
        self._set_window(self._window_sec_override
                         if self._window_sec_override is not None else self.bundle_window_sec)

        # 버퍼에는 매 샘플(최대 sample_rate Hz)마다 값을 쌓지만, 특징추출+예측(스펙트로그램
        # 렌더링 포함)은 predict_hz 주기로만 수행해 CPU 부하를 실시간 가능한 수준으로 제한한다.
        self.predict_every = max(1, int(round(sample_rate / predict_hz))) if predict_hz else 1
        self._samples_since_predict = 0

        # 하드웨어도 CSV도 없을 때 라이브 시뮬레이션으로 생성할 상태.
        # "정상"/"수분부족"/"자극" 중 하나, 또는 "cycle"(SIM_CYCLE_SEC마다 순환)로 지정한다.
        # (예전에는 항상 "정상"만 생성해 하드웨어/CSV 없이는 스트레스 시연이 불가능했다.)
        self.sim_state = sim_state

        # 예측 스무딩: 인접 예측이 크게 겹치므로 최근 몇 개를 평균/다수결로 합쳐
        # 순간적인 오검출 깜빡임을 줄인다.
        #
        # 개수가 아니라 '몇 초를 평균할지'로 지정한다. 개수로 고정해 두면 predict_hz 를
        # 바꿨을 때 실제 평균 구간이 조용히 달라진다. 예전에는 5개로 고정돼 있었는데,
        # 5Hz 기준으로 1초에 해당한다.
        #
        # 주의: 창이 10초인데 1초를 평균하면, 그 예측들은 창의 90% 이상을 공유하므로
        # 사실상 거의 같은 입력을 평균하는 셈이라 잡음 억제 효과가 크지 않다. 실측
        # 데이터를 확보한 뒤 이 값을 창 길이의 1/4~1/2 수준으로 올려 보며 조정할 것.
        # 반대로 너무 키우면 순간 이벤트인 '자극' 반응이 뭉개질 수 있다.
        if smooth_window is None:
            smooth_window = round(float(smooth_sec) * (predict_hz or 1.0))
        self.smooth_window = max(1, int(smooth_window))
        self.smooth_sec = self.smooth_window / (predict_hz or 1.0)
        self._proba_history = deque(maxlen=self.smooth_window)
        self._pred_history = deque(maxlen=self.smooth_window)

        # 하드웨어가 없을 때, 저장된 실제 CSV를 재생하며 시뮬레이션하는 옵션
        self._sim_rows = None
        self._sim_idx = 0
        self._sim_csv_path = None
        if sim_source_csv and os.path.exists(sim_source_csv):
            import pandas as pd
            df = pd.read_csv(sim_source_csv)
            self._sim_rows = df["voltage"].to_numpy(dtype=float)
            self._sim_csv_path = sim_source_csv

        self._t0 = time.time()
        # 라이브 시뮬레이션의 신호 시간축은 벽시계가 아니라 "읽은 샘플 수 / 샘플레이트"로 센다.
        # 벽시계를 쓰면 루프가 명목 속도보다 느릴 때(실측 233Hz vs 명목 250Hz) 생성된 신호가
        # 250Hz로 해석되어 모든 주파수가 그 비율만큼 밀린다. 학습 데이터(CSV)는 t=i/fs로
        # 만들어졌으므로, 라이브도 같은 방식이어야 학습/추론이 일치한다.
        self._sim_n = 0
        self._paced_n = 0   # pace()가 지금까지 맞춘 샘플 수(목표 시각 계산용)

    def _set_window(self, window_sec):
        """분석 창 길이를 정하고 버퍼를 그 길이에 맞춘다. 이미 모아둔 샘플은 살린다."""
        self.window_sec = float(window_sec)
        self.window_len = max(1, int(round(self.window_sec * self.sample_rate)))
        old = getattr(self, "buffer", None)
        buf = deque(maxlen=self.window_len)
        if old:
            buf.extend(list(old)[-self.window_len:])
        self.buffer = buf

    def _install_bundle(self, bundle):
        """joblib 번들의 내용을 속성에 반영한다. 마지막에 _view를 통째로 갈아끼우는 이유는
        step()이 백그라운드 스레드에서 계속 도는 중에도 모델을 바꿀 수 있어야 하기 때문이다.
        step()은 맨 앞에서 _view를 지역변수로 한 번만 읽으므로, 모델과 특징 추출 방식이
        서로 다른 번들에서 섞이는 일이 없다(파이썬 속성 대입은 원자적)."""
        self.model = bundle["model"]
        self.classes = bundle["classes"]  # 예: ["정상","수분부족","자극"] 또는 2-class ["정상","자극"]
        self.model_name = bundle.get("name", "unknown")
        # 이 키가 없는 joblib은 모두 (이 기능이 생기기 전) pixel 방식으로 학습된 것이므로 "pixel"로 취급
        self.feature_mode = bundle.get("feature_mode", "pixel")
        self.img_size = bundle.get("img_size", IMG_SIZE)  # feature_mode=="explicit"일 때는 사용 안 함
        # 학습 때 쓴 필터 대역(없는 예전 번들은 기본 0.5~45Hz + 50Hz 노치). 추론도 동일 대역 사용.
        self.filter = bundle.get("filter", {"lowcut": 0.5, "highcut": 20.0, "notch_freq": 60.0, "notch_q": 30.0})
        # 학습 때 쓴 창 길이. 이 키가 없는 예전 번들은 2초 창으로 학습된 것이다.
        self.bundle_window_sec = float(bundle.get("window_sec", 2.0))
        if self._window_sec_override is None and hasattr(self, "sample_rate"):
            # 실행 중 모델 교체(reload_model)로 창 길이가 바뀌면 버퍼도 같이 맞춘다.
            self._set_window(self.bundle_window_sec)
        # 앞단 선형 변환을 미리 접어 둔다(실패하면 원래 모델을 그대로 쓴다).
        folded = None
        try:
            parts = _fold_linear_head(self.model)
            if parts is not None:
                folded = _FoldedModel(*parts)
        except Exception:
            folded = None
        self.predictor = folded if folded is not None else self.model
        self._view = (self.predictor, self.classes, self.feature_mode, self.img_size, self.filter)

    def reload_model(self, model_path=None):
        """저장된 모델 파일을 다시 읽어 실행 중에 교체한다(웹에서 재학습한 직후 등).
        클래스 구성이 달라질 수 있으므로 스무딩 이력은 비운다."""
        path = model_path or self.model_path
        bundle = joblib.load(path)
        self._proba_history.clear()
        self._pred_history.clear()
        self._install_bundle(bundle)
        self.model_path = path
        return {"name": self.model_name, "classes": list(self.classes),
                "feature_mode": self.feature_mode, "path": path}

    def pace(self):
        """다음 샘플 시각까지만 대기한다(누적 드리프트 보정).

        `time.sleep(1/fs)`를 그대로 쓰면 step()의 처리 시간이 매번 더해져 실제 샘플레이트가
        명목치보다 낮아진다(측정: 250Hz 명목 -> 233Hz 실측). 그러면 필터·스펙트로그램이
        가정하는 fs와 실제 취득 속도가 어긋나 주파수가 왜곡된다. 목표 시각 기준으로 자면
        처리 시간이 흔들려도 평균 샘플레이트가 명목치에 붙는다."""
        self._paced_n += 1
        target = self._t0 + self._paced_n / self.sample_rate
        remain = target - time.time()
        if remain > 0:
            time.sleep(remain)
        elif remain < -1.0:
            # 1초 이상 밀렸으면 따라잡기를 포기하고 기준 시각을 현재로 리셋한다
            # (일시적인 부하 뒤에 sleep 없는 폭주 루프가 되는 것을 막는다).
            self._t0 = time.time()
            self._paced_n = 0

    def actual_rate(self):
        """지금까지 실제로 읽은 평균 샘플레이트(Hz). 명목 sample_rate와 크게 다르면
        (예: 파이가 부하로 못 따라가면) 필터/스펙트로그램의 주파수축이 어긋난다."""
        el = time.time() - self._t0
        if el <= 0 or self._paced_n == 0:
            return None
        return round(self._paced_n / el, 1)

    def input_source(self):
        """현재 입력 신호의 출처를 (kind, 사람이 읽는 라벨)로 반환한다.
        우선순위: CSV 재생 > 하드웨어 > 시뮬레이션. GUI/웹에서 모드 표시에 사용."""
        if self._sim_rows is not None:
            return ("csv", f"CSV 재생 · {os.path.basename(self._sim_csv_path)}")
        if HARDWARE_AVAILABLE:
            return ("hardware", "하드웨어 (실제 센서)")
        label = "순환(정상→수분부족→자극)" if self.sim_state == "순환" else self.sim_state
        return ("sim", f"시뮬레이션 · {label}")

    def _current_sim_state(self, t):
        """라이브 시뮬레이션에서 지금 생성할 상태를 정한다. 순환 모드면 시간에 따라 순환."""
        if self.sim_state == "순환":
            idx = int(t / self.SIM_CYCLE_SEC) % len(self.SIM_CYCLE_STATES)
            return self.SIM_CYCLE_STATES[idx]
        return self.sim_state

    def next_sample(self):
        if self._sim_rows is not None:
            v = self._sim_rows[self._sim_idx % len(self._sim_rows)]
            self._sim_idx += 1
            return v
        # 벽시계가 아니라 샘플 인덱스로 시간축을 만든다(위 _sim_n 주석 참조).
        t = self._sim_n / self.sample_rate
        self._sim_n += 1
        return read_single_realtime(state_for_sim=self._current_sim_state(t), t=t)

    def step(self):
        """샘플 1개를 버퍼에 추가하고, 버퍼가 가득 차면서 predict_hz 주기가 된 경우에만
        (state, prob, filtered_signal)을 계산해 반환한다. 그 외에는 None을 반환한다."""
        v = self.next_sample()
        self.buffer.append(v)

        if len(self.buffer) < self.window_len:
            return None

        self._samples_since_predict += 1
        if self._samples_since_predict < self.predict_every:
            return None
        self._samples_since_predict = 0

        # 모델은 실행 중에 교체될 수 있으므로(reload_model) 한 번만 읽어 고정한다.
        model, classes, feature_mode, img_size, filt = self._view

        signal = np.array(self.buffer)
        feature, filtered, Sxx_db, f, t = signal_to_feature(
            signal, self.sample_rate, img_size=img_size, feature_mode=feature_mode,
            low=filt["lowcut"], high=filt["highcut"],
            notch_freq=filt["notch_freq"], notch_q=filt["notch_q"]
        )

        # 확률 벡터가 있으면 최근 창을 평균해 argmax로 스무딩(확률 평균), 없으면 예측 인덱스
        # 다수결로 스무딩한다. smooth_window=1이면 스무딩 없이 단일 예측과 동일하게 동작한다.
        try:
            proba_vec = model.predict_proba([feature])[0]
        except Exception:
            proba_vec = None

        if proba_vec is not None:
            # 모델이 바뀌어 클래스 수가 달라졌으면 이전 이력과 길이가 안 맞으므로 비운다.
            if self._proba_history and len(self._proba_history[0]) != len(proba_vec):
                self._proba_history.clear()
            self._proba_history.append(np.asarray(proba_vec, dtype=np.float64))
            mean_proba = np.mean(self._proba_history, axis=0)
            pred_idx = int(np.argmax(mean_proba))
            proba = float(mean_proba[pred_idx])
            probs = {cls: float(mean_proba[i]) for i, cls in enumerate(classes)}
        else:
            self._pred_history.append(int(model.predict([feature])[0]))
            vals, counts = np.unique(np.array(self._pred_history), return_counts=True)
            pred_idx = int(vals[np.argmax(counts)])
            proba = None
            probs = None
        state = classes[pred_idx]

        return {
            "state": state,
            "classes": list(classes),
            "emoji": STATE_EMOJI.get(state, "❓"),
            "proba": proba,
            "probs": probs,  # {상태: 확률} 전체 (웹 대시보드 등에서 사용). predict_proba 없으면 None
            "raw_signal": signal,
            "filtered_signal": filtered,
            "spectrogram_db": Sxx_db,
            "spec_f": f,
            "spec_t": t,
        }


def main():
    print(f"[inference] 하드웨어 감지: {HARDWARE_AVAILABLE}")
    clf = RealtimeClassifier(model_path="../models/best_model.joblib")
    print(f"[inference] 모델 로드 완료: {clf.model_name}, 클래스={clf.classes}")

    try:
        while True:
            result = clf.step()
            if result is not None:
                p = f"{result['proba']:.2f}" if result["proba"] is not None else "N/A"
                print(f"상태: {result['state']} {result['emoji']}  (확신도={p})")
            clf.pace()
    except KeyboardInterrupt:
        print("\n[inference] Ctrl+C 감지 - 안전 종료")


if __name__ == "__main__":
    main()
