"""
preprocess.py
=============
data/raw/*.csv 의 원시 전위 신호를 다음과 같이 처리합니다.

1. SciPy Butterworth 대역통과필터(0.5Hz~45Hz)로 50Hz 전원 노이즈 및 DC 드리프트 제거
2. 필터링된 신호를 일정 길이의 윈도우로 나누어 스펙트로그램(224x224, viridis) 이미지 생성
   + 동일한 윈도우에서 명시적 통계/주파수 특징 14개(feature_extraction.py) 추출
3. data/spectrogram/정상/ , data/spectrogram/스트레스/ 폴더에 이미지 저장
   (요구사항: 정상 / 스트레스(수분부족+자극) 2개 클래스로 구분)
4. data/features.csv 에 윈도우별 특징 벡터 저장 (train.py --mode features/both에서 사용)

data/raw/ 에 정상.csv, 수분부족.csv, 자극.csv 중 일부만 있어도(예: 하드웨어로 2가지
상태만 수집된 경우) 있는 파일만으로 진행하며, 어떤 상태가 빠졌는지 안내만 출력합니다.

실행:
    python preprocess.py
    python preprocess.py --raw_dir ../data/raw --out_dir ../data/spectrogram
"""

import argparse
import os

import numpy as np
import pandas as pd
from scipy.signal import butter, filtfilt, spectrogram

from spectro_render import render_rgb_image
from feature_extraction import extract_features, FEATURE_NAMES

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


def make_spectrogram_image(Sxx_db, out_path, img_size=IMG_SIZE):
    """이미 계산된 Sxx_db 배열을 224x224 viridis 컬러맵 스펙트로그램 PNG로 저장.

    matplotlib Figure 렌더링 대신 컬러맵 룩업 테이블 기반 render_rgb_image()를 사용한다
    (inference.py의 signal_to_feature()가 쓰는 render_gray_array()와 같은 색상 로직을
    공유하는 spectro_render 모듈이라 학습/추론 특징이 일치한다)."""
    img = render_rgb_image(Sxx_db, img_size=img_size)
    img.save(out_path)


def process_file(csv_path, out_root, fs=SAMPLE_RATE_HZ, window_sec=WINDOW_SEC, step_sec=STEP_SEC):
    """CSV 하나를 윈도우 단위로 나눠 스펙트로그램 PNG를 저장하고, 각 윈도우의 명시적
    특징 벡터를 (img_name, source_file, label, *features) 행 리스트로 함께 반환한다."""
    fname = os.path.basename(csv_path)
    if fname not in FILE_TO_CLASS:
        print(f"  [건너뜀] 매핑되지 않은 파일: {fname}")
        return 0, []

    cls = FILE_TO_CLASS[fname]
    out_dir = os.path.join(out_root, cls)
    os.makedirs(out_dir, exist_ok=True)

    df = pd.read_csv(csv_path)
    raw_signal = df["voltage"].to_numpy(dtype=float)

    filtered = bandpass_filter(raw_signal, fs)

    win_len = int(window_sec * fs)
    step_len = int(step_sec * fs)

    count = 0
    feature_rows = []
    base_name = os.path.splitext(fname)[0]
    for start in range(0, len(filtered) - win_len + 1, step_len):
        segment = filtered[start:start + win_len]

        f, t, Sxx = spectrogram(segment, fs=fs, nperseg=min(128, len(segment)),
                                 noverlap=64 if len(segment) > 128 else 0)
        Sxx_db = 10 * np.log10(Sxx + 1e-12)

        img_name = f"{base_name}_{start:06d}.png"
        img_path = os.path.join(out_dir, img_name)
        make_spectrogram_image(Sxx_db, img_path)

        feats = extract_features(segment, Sxx_db, f, t, fs=fs)
        feature_rows.append([img_name, fname, cls] + feats.tolist())

        count += 1

    print(f"  {fname} -> 클래스 '{cls}': {count}개 스펙트로그램 이미지 생성")
    return count, feature_rows


def main():
    parser = argparse.ArgumentParser(description="CSV 원시 신호 -> 대역통과필터 -> 스펙트로그램 이미지 + 특징")
    parser.add_argument("--raw_dir", default="../data/raw")
    parser.add_argument("--out_dir", default="../data/spectrogram")
    parser.add_argument("--features_csv", default="../data/features.csv")
    args = parser.parse_args()

    print(f"[preprocess] 원시 데이터: {args.raw_dir}")
    print(f"[preprocess] 출력(스펙트로그램): {args.out_dir}")

    existing = set(os.listdir(args.raw_dir)) if os.path.isdir(args.raw_dir) else set()
    missing_states = [fname.replace(".csv", "") for fname in FILE_TO_CLASS if fname not in existing]
    if missing_states:
        print(f"⚠️  다음 상태가 수집되지 않았습니다: {', '.join(missing_states)}")
        collected = [fname.replace(".csv", "") for fname in FILE_TO_CLASS if fname in existing]
        print(f"   ({' + '.join(collected) if collected else '수집된 상태 없음'} 데이터만으로 진행합니다)")

    total = 0
    all_feature_rows = []
    for fname in sorted(existing):
        if fname.endswith(".csv"):
            csv_path = os.path.join(args.raw_dir, fname)
            count, feature_rows = process_file(csv_path, args.out_dir)
            total += count
            all_feature_rows.extend(feature_rows)

    print(f"[preprocess] 총 {total}개 스펙트로그램 이미지 생성 완료")

    if all_feature_rows:
        columns = ["img_name", "source_file", "label"] + FEATURE_NAMES
        feat_df = pd.DataFrame(all_feature_rows, columns=columns)
        feat_df.to_csv(args.features_csv, index=False, encoding="utf-8")
        print(f"[preprocess] 명시적 특징 {len(feat_df)}개 행 -> {args.features_csv}")


if __name__ == "__main__":
    main()
