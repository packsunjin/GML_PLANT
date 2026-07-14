"""
sensor_control.py
==================
AD8232(생체 전위 증폭기) + ADS1115(16비트 ADC)를 이용하여
식물 잎/줄기에 부착한 Ag 전극의 미세 전위 신호를 수집하는 모듈입니다.

- 실제 라즈베리파이 5 + I2C 환경에서는 `adafruit-circuitpython-ads1x15` 패키지를 사용합니다.
- 해당 하드웨어/라이브러리가 없는 개발 PC(검증 환경)에서는 자동으로
  SIMULATION 모드로 전환되어, 생체 전위와 유사한 합성 신호를 생성합니다.
  (요구사항의 "하드웨어가 없을 경우 시뮬레이션 입력 사용" 조건을 만족)

사용 예:
    python sensor_control.py --state 정상 --duration 30
    python sensor_control.py --state 수분부족 --duration 30 --rate 250
    python sensor_control.py --state 자극 --duration 30
"""

import argparse
import csv
import os
import time
import sys

import numpy as np

# ----------------------------------------------------------------------
# 하드웨어 접근 시도 (실패 시 시뮬레이션 모드로 자동 전환)
# ----------------------------------------------------------------------
HARDWARE_AVAILABLE = False
try:
    import board
    import busio
    import adafruit_ads1x15.ads1115 as ADS
    from adafruit_ads1x15.analog_in import AnalogIn

    i2c = busio.I2C(board.SCL, board.SDA)
    ads = ADS.ADS1115(i2c)
    chan = AnalogIn(ads, ADS.P0)  # AD8232 출력 -> A0
    HARDWARE_AVAILABLE = True
except Exception as e:  # ImportError, NotImplementedError(비-Pi 환경), OSError(I2C 없음) 등
    HARDWARE_AVAILABLE = False
    _HW_ERR = str(e)


# 상태별 저장 파일명(한글). 수집된 원시 CSV는 이 이름으로 data/raw/ 아래에 저장된다.
KOR_FILENAMES = {"정상": "정상.csv", "수분부족": "수분부족.csv", "자극": "자극.csv"}
VALID_STATES = tuple(KOR_FILENAMES.keys())


def read_sample_hardware():
    """ADS1115에서 실측 전위(V) 1개 샘플을 읽어온다."""
    return chan.voltage


def simulate_sample(t, state):
    """
    하드웨어가 없을 때 사용하는 합성 신호 생성기.
    실제 식물 전기신호(plant electrophysiology) 문헌에서 보고되는 패턴을 참고하여
    상태별로 다른 저주파 변동 + 노이즈 특성을 부여한다.

    - 정상: 완만한 기저선 변동 (0.05~0.5Hz 위주), 저잡음
    - 수분부족: 느린 드리프트(스트레스 반응) + 간헐적 저주파 스파이크
    - 자극(잎 흔들기): 짧고 진폭 큰 액션포텐셜 유사 스파이크가 불규칙하게 발생
    """
    baseline = 0.0  # V, AD8232 출력 기준선(가상)
    powerline_noise = 0.01 * np.sin(2 * np.pi * 50 * t)  # 50Hz 전원 노이즈(제거 대상)

    if state == "정상":
        signal = 0.02 * np.sin(2 * np.pi * 0.2 * t) + 0.01 * np.sin(2 * np.pi * 0.05 * t)
        noise = np.random.normal(0, 0.005)
    elif state == "수분부족":
        drift = 0.03 * np.sin(2 * np.pi * 0.02 * t)
        spike = 0.04 if (int(t * 10) % 47 == 0) else 0.0
        signal = drift + spike
        noise = np.random.normal(0, 0.008)
    elif state == "자극":
        # 불규칙한 순간 스파이크 (잎 접촉/흔들림에 의한 액션포텐셜 유사 반응)
        spike = 0.15 * np.exp(-((t % 1.3 - 0.15) ** 2) / (2 * 0.01 ** 2))
        signal = spike + 0.02 * np.sin(2 * np.pi * 0.3 * t)
        noise = np.random.normal(0, 0.01)
    else:
        raise ValueError(f"알 수 없는 상태: {state}")

    return baseline + signal + powerline_noise + noise


def simulate_batch(t_array, state):
    """simulate_sample()의 벡터화 버전. 시뮬레이션 모드에서는 실시간 페이싱이 필요 없으므로
    전체 구간을 numpy 배열 연산으로 한 번에 계산해 샘플별 파이썬 함수 호출 오버헤드를 없앤다.
    (수식은 simulate_sample()과 동일)"""
    baseline = 0.0
    powerline_noise = 0.01 * np.sin(2 * np.pi * 50 * t_array)

    if state == "정상":
        signal = 0.02 * np.sin(2 * np.pi * 0.2 * t_array) + 0.01 * np.sin(2 * np.pi * 0.05 * t_array)
        noise = np.random.normal(0, 0.005, size=t_array.shape)
    elif state == "수분부족":
        drift = 0.03 * np.sin(2 * np.pi * 0.02 * t_array)
        spike = np.where((t_array * 10).astype(np.int64) % 47 == 0, 0.04, 0.0)
        signal = drift + spike
        noise = np.random.normal(0, 0.008, size=t_array.shape)
    elif state == "자극":
        spike = 0.15 * np.exp(-((t_array % 1.3 - 0.15) ** 2) / (2 * 0.01 ** 2))
        signal = spike + 0.02 * np.sin(2 * np.pi * 0.3 * t_array)
        noise = np.random.normal(0, 0.01, size=t_array.shape)
    else:
        raise ValueError(f"알 수 없는 상태: {state}")

    return baseline + signal + powerline_noise + noise


def collect(state, duration_sec, sample_rate_hz, out_dir):
    """
    지정한 상태(state)에 대해 duration_sec 동안 sample_rate_hz로 샘플링하여
    out_dir/<상태>.csv 로 저장한다. (timestamp, voltage) 2개 컬럼.
    """
    if state not in VALID_STATES:
        raise ValueError(f"state는 {list(VALID_STATES)} 중 하나여야 합니다.")

    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, KOR_FILENAMES[state])

    n_samples = int(duration_sec * sample_rate_hz)
    interval = 1.0 / sample_rate_hz

    mode_str = "HARDWARE (AD8232+ADS1115)" if HARDWARE_AVAILABLE else "SIMULATION (no I2C hardware detected)"
    print(f"[sensor_control] 모드: {mode_str}")
    print(f"[sensor_control] 상태='{state}' 샘플링={sample_rate_hz}Hz 길이={duration_sec}s (총 {n_samples} 샘플) -> {out_path}")

    if HARDWARE_AVAILABLE:
        rows = []
        start = time.time()
        for i in range(n_samples):
            t = i * interval
            v = read_sample_hardware()
            rows.append((round(t, 6), v))

            # 실제 하드웨어 샘플링 주기에 맞춰 페이싱
            elapsed = time.time() - start
            target = (i + 1) * interval
            if target > elapsed:
                time.sleep(min(target - elapsed, interval))
    else:
        # 시뮬레이션 모드는 실시간 페이싱이 필요 없으므로, 샘플별 파이썬 반복문 대신
        # numpy 배열 연산으로 전체 구간을 한 번에 생성한다 (동일한 신호 수식 사용).
        t_array = np.arange(n_samples) * interval
        v_array = simulate_batch(t_array, state)
        rows = list(zip(np.round(t_array, 6), v_array))

    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["timestamp_sec", "voltage"])
        writer.writerows(rows)

    print(f"[sensor_control] 저장 완료: {out_path} ({len(rows)} rows)")
    return out_path


def read_single_realtime(state_for_sim="정상", t=None):
    """
    inference.py에서 실시간 1샘플을 가져올 때 사용하는 헬퍼.
    하드웨어가 있으면 실제 값을, 없으면 시뮬레이션 값을 반환한다.
    """
    if HARDWARE_AVAILABLE:
        return read_sample_hardware()
    if t is None:
        t = time.time() % 1000
    return simulate_sample(t, state_for_sim)


def main():
    parser = argparse.ArgumentParser(description="AD8232+ADS1115 식물 전위 신호 수집")
    parser.add_argument("--state", required=True, choices=list(VALID_STATES))
    parser.add_argument("--duration", type=float, default=30.0, help="수집 시간(초), 기본 30초")
    parser.add_argument("--rate", type=float, default=250.0, help="샘플링 주기(Hz), 100~1000 권장")
    parser.add_argument("--out", default="../data/raw", help="저장 폴더")
    args = parser.parse_args()

    collect(args.state, args.duration, args.rate, args.out)


if __name__ == "__main__":
    main()
