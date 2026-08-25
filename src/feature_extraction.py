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
    # ── 느린 대역(DC~0.1Hz) ───────────────────────────────────────────
    # 위 14개는 전부 대역통과 필터를 거친 신호에서 뽑는다. 그런데 수분 스트레스는
    # 수 시간 규모(10⁻⁴ Hz)라 필터가 지우고, 남아 있더라도 10초 창의 최저 주파수가
    # 0.1Hz 라 스펙트로그램에 나타날 자리가 없다.
    # 그래서 **필터 전 원신호**에서 세 가지를 따로 잰다. DC 결합 앞단에서만
    # 의미가 있고(AD8232 는 회로가 이미 지웠다), 그 경우 0 근처로 나온다.
    "dc_level", "dc_slope", "dc_drift",
]

# 저/중/고 대역은 실제 분석 대역 안에서 나눠야 한다.
# 예전에는 (0.5~5, 5~20, 20~45)로 고정해 두었는데, 분석 대역 상한을 45Hz에서 20Hz로
# 낮추자 20~45Hz 구간에 아무것도 남지 않아 band_power_high 가 항상 0이 되었다.
# 특징 14개 중 하나가 상수가 되면 그만큼 정보가 줄어든다.
# -> 고정하지 않고 스펙트로그램의 주파수축에서 직접 만든다. 대역을 또 바꿔도 따라온다.
#
# 3등분은 로그 축으로 한다. 식물 신호의 에너지는 낮은 쪽에 몰려 있어서, 선형으로
# 나누면 저역 한 칸에 대부분이 들어가고 나머지 두 칸이 거의 비기 때문이다.
BAND_LOW_HZ = 0.5   # 분석 대역 하한(AD8232의 고역통과와 같다)


def frequency_bands(f, low=BAND_LOW_HZ):
    """주파수축 f 에서 저/중/고 대역 경계를 만든다. (저, 중, 고) 각각 (lo, hi) 튜플."""
    hi = float(np.max(f))
    if hi <= low:
        return (low, low), (low, low), (low, low)
    r = hi / low
    a = low * r ** (1.0 / 3.0)
    b = low * r ** (2.0 / 3.0)
    return (low, a), (a, b), (b, hi + 1e-9)


def slow_features(raw_window, fs, baseline=None):
    """필터 전 원신호에서 느린 성분 3개를 잰다.

    raw_window : 대역통과를 **거치지 않은** 창 (V)
    baseline   : 파일 앞부분의 기준 전위(V). 주면 그 대비 변화량을 함께 낸다.

    · dc_level — 이 창의 평균 전위. 수분 스트레스는 기준점 자체를 밀어 올린다.
    · dc_slope — 창 안에서의 기울기(분당 변화). 진행 중인 드리프트를 잡는다.
    · dc_drift — 측정 시작 시점 대비 변화량. 가장 직접적인 지표다.
    """
    v = np.asarray(raw_window, dtype=float)
    if v.size < 2:
        return np.zeros(3, dtype=np.float64)
    level = float(np.mean(v))
    # 최소제곱 직선의 기울기 -> 분당 변화로 환산
    x = np.arange(v.size, dtype=float) / float(fs or 1.0)
    slope = float(np.polyfit(x, v, 1)[0]) * 60.0
    drift = level - float(baseline) if baseline is not None else 0.0
    return np.array([level, slope, drift], dtype=np.float64)


def extract_features(signal, Sxx_db, f, t, fs=None, raw_window=None, baseline=None):
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
    # 정확히 0인 샘플은 np.sign이 0이 되어 한 번의 교차를 두 번으로 세므로,
    # 0이 아닌 샘플만 남겨 부호 전환 횟수를 센다.
    nonzero = signal[signal != 0.0]
    sign_changes = int(np.sum(np.diff(np.signbit(nonzero)) != 0)) if nonzero.size > 1 else 0
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
    b_low, b_mid, b_high = frequency_bands(f)
    band_power_low = band_power(*b_low)
    band_power_mid = band_power(*b_mid)
    band_power_high = band_power(*b_high)

    if total_power > 1e-12:
        spectral_centroid = float(np.sum(f * power_per_freq) / total_power)
        spectral_bandwidth = float(
            np.sqrt(np.sum(((f - spectral_centroid) ** 2) * power_per_freq) / total_power)
        )
    else:
        spectral_centroid = 0.0
        spectral_bandwidth = 0.0
    peak_frequency = float(f[np.argmax(power_per_freq)]) if len(f) > 0 else 0.0

    fast = np.array([
        mean, std, rms, skewness, kurt,
        zero_crossing_rate, peak_to_peak,
        total_energy, band_power_low, band_power_mid, band_power_high,
        spectral_centroid, spectral_bandwidth, peak_frequency,
    ], dtype=np.float64)

    # 느린 성분은 필터 전 원신호에서만 볼 수 있다. 주지 않으면 0 으로 채워
    # 특징 개수는 항상 같게 유지한다(모델 입력 차원이 흔들리면 안 된다).
    slow = (slow_features(raw_window, fs or 1.0, baseline)
            if raw_window is not None else np.zeros(3, dtype=np.float64))
    return np.concatenate([fast, slow])
