"""
feature_extraction.py
======================
스펙트로그램 이미지를 픽셀째로 flatten하는 대신, 필터링된 시간영역 신호와
스펙트로그램(Sxx_db) 배열에서 명시적인 통계/주파수 특징 14개를 추출한다.

preprocess.py(데이터셋 생성 시 data/features.csv에 저장)와 inference.py(실시간
추론)가 반드시 이 모듈의 extract_features()를 그대로 공유해야 학습 때 특징과
추론 때 특징이 일치한다 (spectro_render.py와 동일한 이유).

dB로 저장된 스펙트로그램을 선형 파워로 되돌리는 역변환(10**(Sxx_db/10))은
반드시 이 모듈 안에서만 수행한다 - 다른 곳에서 재구현하면 학습/추론 특징이
미묘하게 어긋날 수 있다.
"""

import numpy as np
from scipy.stats import kurtosis as _kurtosis, skew as _skew

FEATURE_NAMES = [
    "mean", "std", "rms", "skewness", "kurtosis",
    "zero_crossing_rate", "peak_to_peak",
    "total_energy", "band_power_low", "band_power_mid", "band_power_high",
    "spectral_centroid", "spectral_bandwidth", "peak_frequency",
]

# 대역통과필터 통과 대역(0.5~45Hz) 내에서 나눈 저/중/고 주파수 구간
BAND_LOW = (0.5, 5.0)
BAND_MID = (5.0, 20.0)
BAND_HIGH = (20.0, 45.0)


def extract_features(signal, Sxx_db, f, t, fs=None):
    """
    signal : 필터링된 시간영역 신호 구간(1차원 배열). preprocess.py의 segment,
             inference.py의 filtered와 동일한 것을 그대로 전달한다.
    Sxx_db, f, t : scipy.signal.spectrogram(signal, ...)의 (f, t, Sxx)를
             10*np.log10(Sxx+1e-12)한 결과. 두 호출부 모두 이미 계산해 갖고
             있는 값을 그대로 넘긴다 (스펙트로그램을 여기서 다시 계산하지 않음).
    fs : signal의 샘플링 레이트(Hz). zero_crossing_rate 정규화에 사용.

    반환: FEATURE_NAMES 순서의 float64 1차원 배열, shape (len(FEATURE_NAMES),).
    """
    signal = np.asarray(signal, dtype=np.float64)
    std = float(np.std(signal))

    # ---- 시간영역 ----
    mean = float(np.mean(signal))
    rms = float(np.sqrt(np.mean(signal ** 2)))
    # 표준편차가 0에 가까우면 skew/kurtosis가 정의되지 않으므로 0으로 처리
    skewness = float(_skew(signal)) if std > 1e-12 else 0.0
    kurt = float(_kurtosis(signal)) if std > 1e-12 else 0.0
    sign_changes = np.sum(np.diff(np.sign(signal)) != 0)
    duration_sec = len(signal) / fs if fs else len(signal)
    zero_crossing_rate = float(sign_changes / duration_sec) if duration_sec > 0 else 0.0
    peak_to_peak = float(np.max(signal) - np.min(signal))

    # ---- 주파수영역 (dB -> 선형 파워 역변환은 여기서만) ----
    Sxx = 10.0 ** (np.asarray(Sxx_db, dtype=np.float64) / 10.0)
    power_per_freq = Sxx.mean(axis=1)  # 시간축 평균 -> 주파수별 대표 파워
    total_power = float(power_per_freq.sum())

    def band_power(lo, hi):
        mask = (f >= lo) & (f < hi)
        return float(power_per_freq[mask].sum()) if np.any(mask) else 0.0

    total_energy = total_power
    band_power_low = band_power(*BAND_LOW)
    band_power_mid = band_power(*BAND_MID)
    band_power_high = band_power(*BAND_HIGH)

    if total_power > 1e-12:
        spectral_centroid = float(np.sum(f * power_per_freq) / total_power)
        spectral_bandwidth = float(
            np.sqrt(np.sum(((f - spectral_centroid) ** 2) * power_per_freq) / total_power)
        )
    else:
        spectral_centroid = 0.0
        spectral_bandwidth = 0.0
    peak_frequency = float(f[np.argmax(power_per_freq)]) if len(f) > 0 else 0.0

    return np.array([
        mean, std, rms, skewness, kurt,
        zero_crossing_rate, peak_to_peak,
        total_energy, band_power_low, band_power_mid, band_power_high,
        spectral_centroid, spectral_bandwidth, peak_frequency,
    ], dtype=np.float64)
