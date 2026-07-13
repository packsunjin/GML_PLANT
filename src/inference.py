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
from scipy.signal import butter, filtfilt, spectrogram

from sensor_control import read_single_realtime, HARDWARE_AVAILABLE
from spectro_render import render_gray_array

SAMPLE_RATE_HZ = 250.0
WINDOW_SEC = 2.0
IMG_SIZE = 224
PREDICT_HZ = 5.0  # 실시간 루프에서 무거운 특징추출+추론을 반복할 최대 빈도

STATE_EMOJI = {"정상": "🌱", "스트레스": "😵"}


@lru_cache(maxsize=8)
def _design_bandpass(fs, low, high, order):
    """Butterworth 계수는 (fs, low, high, order)가 고정이면 항상 동일하므로 캐시하여 재사용한다."""
    nyq = 0.5 * fs
    return butter(order, [low / nyq, high / nyq], btype="band")


def bandpass_filter(signal, fs, low=0.5, high=45.0, order=4):
    b, a = _design_bandpass(fs, low, high, order)
    return filtfilt(b, a, signal)


def signal_to_feature(signal, fs=SAMPLE_RATE_HZ, img_size=IMG_SIZE):
    """실시간 신호 버퍼 -> 필터링 -> 스펙트로그램 -> 학습 때와 동일한 grayscale flatten 벡터로 변환.

    이미지화는 matplotlib Figure 렌더링이 아니라 spectro_render.render_gray_array()
    (컬러맵 룩업 테이블 기반, preprocess.py와 공유)를 사용해 실시간 루프에서 가장 무거웠던
    단계를 제거했다."""
    filtered = bandpass_filter(signal, fs)
    f, t, Sxx = spectrogram(filtered, fs=fs, nperseg=min(128, len(filtered)),
                             noverlap=64 if len(filtered) > 128 else 0)
    Sxx_db = 10 * np.log10(Sxx + 1e-12)

    arr = render_gray_array(Sxx_db, img_size=img_size)
    return arr, filtered, Sxx_db, f, t


class RealtimeClassifier:
    """
    실시간 버퍼 관리 + 모델 추론을 담당하는 클래스.
    GUI 없이도(main.py --no-gui) 그대로 재사용 가능.
    """

    def __init__(self, model_path="../models/best_model.joblib",
                 sample_rate=SAMPLE_RATE_HZ, window_sec=WINDOW_SEC,
                 sim_source_csv=None, predict_hz=PREDICT_HZ):
        bundle = joblib.load(model_path)
        self.model = bundle["model"]
        self.classes = bundle["classes"]  # ["정상", "스트레스"]
        self.model_name = bundle.get("name", "unknown")

        self.sample_rate = sample_rate
        self.window_len = int(window_sec * sample_rate)
        self.buffer = deque(maxlen=self.window_len)

        # 버퍼에는 매 샘플(최대 sample_rate Hz)마다 값을 쌓지만, 특징추출+예측(스펙트로그램
        # 렌더링 포함)은 predict_hz 주기로만 수행해 CPU 부하를 실시간 가능한 수준으로 제한한다.
        self.predict_every = max(1, int(round(sample_rate / predict_hz))) if predict_hz else 1
        self._samples_since_predict = 0

        # 하드웨어가 없을 때, 저장된 실제 CSV를 재생하며 시뮬레이션하는 옵션
        self._sim_rows = None
        self._sim_idx = 0
        if sim_source_csv and os.path.exists(sim_source_csv):
            import pandas as pd
            df = pd.read_csv(sim_source_csv)
            self._sim_rows = df["voltage"].to_numpy(dtype=float)

        self._t0 = time.time()

    def next_sample(self):
        if self._sim_rows is not None:
            v = self._sim_rows[self._sim_idx % len(self._sim_rows)]
            self._sim_idx += 1
            return v
        t = time.time() - self._t0
        return read_single_realtime(state_for_sim="정상", t=t)

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

        signal = np.array(self.buffer)
        feature, filtered, Sxx_db, f, t = signal_to_feature(signal, self.sample_rate)
        pred_idx = self.model.predict([feature])[0]
        state = self.classes[pred_idx]

        try:
            proba = self.model.predict_proba([feature])[0][pred_idx]
        except Exception:
            proba = None

        return {
            "state": state,
            "emoji": STATE_EMOJI.get(state, "❓"),
            "proba": proba,
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
            time.sleep(1.0 / clf.sample_rate)
    except KeyboardInterrupt:
        print("\n[inference] Ctrl+C 감지 - 안전 종료")


if __name__ == "__main__":
    main()
