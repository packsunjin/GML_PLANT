"""
preprocess.py
=============
data/raw/*.csv 의 원시 전위 신호를 다음과 같이 처리합니다.

1. SciPy Butterworth 대역통과필터(0.5Hz~45Hz) + 50Hz 노치필터로 전원 노이즈 및 DC 드리프트 제거
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
from scipy.signal import butter, filtfilt, iirnotch, spectrogram

from spectro_render import render_rgb_image
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

SAMPLE_RATE_HZ = 250.0  # sensor_control.py 기본 샘플링과 일치
WINDOW_SEC = 2.0        # 스펙트로그램 1장을 만들 신호 구간 길이
STEP_SEC = 1.0          # 슬라이딩 윈도우 이동 간격 (오버랩으로 이미지 수 확보)
IMG_SIZE = 224


def bandpass_filter(signal, fs, low=0.5, high=45.0, order=4, notch_freq=50.0, notch_q=30.0):
    """Butterworth 대역통과필터(0.5Hz~45Hz)로 저주파 드리프트/고주파 잡음을 줄이고,
    이어서 50Hz IIR 노치필터로 전원 노이즈를 추가로 감쇠시킨다.

    대역통과 상한이 45Hz라 50Hz는 통과대역 밖이지만, order=4 Butterworth의 감쇠만으로는
    50Hz 성분이 30% 안팎 남는다. 노치를 더하면 절반 수준(~18%)까지 더 줄어든다. 다만
    윈도우가 2초로 짧아 필터 과도응답 때문에 완전히 0이 되지는 않는다.
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
                 low=0.5, high=45.0, notch_freq=50.0,
                 quality=True, rail_high=RAIL_HIGH_V, rail_low=RAIL_LOW_V, min_std=MIN_STD_V):
    """CSV 하나를 윈도우 단위로 나눠 스펙트로그램 PNG를 저장하고, 각 윈도우의 명시적
    특징 벡터를 (img_name, source_file, label, *features) 행 리스트로 함께 반환한다.
    low/high/notch_freq로 대역통과·노치 필터 대역을 조절한다(느린 식물 신호 보존 시 low를 낮춤)."""
    fname = os.path.basename(csv_path)
    if fname not in FILE_TO_STATE:
        print(f"  [건너뜀] 매핑되지 않은 파일: {fname}")
        return 0, []

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
            measured = 1.0 / dt
            if abs(measured - fs) / fs > 0.05:
                print(f"  [샘플레이트] CSV 실측 {measured:.1f}Hz (기본 {fs:.0f}Hz와 다름) -> 실측값 사용")
            fs = measured

    # 레일 포화 검사는 단전원(0~3.3V) 하드웨어 신호에만 의미가 있다. 시뮬레이션 CSV는
    # 0V를 중심으로 흔들리므로 그대로 적용하면 전부 "바닥 레일"로 오판한다.
    # 파일의 중앙값이 0.5V보다 위면 하드웨어 신호로 보고 레일 검사를 켠다.
    hw_like = float(np.median(raw_signal)) > 0.5
    rail_check = quality and hw_like

    win_len = int(window_sec * fs)
    step_len = int(step_sec * fs)

    count = 0
    skipped_rail = 0
    skipped_flat = 0
    feature_rows = []
    base_name = os.path.splitext(fname)[0]
    # 필터는 각 윈도우를 개별적으로 적용한다. 실시간 추론(inference.py)은 스트리밍으로 들어온
    # 2초 버퍼만 필터링할 수 있으므로, 학습 특징도 반드시 "윈도우 단위 필터링"으로 만들어야
    # 필터 과도응답까지 포함해 학습/추론 특징이 일치한다. (전체 신호를 한 번에 필터링한 뒤
    # 잘라내면 윈도우 경계의 과도응답이 서로 달라 train/serve skew가 생긴다 — 특히 통계/주파수
    # 특징 14개는 이 차이에 민감하다.)
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

        segment = bandpass_filter(raw_win, fs, low=low, high=high, notch_freq=notch_freq)

        if quality and float(np.std(segment)) < min_std:
            # 필터 후에도 거의 평평하면 신호가 없는 것(선 빠짐/전원만 잡힘).
            skipped_flat += 1
            continue

        f, t, Sxx = spectrogram(segment, fs=fs, nperseg=min(128, len(segment)),
                                 noverlap=64 if len(segment) > 128 else 0)
        Sxx_db = 10 * np.log10(Sxx + 1e-12)

        img_name = f"{base_name}_{start:06d}.png"
        img_path = os.path.join(out_dir, img_name)
        make_spectrogram_image(Sxx_db, img_path)

        feats = extract_features(segment, Sxx_db, f, t, fs=fs)
        feature_rows.append([img_name, fname, cls] + feats.tolist())

        count += 1

    if quality and not hw_like:
        print("    [품질] 0V 중심 신호라 레일 포화 검사는 건너뜁니다(시뮬레이션 데이터).")
    total_win = count + skipped_rail + skipped_flat
    print(f"  {fname} -> 클래스 '{cls}': {count}개 스펙트로그램 이미지 생성"
          + (f"  (전체 {total_win}창 중 {100.0*count/total_win:.0f}% 사용)" if total_win else ""))
    if skipped_rail or skipped_flat:
        print(f"    [품질] 건너뜀 — 전원 레일 포화 {skipped_rail}창"
              f"{f', 신호 없음 {skipped_flat}창' if skipped_flat else ''}")
        if count == 0:
            print("    ⚠️  쓸 수 있는 구간이 하나도 없습니다. 전극 접촉을 확인하고 다시 수집하세요.")
        elif count < 10:
            print(f"    ⚠️  쓸 수 있는 창이 {count}개뿐입니다. 더 길게 수집하는 것이 좋습니다.")
    return count, feature_rows


def main():
    parser = argparse.ArgumentParser(description="CSV 원시 신호 -> 대역통과필터 -> 스펙트로그램 이미지 + 특징")
    parser.add_argument("--raw_dir", default="../data/raw")
    parser.add_argument("--out_dir", default="../data/spectrogram")
    parser.add_argument("--features_csv", default="../data/features.csv")
    parser.add_argument("--lowcut", type=float, default=0.5,
                        help="대역통과 하한(Hz). 느린 식물 신호(수분부족 등)를 살리려면 0.05~0.1 처럼 낮추세요")
    parser.add_argument("--highcut", type=float, default=45.0, help="대역통과 상한(Hz)")
    parser.add_argument("--notch", type=float, default=50.0, help="노치 주파수(Hz). 0이면 노치 끔")
    parser.add_argument("--no-quality", action="store_true",
                        help="품질 검사를 끄고 모든 창을 그대로 변환한다(포화 구간 포함)")
    parser.add_argument("--rail-high", type=float, default=RAIL_HIGH_V,
                        help=f"평균이 이 값보다 높은 창은 포화로 보고 버림(기본 {RAIL_HIGH_V}V)")
    parser.add_argument("--min-std", type=float, default=MIN_STD_V,
                        help=f"필터 후 표준편차가 이보다 작으면 신호 없음으로 보고 버림(기본 {MIN_STD_V}V)")
    parser.add_argument("--only", default=None, choices=sorted(set(FILE_TO_STATE.values())),
                        help="이 상태 하나만 변환한다. 나머지 상태의 결과는 그대로 둔다.")
    args = parser.parse_args()

    print(f"[preprocess] 원시 데이터: {args.raw_dir}")
    print(f"[preprocess] 출력(스펙트로그램): {args.out_dir}")
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
    for fname in sorted(targets):
        if fname.endswith(".csv"):
            csv_path = os.path.join(args.raw_dir, fname)
            count, feature_rows = process_file(csv_path, args.out_dir,
                                               low=args.lowcut, high=args.highcut, notch_freq=args.notch,
                                               quality=not args.no_quality,
                                               rail_high=args.rail_high, min_std=args.min_std)
            total += count
            all_feature_rows.extend(feature_rows)

    # 사용한 필터 대역을 사이드카로 기록 -> train.py가 읽어 모델 번들에 저장 -> inference가 동일 필터 사용.
    meta_path = os.path.join(os.path.dirname(args.features_csv) or ".", "preprocess_meta.json")
    with open(meta_path, "w", encoding="utf-8") as mf:
        json.dump({"lowcut": args.lowcut, "highcut": args.highcut,
                   "notch_freq": args.notch, "notch_q": 30.0,
                   "sample_rate": SAMPLE_RATE_HZ, "window_sec": WINDOW_SEC, "step_sec": STEP_SEC}, mf)
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
