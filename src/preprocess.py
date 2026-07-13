"""
preprocess.py
=============
data/raw/*.csv 의 원시 전위 신호를 다음과 같이 처리합니다.

1. SciPy Butterworth 대역통과필터(0.5Hz~45Hz)로 50Hz 전원 노이즈 및 DC 드리프트 제거
2. 필터링된 신호를 일정 길이의 윈도우로 나누어 스펙트로그램(224x224, viridis) 이미지 생성
3. data/spectrogram/정상/ , data/spectrogram/스트레스/ 폴더에 저장
   (요구사항: 정상 / 스트레스(수분부족+자극) 2개 클래스로 구분)

실행:
    python preprocess.py
    python preprocess.py --raw_dir ../data/raw --out_dir ../data/spectrogram
"""

import argparse
import os

import numpy as np
import pandas as pd
from scipy.signal import butter, filtfilt, spectrogram
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as _plt_font_setup
_plt_font_setup.rcParams["font.family"] = "Noto Sans CJK JP"
_plt_font_setup.rcParams["axes.unicode_minus"] = False  # 헤드리스 환경(서버/검증)에서도 이미지 저장 가능하도록
import matplotlib.pyplot as plt

# 원시 파일명 -> 최종 클래스 매핑 (정상 / 스트레스)
FILE_TO_CLASS = {
    "정상.csv": "정상",
    "수분부족.csv": "스트레스",
    "자극.csv": "스트레스",
}

SAMPLE_RATE_HZ = 250.0  # sensor_control.py 기본 샘플링과 일치
WINDOW_SEC = 2.0        # 스펙트로그램 1장을 만들 신호 구간 길이
STEP_SEC = 1.0          # 슬라이딩 윈도우 이동 간격 (오버랩으로 이미지 수 확보)
IMG_SIZE = 224


def bandpass_filter(signal, fs, low=0.5, high=45.0, order=4):
    """Butterworth 대역통과필터 (0.5Hz~45Hz) - 50Hz 전원 노이즈 및 저주파 드리프트 제거"""
    nyq = 0.5 * fs
    low_n = low / nyq
    high_n = high / nyq
    b, a = butter(order, [low_n, high_n], btype="band")
    return filtfilt(b, a, signal)


# 파일당 수십 장씩 생성되는 배치 작업이므로, 매 이미지마다 Figure/Axes를 새로 만들고
# 버리는 대신 하나만 만들어 재사용해 반복적인 matplotlib 객체 생성 비용을 줄인다.
_spec_fig = None
_spec_ax = None


def _get_spectrogram_axes(img_size, dpi):
    global _spec_fig, _spec_ax
    if _spec_fig is None:
        _spec_fig = plt.figure(figsize=(img_size / dpi, img_size / dpi), dpi=dpi)
        _spec_ax = plt.Axes(_spec_fig, [0.0, 0.0, 1.0, 1.0])
        _spec_ax.set_axis_off()
        _spec_fig.add_axes(_spec_ax)
    else:
        _spec_ax.cla()
        _spec_ax.set_axis_off()
    return _spec_fig, _spec_ax


def make_spectrogram_image(signal, fs, out_path, img_size=IMG_SIZE):
    """신호 구간 하나를 224x224 viridis 컬러맵 스펙트로그램 PNG로 저장"""
    f, t, Sxx = spectrogram(signal, fs=fs, nperseg=min(128, len(signal)), noverlap=64 if len(signal) > 128 else 0)
    Sxx_db = 10 * np.log10(Sxx + 1e-12)

    dpi = 100
    fig, ax = _get_spectrogram_axes(img_size, dpi)
    ax.pcolormesh(t, f, Sxx_db, shading="auto", cmap="viridis")
    fig.savefig(out_path, dpi=dpi)


def process_file(csv_path, out_root, fs=SAMPLE_RATE_HZ, window_sec=WINDOW_SEC, step_sec=STEP_SEC):
    fname = os.path.basename(csv_path)
    if fname not in FILE_TO_CLASS:
        print(f"  [건너뜀] 매핑되지 않은 파일: {fname}")
        return 0

    cls = FILE_TO_CLASS[fname]
    out_dir = os.path.join(out_root, cls)
    os.makedirs(out_dir, exist_ok=True)

    df = pd.read_csv(csv_path)
    raw_signal = df["voltage"].to_numpy(dtype=float)

    filtered = bandpass_filter(raw_signal, fs)

    win_len = int(window_sec * fs)
    step_len = int(step_sec * fs)

    count = 0
    base_name = os.path.splitext(fname)[0]
    for start in range(0, len(filtered) - win_len + 1, step_len):
        segment = filtered[start:start + win_len]
        img_name = f"{base_name}_{start:06d}.png"
        img_path = os.path.join(out_dir, img_name)
        make_spectrogram_image(segment, fs, img_path)
        count += 1

    print(f"  {fname} -> 클래스 '{cls}': {count}개 스펙트로그램 이미지 생성")
    return count


def main():
    parser = argparse.ArgumentParser(description="CSV 원시 신호 -> 대역통과필터 -> 스펙트로그램 이미지")
    parser.add_argument("--raw_dir", default="../data/raw")
    parser.add_argument("--out_dir", default="../data/spectrogram")
    args = parser.parse_args()

    print(f"[preprocess] 원시 데이터: {args.raw_dir}")
    print(f"[preprocess] 출력(스펙트로그램): {args.out_dir}")

    total = 0
    for fname in sorted(os.listdir(args.raw_dir)):
        if fname.endswith(".csv"):
            csv_path = os.path.join(args.raw_dir, fname)
            total += process_file(csv_path, args.out_dir)

    print(f"[preprocess] 총 {total}개 스펙트로그램 이미지 생성 완료")


if __name__ == "__main__":
    main()
