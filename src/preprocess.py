"""
preprocess.py
=============
data/raw/*.csv 의 원시 전위 신호를 다음과 같이 처리합니다.

1. SciPy Butterworth 대역통과필터(기본 0.5~20Hz) + 노치필터(기본 60Hz, 신호에서 자동 판별)로
   전원 노이즈 및 DC 드리프트 제거
2. 필터링된 신호를 일정 길이의 윈도우로 나누어 스펙트로그램(224x224, viridis) 이미지 생성
   + 동일한 윈도우에서 명시적 통계/주파수 특징 14개(feature_extraction.py) 추출
3. data/spectrogram/{정상,수분부족,자극}/ 폴더에 상태별로 이미지 저장
   (3가지 상태를 각각 별도 클래스로. 2-class 비교는 train.py에서 필요한 상태만 골라 수행)
4. data/features.csv 에 윈도우별 특징 벡터 저장 (train.py의 features/both 모드에서 사용)

data/raw/ 에 정상.csv, 수분부족.csv, 자극.csv 중 일부만 있어도(예: 하드웨어로 2가지
상태만 수집된 경우) 있는 파일만으로 진행하며, 어떤 상태가 빠졌는지 안내만 출력합니다.

실행:
    python preprocess.py
    python preprocess.py --raw_dir ../data/raw --out_dir ../data/spectrogram
"""

import argparse
import json
import os

import numpy as np
import pandas as pd
from scipy.signal import butter, filtfilt, iirnotch

from spectro_render import render_rgb_image, compute_spectrogram
from sensor_control import AD8232_HPF_HZ, AD8232_LPF_HZ
from feature_extraction import extract_features, FEATURE_NAMES

# 원시 파일명 -> 상태(클래스) 매핑. 3가지 상태를 각각 별도 클래스로 저장한다.
# 수분부족/자극을 "스트레스"로 합치지 않는다 - 3-class(정상/수분부족/자극) 분류가 기본이고,
# 2-class 비교(정상+수분부족 / 정상+자극)는 train.py에서 필요한 상태만 골라 수행한다.
FILE_TO_STATE = {
    "정상.csv": "정상",
    "수분부족.csv": "수분부족",
    "자극.csv": "자극",
}

# ---- 품질 기준 --------------------------------------------------------
# 전극이 잘 안 붙으면 AD8232 출력이 전원 레일에 붙어버린다(3.3V 계통 기준 3.2V 근처).
# 그 구간은 증폭기 포화지 식물 신호가 아니므로 학습에서 제외한다.
RAIL_HIGH_V = 3.0   # 이 위로 평균이 올라간 창은 버림
RAIL_LOW_V = 0.3    # 이 아래로 내려간 창도 버림
MIN_STD_V = 0.0005  # 필터 후 표준편차가 이보다 작으면 신호가 없다고 본다

# 위의 절대 문턱만으로는 포화를 못 잡는 경우가 있다. 실제로 이 프로젝트에서 수집한
# CSV 6개를 조사한 결과가 그랬다. 모든 파일의 최댓값이 2.57~2.60V, 최솟값이 -0.59~-0.61V로
# 일정했는데, 이는 이 보드의 실제 출력 한계다. 그런데 위 문턱은 3.0V/0.3V라서,
# 전 구간이 2.56~2.60V에 붙어 있던 파일(= 완전히 상단 포화)이 100% 통과해 버렸다.
#
# 그래서 문턱을 짐작하지 않고 신호에서 직접 찾는다. 증폭기가 포화하면 출력이 한 값에
# 머무르므로, 파일의 극값 근처에 샘플이 눈에 띄게 몰린다. 그 몰림이 보이면 그 극값을
# 레일로 인정하고, 창별로 레일에 붙은 샘플 비율을 재서 버린다.
# 포화되지 않은 파일은 극값을 한두 번만 스치므로 레일이 검출되지 않고, 따라서 아무것도
# 버리지 않는다(실측 6개 파일로 확인: 깨끗한 2개는 검출 0건, 포화된 2개는 전량 제외).
# AD8232의 fast-restore 는 전극 임피던스가 높으면 계속 재작동하면서 출력을 순간적으로
# 0V 근처까지 떨어뜨린다(실측: 168ms마다 약 20ms). 이건 식물 신호가 아니라 회로 동작이고,
# 창마다 여러 번씩 들어가 모든 창을 오염시키므로 창 단위로 거를 수가 없다.
# -> 그 구간만 찾아 양옆 값으로 이어 붙인다(심전도에서 페이스메이커 스파이크를 지우는 것과 같은 처리).
DROPOUT_FRAC = 0.5   # 창 중앙값의 이 비율보다 낮으면 강하로 본다
DROPOUT_MAX = 0.15   # 전체의 이 비율을 넘게 걸리면 신호 자체가 이상한 것이므로 손대지 않는다

# "자극"은 정상/수분부족과 달리 지속 상태가 아니라 순간 이벤트다(파일 상단 주석 참고).
# 그런데 5분을 수집해도 실제로 반응이 담긴 구간은 짧고, 나머지 대부분은 조용한 baseline이다.
# 파일 하나를 통째로 "자극"이라 라벨링하면 그 조용한 구간까지 전부 "자극"으로 잘못 학습되어,
# 모델이 사실상 "자극 = 아무 신호"라고 배우고 실사용에서 계속 자극만 예측하게 된다.
# -> 자극 파일에서는 그 파일 안에서 상대적으로 튀는 창만 남기고 나머지 조용한 창은 버린다.
#    정상/수분부족은 지속 상태라 이 필터를 적용하지 않는다.
# std가 아니라 peak-to-peak(최대-최소)으로 판단한다: 반응 스파이크는 보통 창 안에서
# 아주 짧게(수십 ms) 튀므로, std는 나머지 조용한 구간에 묻혀 거의 안 움직이지만 p2p는
# 그 짧은 스파이크 하나만으로도 확 뛴다.
EVENT_ONLY_STATE = "자극"
EVENT_P2P_MULT = 2.0
EVENT_MIN_WINDOWS = 10  # 이 미만이면 파일 내 편차가 별로 없다는 뜻이라 거르지 않는다


RAIL_BAND_V = 0.03       # 극값에서 이 안이면 '레일에 붙었다'로 본다
RAIL_PILE_FRAC = 0.005   # 극값 근처에 이 비율 이상 몰려 있으면 그 극값은 레일이다
RAIL_WIN_FRAC = 0.20     # 창의 이 비율 이상이 레일에 붙어 있으면 그 창은 버린다


def detect_rails(signal, band=RAIL_BAND_V, pile=RAIL_PILE_FRAC):
    """신호에서 증폭기 출력 한계(레일)를 찾아 (하단, 상단)으로 돌려준다.

    포화가 없으면 해당 쪽은 None이다. 보드마다 다른 레일 전압을 미리 몰라도 되고,
    시뮬레이션처럼 0V 중심인 신호에도 잘못 걸리지 않는다."""
    v = np.asarray(signal, dtype=float)
    if len(v) < 100:
        return None, None
    mx = float(v.max()); mn = float(v.min())
    hi = mx if float((v >= mx - band).mean()) >= pile else None
    lo = mn if float((v <= mn + band).mean()) >= pile else None
    return lo, hi


def rail_fraction(win, lo, hi, band=RAIL_BAND_V):
    """창에서 레일에 붙어 있는 샘플의 비율.

    상단·하단을 각각 세어 더하면, 신호가 레일 폭보다 좁은 띠에 갇혔을 때 같은 샘플을
    두 번 세어 100%를 넘어 버린다(실측에서 190%가 나왔다). 합집합으로 센다."""
    w = np.asarray(win, dtype=float)
    mask = np.zeros(len(w), dtype=bool)
    if hi is not None:
        mask |= (w >= hi - band)
    if lo is not None:
        mask |= (w <= lo + band)
    return float(mask.mean())


def repair_dropouts(raw_win):
    """fast-restore 로 인한 순간적인 0V 강하를 양옆 값으로 보간해 없앤다.
    (고친 샘플 수, 고친 신호)를 돌려준다. 고칠 게 없으면 원본을 그대로 준다."""
    med = float(np.median(raw_win))
    if med <= 0.3:                      # 0V 중심 신호(시뮬레이션)에는 적용하지 않는다
        return 0, raw_win
    bad = raw_win < med * DROPOUT_FRAC
    n_bad = int(bad.sum())
    if n_bad == 0 or n_bad > len(raw_win) * DROPOUT_MAX:
        return 0, raw_win
    fixed = raw_win.copy()
    good = np.where(~bad)[0]
    if len(good) < 2:
        return 0, raw_win
    fixed[bad] = np.interp(np.where(bad)[0], good, raw_win[good])
    return n_bad, fixed

SAMPLE_RATE_HZ = 250.0  # sensor_control.py 기본 샘플링과 일치

# 분석 창 길이와 대역은 "무엇을 잡으려는 신호인가"와 "이 하드웨어가 무엇을 통과시키는가"
# 두 가지가 겹치는 곳으로 정해야 한다.
#
#  · AD8232로 식물 신호를 분류한 선행연구의 창 길이: 3초(MIT 2025, 400Hz,
#    스펙트로그램+CNN) ~ 15초(NJAS 2025, AD8232+아두이노)
#  · AD8232가 실제로 통과시키는 대역: 0.5~40Hz (아날로그 필터가 회로에 박혀 있음)
#
# 창은 그 범위의 가운데인 10초로 잡았다. 대역 하한 0.5Hz는 회로가 정한 값이라
# 소프트웨어에서 더 낮춰 봐야 되살아나지 않고, 상한은 식물 신호가 거의 없는
# 20~40Hz 구간의 잡음을 빼려고 20Hz로 낮췄다.
# 심전도 기준(2초 창, 0.5~45Hz)을 그대로 쓰면 한 창 안에 반응이 다 들어오지 않는다.
WINDOW_SEC = 10.0       # 스펙트로그램 1장을 만들 신호 구간 길이 (선행연구 8~15초)
STEP_SEC = 2.0          # 슬라이딩 윈도우 이동 간격 (창의 1/5씩 겹쳐 표본 수 확보)
IMG_SIZE = 224


def bandpass_filter(signal, fs, low=0.5, high=20.0, order=4, notch_freq=60.0, notch_q=30.0):
    """Butterworth 대역통과필터(기본 0.5~20Hz)로 저주파 드리프트/고주파 잡음을 줄이고,
    이어서 IIR 노치필터로 전원 노이즈를 추가로 감쇠시킨다.

    상한이 20Hz인 이유: 식물 전기신호를 다룬 선행연구들이 신호를 0.1~20Hz로 제한한다.
    그 위에는 식물 신호가 사실상 없고 잡음만 들어온다.
    노치 기본값이 60Hz인 이유: 한국 상용 전원이 60Hz다(유럽·일본 동부는 50Hz).
    상한 20Hz면 60Hz는 통과대역에서 한참 벗어나지만, 대역통과만으로 완전히 0이 되지는
    않으므로 노치를 함께 건다. 실제 값은 process_file()이 신호에서 재서 자동 보정한다.
    (inference.py의 bandpass_filter와 동일한 필터 체인을 사용해야 학습/추론이 일치한다)"""
    nyq = 0.5 * fs
    b, a = butter(order, [low / nyq, high / nyq], btype="band")
    filtered = filtfilt(b, a, signal)
    if notch_freq and notch_freq < nyq:
        bn, an = iirnotch(notch_freq, notch_q, fs)
        filtered = filtfilt(bn, an, filtered)
    return filtered


def make_spectrogram_image(Sxx_db, out_path, img_size=IMG_SIZE):
    """이미 계산된 Sxx_db 배열을 224x224 viridis 컬러맵 스펙트로그램 PNG로 저장.

    matplotlib Figure 렌더링 대신 컬러맵 룩업 테이블 기반 render_rgb_image()를 사용한다
    (inference.py의 signal_to_feature()가 쓰는 render_gray_array()와 같은 색상 로직을
    공유하는 spectro_render 모듈이라 학습/추론 특징이 일치한다)."""
    img = render_rgb_image(Sxx_db, img_size=img_size)
    img.save(out_path)


def process_file(csv_path, out_root, fs=SAMPLE_RATE_HZ, window_sec=WINDOW_SEC, step_sec=STEP_SEC,
                 low=0.5, high=20.0, notch_freq=60.0,
                 quality=True, rail_high=RAIL_HIGH_V, rail_low=RAIL_LOW_V, min_std=MIN_STD_V,
                 rail_frac=RAIL_WIN_FRAC,
                 repair=True):
    """CSV 하나를 윈도우 단위로 나눠 스펙트로그램 PNG를 저장하고,
    (이미지 수, 특징 행 리스트, 실제로 사용한 노치 주파수)를 반환한다.
    노치는 신호에서 50/60Hz를 재서 자동 보정하므로 파일마다 다를 수 있고,
    추론이 같은 필터를 쓰도록 이 값을 메타에 기록해야 한다.
    low/high/notch_freq로 대역통과·노치 필터 대역을 조절한다(느린 식물 신호 보존 시 low를 낮춤)."""
    fname = os.path.basename(csv_path)
    if fname not in FILE_TO_STATE:
        print(f"  [건너뜀] 매핑되지 않은 파일: {fname}")
        return 0, [], notch_freq

    cls = FILE_TO_STATE[fname]
    out_dir = os.path.join(out_root, cls)
    os.makedirs(out_dir, exist_ok=True)

    # 이전에 만든 이미지를 먼저 지운다. 새로 수집한 CSV가 더 짧으면 예전 이미지가
    # 남아 학습 데이터에 섞이기 때문이다(파일명이 시작 인덱스라 덮어쓰기가 안 됨).
    stale = [n for n in os.listdir(out_dir) if n.lower().endswith(".png")]
    for n in stale:
        os.remove(os.path.join(out_dir, n))
    if stale:
        print(f"  이전 이미지 {len(stale)}장 정리")

    df = pd.read_csv(csv_path)
    raw_signal = df["voltage"].to_numpy(dtype=float)

    # 샘플레이트는 가정하지 않고 CSV의 timestamp 에서 실제로 계산한다.
    # 하드웨어가 목표 속도를 못 따라가면(ADS1115 가 느릴 때) 실제 250Hz가 아닌데,
    # 250Hz로 가정하면 필터 대역과 스펙트로그램 주파수축이 통째로 어긋난다.
    if "timestamp_sec" in df.columns and len(df) > 10:
        dt = float(np.median(np.diff(df["timestamp_sec"].to_numpy(dtype=float))))
        if dt > 0:
            # 부동소수 오차로 250이 249.9999... 로 나오면 창 길이가 1샘플 밀린다.
            # 소수 둘째 자리로 정리하고, 정수에 아주 가까우면 정수로 스냅한다.
            measured = round(1.0 / dt, 2)
            if abs(measured - round(measured)) < 0.05:
                measured = float(round(measured))
            if abs(measured - fs) / fs > 0.05:
                print(f"  [샘플레이트] CSV 실측 {measured:.1f}Hz (기본 {fs:.0f}Hz와 다름) -> 실측값 사용")
            fs = measured

    # 전원 노이즈 주파수를 신호에서 직접 찾는다. 한국은 60Hz, 유럽/시뮬레이션은 50Hz라
    # 고정값을 쓰면 한쪽이 항상 틀린다. 두 후보의 파워를 재서 센 쪽을 노치한다.
    if notch_freq and len(raw_signal) > int(fs) * 2:
        from scipy.signal import welch as _welch
        _f, _P = _welch(raw_signal - raw_signal.mean(), fs=fs, nperseg=min(4096, len(raw_signal)))
        def _at(hz):
            m = (_f > hz - 1.5) & (_f < hz + 1.5)
            return float(_P[m].sum()) if m.any() else 0.0
        p50, p60 = _at(50.0), _at(60.0)
        picked = 60.0 if p60 > p50 * 1.5 else (50.0 if p50 > p60 * 1.5 else notch_freq)
        if picked != notch_freq:
            tot = float(_P.sum()) or 1.0
            print(f"  [전원 노이즈] 50Hz {100*p50/tot:.1f}% / 60Hz {100*p60/tot:.1f}% "
                  f"-> 노치를 {picked:.0f}Hz 로 맞춤")
            notch_freq = picked

    # 레일 포화 검사는 단전원(0~3.3V) 하드웨어 신호에만 의미가 있다. 시뮬레이션 CSV는
    # 0V를 중심으로 흔들리므로 그대로 적용하면 전부 "바닥 레일"로 오판한다.
    # 파일의 중앙값이 0.5V보다 위면 하드웨어 신호로 보고 레일 검사를 켠다.
    hw_like = float(np.median(raw_signal)) > 0.5
    rail_check = quality and hw_like

    rail_lo = rail_hi = None
    if rail_check:
        rail_lo, rail_hi = detect_rails(raw_signal)
        if rail_lo is not None or rail_hi is not None:
            at = 100.0 * rail_fraction(raw_signal, rail_lo, rail_hi)
            print(f"    [레일 검출] 하단 {('%.2fV' % rail_lo) if rail_lo is not None else '—'} / "
                  f"상단 {('%.2fV' % rail_hi) if rail_hi is not None else '—'} "
                  f"— 전체 샘플의 {at:.1f}%가 레일에 붙어 있습니다.")

    win_len = int(round(window_sec * fs))
    step_len = int(round(step_sec * fs))

    count = 0
    skipped_rail = 0
    skipped_clip = 0
    skipped_flat = 0
    skipped_quiet = 0
    repaired_samples = 0
    repaired_windows = 0
    feature_rows = []
    base_name = os.path.splitext(fname)[0]
    # 필터는 각 윈도우를 개별적으로 적용한다. 실시간 추론(inference.py)은 스트리밍으로 들어온
    # 버퍼 하나만 필터링할 수 있으므로, 학습 특징도 반드시 "윈도우 단위 필터링"으로 만들어야
    # 필터 과도응답까지 포함해 학습/추론 특징이 일치한다. (전체 신호를 한 번에 필터링한 뒤
    # 잘라내면 윈도우 경계의 과도응답이 서로 달라 train/serve skew가 생긴다 — 특히 통계/주파수
    # 특징 14개는 이 차이에 민감하다.)
    # 1차 패스: 레일/보정/필터까지 끝낸 창과 그 std를 모아 둔다. '자극'의 이벤트 문턱값은
    # 이 파일 안의 std 분포를 봐야 정할 수 있어서(파일마다 baseline이 다름) 미리 다 훑는다.
    prepared = []
    for start in range(0, len(raw_signal) - win_len + 1, step_len):
        raw_win = raw_signal[start:start + win_len]

        # ---- 품질 검사 ----------------------------------------------------
        # 전극 접촉이 나쁘면 AD8232 출력이 전원 레일에 붙는다(3.3V 계통에서 3.2V 근처).
        # 그런 구간은 식물 신호가 아니라 증폭기 포화라서 학습에 넣으면 안 된다.
        # 스펙트로그램은 장마다 정규화되므로 이미지만 봐서는 구분이 안 된다 -> 여기서 거른다.
        if rail_check:
            m = float(np.mean(raw_win))
            if m > rail_high or m < rail_low:
                skipped_rail += 1
                continue
            # 평균이 정상 범위여도 파형이 레일에 붙어 있을 수 있다(위 RAIL_* 주석 참고).
            if (rail_lo is not None or rail_hi is not None) and \
                    rail_fraction(raw_win, rail_lo, rail_hi) > rail_frac:
                skipped_clip += 1
                continue

        # fast-restore 강하를 먼저 지우고 필터를 건다(필터 전에 지워야 링잉이 안 남는다).
        if repair and hw_like:
            n_fix, raw_win = repair_dropouts(raw_win)
            if n_fix:
                repaired_samples += n_fix
                repaired_windows += 1

        segment = bandpass_filter(raw_win, fs, low=low, high=high, notch_freq=notch_freq)
        std = float(np.std(segment))

        if quality and std < min_std:
            # 필터 후에도 거의 평평하면 신호가 없는 것(선 빠짐/전원만 잡힘).
            skipped_flat += 1
            continue

        p2p = float(segment.max() - segment.min())
        prepared.append((start, segment, p2p))

    # 2차 패스: '자극' 파일은 이 파일 안에서 상대적으로 튀는 창만 남긴다(위 EVENT_ONLY_STATE 설명).
    event_thresh = None
    if quality and cls == EVENT_ONLY_STATE and len(prepared) >= EVENT_MIN_WINDOWS:
        p2ps = np.array([p[2] for p in prepared])
        baseline = float(np.median(p2ps))
        thresh = baseline * EVENT_P2P_MULT
        if np.any(p2ps > thresh):   # 전부 baseline 근처면(계속 자극 중) 거르지 않는다
            event_thresh = thresh

    for start, segment, p2p in prepared:
        if event_thresh is not None and p2p <= event_thresh:
            skipped_quiet += 1
            continue

        # inference.py와 같은 함수(spectro_render.compute_spectrogram)를 쓴다.
        Sxx_db, f, t = compute_spectrogram(segment, fs, high=high)

        img_name = f"{base_name}_{start:06d}.png"
        img_path = os.path.join(out_dir, img_name)
        make_spectrogram_image(Sxx_db, img_path)

        feats = extract_features(segment, Sxx_db, f, t, fs=fs)
        feature_rows.append([img_name, fname, cls] + feats.tolist())

        count += 1

    if event_thresh is not None:
        print(f"    [이벤트 선별] '{cls}'는 순간 이벤트라 조용한 창은 뺌 — "
              f"{skipped_quiet}창 제외, {count}창만 사용 (문턱값 p2p>{event_thresh:.4f}V)")
    if repaired_windows:
        print(f"    [보정] fast-restore 강하 제거 — {repaired_windows}창에서 {repaired_samples}샘플 보간")
    if quality and not hw_like:
        print("    [품질] 0V 중심 신호라 레일 포화 검사는 건너뜁니다(시뮬레이션 데이터).")
    total_win = count + skipped_rail + skipped_clip + skipped_flat + skipped_quiet
    print(f"  {fname} -> 클래스 '{cls}': {count}개 스펙트로그램 이미지 생성"
          + (f"  (전체 {total_win}창 중 {100.0*count/total_win:.0f}% 사용)" if total_win else ""))
    if skipped_rail or skipped_flat or skipped_clip:
        print(f"    [품질] 건너뜀 — 전원 레일 포화 {skipped_rail}창"
              f"{f', 파형 잘림 {skipped_clip}창' if skipped_clip else ''}"
              f"{f', 신호 없음 {skipped_flat}창' if skipped_flat else ''}")
        if count == 0:
            print("    ⚠️  쓸 수 있는 구간이 하나도 없습니다. 전극 접촉을 확인하고 다시 수집하세요.")
        elif count < 10:
            print(f"    ⚠️  쓸 수 있는 창이 {count}개뿐입니다. 더 길게 수집하는 것이 좋습니다.")
    if event_thresh is not None and count < EVENT_MIN_WINDOWS:
        print(f"    ⚠️  이벤트 창이 {count}개뿐입니다. 수집할 때 더 자주(10~15초 간격) "
              "잎을 건드려 반응 구간을 늘려주세요.")
    return count, feature_rows, notch_freq


def main():
    parser = argparse.ArgumentParser(description="CSV 원시 신호 -> 대역통과필터 -> 스펙트로그램 이미지 + 특징")
    parser.add_argument("--raw_dir", default="../data/raw")
    parser.add_argument("--out_dir", default="../data/spectrogram")
    parser.add_argument("--features_csv", default="../data/features.csv")
    parser.add_argument("--lowcut", type=float, default=0.5,
                        help="대역통과 하한(Hz). AD8232는 0.5Hz 아래를 회로에서 이미 잘라내므로, "
                             "이 값을 더 낮춰도 하드웨어로 받은 신호에서는 아무것도 되살아나지 않습니다")
    parser.add_argument("--highcut", type=float, default=20.0,
                        help="대역통과 상한(Hz). 기본 20Hz — 식물 신호는 이 위에 거의 없다")
    parser.add_argument("--notch", type=float, default=60.0,
                        help="노치 주파수(Hz). 기본 60Hz — 한국 상용 전원 주파수. 0이면 노치 끔. "
                             "신호에서 50/60Hz를 직접 재서 센 쪽으로 자동 보정하므로 "
                             "보통 그대로 두면 됩니다")
    parser.add_argument("--no-repair", action="store_true",
                        help="fast-restore 강하 보간을 끄고 원신호 그대로 사용한다")
    parser.add_argument("--no-quality", action="store_true",
                        help="품질 검사를 끄고 모든 창을 그대로 변환한다(포화 구간 포함)")
    parser.add_argument("--rail-high", type=float, default=RAIL_HIGH_V,
                        help=f"평균이 이 값보다 높은 창은 포화로 보고 버림(기본 {RAIL_HIGH_V}V)")
    parser.add_argument("--rail-frac", type=float, default=RAIL_WIN_FRAC,
                        help=f"창의 이 비율 이상이 증폭기 출력 한계에 붙어 있으면 버림(기본 {RAIL_WIN_FRAC})")
    parser.add_argument("--min-std", type=float, default=MIN_STD_V,
                        help=f"필터 후 표준편차가 이보다 작으면 신호 없음으로 보고 버림(기본 {MIN_STD_V}V)")
    parser.add_argument("--window_sec", type=float, default=WINDOW_SEC,
                        help="분석 창 길이(초). 학습·추론이 같은 값을 써야 한다")
    parser.add_argument("--step_sec", type=float, default=STEP_SEC,
                        help="창 이동 간격(초)")
    parser.add_argument("--only", default=None, choices=sorted(set(FILE_TO_STATE.values())),
                        help="이 상태 하나만 변환한다. 나머지 상태의 결과는 그대로 둔다.")
    args = parser.parse_args()

    print(f"[preprocess] 원시 데이터: {args.raw_dir}")
    print(f"[preprocess] 출력(스펙트로그램): {args.out_dir}")
    print(f"[preprocess] 창 {args.window_sec}초 / 스텝 {args.step_sec}초")
    # AD8232의 아날로그 대역(0.5~40Hz) 밖을 지정하면 소프트웨어 필터는 헛돈다.
    if args.lowcut < AD8232_HPF_HZ:
        print(f"[preprocess] ⚠️  하한 {args.lowcut}Hz 는 AD8232의 고역통과({AD8232_HPF_HZ}Hz)보다 낮습니다.")
        print("    회로에서 이미 지워진 대역이라 하드웨어 데이터에서는 효과가 없습니다"
              " (시뮬레이션 데이터에는 적용됩니다).")
    if args.highcut > AD8232_LPF_HZ:
        print(f"[preprocess] ⚠️  상한 {args.highcut}Hz 는 AD8232의 저역통과({AD8232_LPF_HZ}Hz)보다 높습니다.")
        print("    그 위에는 회로가 통과시킨 신호가 없습니다.")
    print(f"[preprocess] 필터: 대역통과 {args.lowcut}~{args.highcut}Hz"
          + (f" + 노치 {args.notch}Hz" if args.notch else " (노치 없음)"))
    if args.only:
        print(f"[preprocess] 대상: '{args.only}' 하나만 변환")

    existing = set(os.listdir(args.raw_dir)) if os.path.isdir(args.raw_dir) else set()
    if args.only:
        # 한 상태만 돌릴 때는 그 파일만 본다.
        targets = {f for f in existing if FILE_TO_STATE.get(f) == args.only}
        if not targets:
            print(f"❌ '{args.only}' 의 원시 CSV가 없습니다. 먼저 수집하세요.")
            return
    else:
        targets = existing
        missing_states = [fname.replace(".csv", "") for fname in FILE_TO_STATE if fname not in existing]
        if missing_states:
            print(f"⚠️  다음 상태가 수집되지 않았습니다: {', '.join(missing_states)}")
            collected = [fname.replace(".csv", "") for fname in FILE_TO_STATE if fname in existing]
            print(f"   ({' + '.join(collected) if collected else '수집된 상태 없음'} 데이터만으로 진행합니다)")

    total = 0
    all_feature_rows = []
    used_notches = []
    for fname in sorted(targets):
        if fname.endswith(".csv"):
            csv_path = os.path.join(args.raw_dir, fname)
            count, feature_rows, used_notch = process_file(csv_path, args.out_dir,
                                               window_sec=args.window_sec, step_sec=args.step_sec,
                                               low=args.lowcut, high=args.highcut, notch_freq=args.notch,
                                               quality=not args.no_quality,
                                               rail_high=args.rail_high, min_std=args.min_std, rail_frac=args.rail_frac,
                                               repair=not args.no_repair)
            total += count
            all_feature_rows.extend(feature_rows)
            if count:
                used_notches.append(used_notch)

    # 실제로 사용한 노치 주파수를 메타에 남긴다(자동 보정된 값). 파일마다 다르면
    # 가장 많이 쓰인 값을 쓰고 경고한다 - 서로 다른 전원 환경의 데이터가 섞였다는 뜻이다.
    meta_notch = args.notch
    if used_notches:
        meta_notch = max(set(used_notches), key=used_notches.count)
        if len(set(used_notches)) > 1:
            print(f"⚠️  파일마다 전원 주파수가 다릅니다: {sorted(set(used_notches))}")
            print(f"   -> 메타에는 {meta_notch:.0f}Hz 를 기록합니다. 같은 환경에서 다시 수집하는 것이 좋습니다.")
        elif meta_notch != args.notch:
            print(f"[preprocess] 노치 주파수 {meta_notch:.0f}Hz 를 메타에 기록합니다(자동 감지).")

    # 사용한 필터 대역을 사이드카로 기록 -> train.py가 읽어 모델 번들에 저장 -> inference가 동일 필터 사용.
    meta_path = os.path.join(os.path.dirname(args.features_csv) or ".", "preprocess_meta.json")

    # --only 로 한 상태만 다시 변환하면, 다른 상태의 스펙트로그램은 이전 설정으로 만들어진
    # 채로 남는다. 그런데 메타는 이번 실행 값으로 덮어써지므로, 학습·추론은 그 값을 쓴다.
    # 즉 데이터셋 안에 서로 다른 필터로 만든 이미지가 섞이는데 아무도 모르는 상태가 된다.
    # -> 상태별로 실제 사용한 설정을 누적해 두고, 서로 다르면 크게 경고한다.
    prev = {}
    if os.path.exists(meta_path):
        try:
            with open(meta_path, encoding="utf-8") as mf:
                prev = json.load(mf)
        except Exception:
            prev = {}

    by_state = dict(prev.get("by_state") or {})
    this_run = {"lowcut": args.lowcut, "highcut": args.highcut, "notch_freq": meta_notch,
                "window_sec": args.window_sec, "step_sec": args.step_sec}
    for fname in sorted(targets):
        st = FILE_TO_STATE.get(fname)
        if st:
            by_state[st] = dict(this_run)

    # 상태마다 설정이 다르면 데이터셋이 섞인 것이다.
    distinct = {json.dumps(v, sort_keys=True, ensure_ascii=False) for v in by_state.values()}
    if len(distinct) > 1:
        print("⚠️  상태마다 다른 설정으로 변환된 스펙트로그램이 섞여 있습니다.")
        for st, v in sorted(by_state.items()):
            print(f"     {st}: 대역 {v['lowcut']}~{v['highcut']}Hz, 노치 {v['notch_freq']:.0f}Hz, "
                  f"창 {v['window_sec']}초/{v['step_sec']}초")
        print("     이 상태로 학습하면 모델이 상태 차이가 아니라 '전처리 설정 차이'를 배울 수 있습니다.")
        print("     전체를 다시 변환하세요:  python preprocess.py   (--only 없이)")

    with open(meta_path, "w", encoding="utf-8") as mf:
        json.dump({"lowcut": args.lowcut, "highcut": args.highcut,
                   "notch_freq": meta_notch, "notch_q": 30.0,
                   "sample_rate": SAMPLE_RATE_HZ,
                   "window_sec": args.window_sec, "step_sec": args.step_sec,
                   "by_state": by_state}, mf, ensure_ascii=False)
    print(f"[preprocess] 필터 메타 저장 -> {meta_path}")

    print(f"[preprocess] 총 {total}개 스펙트로그램 이미지 생성 완료")

    if all_feature_rows:
        columns = ["img_name", "source_file", "label"] + FEATURE_NAMES
        feat_df = pd.DataFrame(all_feature_rows, columns=columns)

        # --only 로 한 상태만 돌렸으면 features.csv를 통째로 덮어쓰면 안 된다.
        # 그 상태의 예전 행만 걷어내고 나머지는 그대로 둔 채 합친다.
        if args.only and os.path.isfile(args.features_csv):
            try:
                old = pd.read_csv(args.features_csv)
                kept = old[old["label"] != args.only]
                feat_df = pd.concat([kept, feat_df], ignore_index=True)
                print(f"[preprocess] 기존 특징 {len(kept)}행 유지 + '{args.only}' 새로 {len(all_feature_rows)}행")
            except Exception as e:
                print(f"⚠️  기존 features.csv를 합치지 못해 새로 만듭니다: {e}")

        feat_df.to_csv(args.features_csv, index=False, encoding="utf-8")
        counts = feat_df["label"].value_counts().to_dict()
        print(f"[preprocess] 명시적 특징 {len(feat_df)}개 행 -> {args.features_csv}")
        print(f"[preprocess] 클래스별 개수: {counts}")


if __name__ == "__main__":
    main()
