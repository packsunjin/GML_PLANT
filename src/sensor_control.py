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
import signal
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
    # 채널 0(A0) 지정. 라이브러리 버전에 따라 ADS.P0가 없을 수 있어(P0는 곧 정수 0) 안전하게 폴백한다.
    _CH0 = getattr(ADS, "P0", 0)
    chan = AnalogIn(ads, _CH0)  # AD8232 출력 -> A0
    # 아날로그 습도(토양 수분) 센서 -> A1. 안 꽂혀 있어도 읽기는 되므로(값이 뜸)
    # 실제 사용 여부는 read_moisture()의 범위 검사로 판단한다.
    _CH1 = getattr(ADS, "P1", 1)
    moisture_chan = AnalogIn(ads, _CH1)
    HARDWARE_AVAILABLE = True
except Exception as e:  # ImportError, NotImplementedError(비-Pi 환경), OSError(I2C 없음) 등
    HARDWARE_AVAILABLE = False
    moisture_chan = None
    _HW_ERR = str(e)


# ----------------------------------------------------------------------
# 온·습도 센서
# ----------------------------------------------------------------------
# 세 가지를 모두 지원하고, 붙어 있는 것을 자동으로 골라 쓴다.
#   1) AHT20 / AHT21 (I2C 0x38)  -> 온도 + 공기습도. 권장.
#   2) DHT22 / DHT11 (GPIO 1선)  -> 온도 + 공기습도.
#   3) 아날로그 토양수분 (+/-/OUT 3핀, ADS1115 A1) -> 흙 수분만. 온도는 못 잼.
# 세 개 다 없으면 값이 None이고, 화면에는 "센서 대기 중"으로 나온다.
ENV_SENSOR = None       # "AHT20" / "DHT22" / None
ENV_ERROR = None        # 왜 못 잡았는지 (진단용)
_env_dev = None
DHT_PIN_NAME = os.environ.get("GML_DHT_PIN", "D4")   # DHT를 쓴다면 꽂은 GPIO 번호

try:
    import adafruit_ahtx0                                    # noqa: E402
    if HARDWARE_AVAILABLE:
        _env_dev = adafruit_ahtx0.AHTx0(i2c)
        ENV_SENSOR = "AHT20"
    else:
        ENV_ERROR = "I2C 버스를 열지 못해 AHT20을 확인할 수 없음"
except Exception as _e:
    ENV_ERROR = f"AHT20 없음({_e})"

if ENV_SENSOR is None:
    try:
        import adafruit_dht                                  # noqa: E402
        import board as _board                               # noqa: E402
        _env_dev = adafruit_dht.DHT22(getattr(_board, DHT_PIN_NAME))
        ENV_SENSOR = "DHT22"
        ENV_ERROR = None
    except Exception as _e2:
        ENV_ERROR = f"{ENV_ERROR}; DHT22 없음({_e2})" if ENV_ERROR else f"DHT22 없음({_e2})"


# 아날로그 습도(토양 수분) 센서 보정값 -- 실제 센서로 한 번 재보고 맞추세요.
# 물에 담갔을 때(가장 젖음)와 공기 중(가장 마름)의 전압을 넣으면 됩니다.
MOISTURE_WET_V = 1.20   # 젖음 = 100%
MOISTURE_DRY_V = 2.60   # 마름 = 0%

# 온·습도는 매 요청마다 읽으면(특히 DHT22는 2초 간격 제한) 실패하므로 잠깐 캐시한다.
_env_cache = {"t": 0.0, "temp": None, "humidity": None}
ENV_CACHE_SEC = 2.0


def read_moisture(raw=False):
    """A1에 연결된 아날로그 토양수분 센서를 0~100%로 환산해 돌려준다.

    raw=True면 (퍼센트, 전압, 사유) 튜플을 돌려준다(보정/진단용).
    센서가 없거나 전압이 보정 범위를 크게 벗어나면 퍼센트는 None이다.
    """
    def out(pct, volts=None, why=None):
        return (pct, volts, why) if raw else pct

    if not HARDWARE_AVAILABLE:
        return out(None, None, "I2C 하드웨어 없음 (시뮬레이션 모드)")
    if moisture_chan is None:
        return out(None, None, "ADS1115 A1 채널을 열지 못함")
    try:
        volts = moisture_chan.voltage
    except Exception as e:
        return out(None, None, f"읽기 실패: {e}")
    if not (0.05 < volts < 3.30):
        return out(None, volts, "전압이 0.05~3.30V 밖 — 선이 빠졌거나 전원 미연결")

    lo, hi = sorted((MOISTURE_WET_V, MOISTURE_DRY_V))
    if not (lo - 0.4 <= volts <= hi + 0.4):
        return out(None, volts,
                   f"보정 범위({lo:.2f}~{hi:.2f}V) 밖 — MOISTURE_WET_V/DRY_V를 맞춰주세요")
    # 젖을수록 전압이 낮은 센서 기준(대부분의 정전용량식). 반대면 두 상수를 바꿔 넣으세요.
    pct = (MOISTURE_DRY_V - volts) / (MOISTURE_DRY_V - MOISTURE_WET_V) * 100.0
    return out(round(max(0.0, min(100.0, pct)), 1), volts, None)


def read_environment():
    """온도(°C)와 습도(%)를 돌려준다. -> {"temp": .., "humidity": .., "source": ..}

    AHT20/DHT22가 있으면 그걸로 온도+공기습도를 읽고, 없으면 온도는 None이고
    습도 자리에 아날로그 토양수분을 넣는다(무엇을 쟀는지는 source로 구분).
    """
    now = time.time()
    if now - _env_cache["t"] < ENV_CACHE_SEC:
        return {"temp": _env_cache["temp"], "humidity": _env_cache["humidity"],
                "source": ENV_SENSOR or ("토양수분(A1)" if HARDWARE_AVAILABLE else None)}

    temp = humidity = None
    if _env_dev is not None:
        try:
            temp = round(float(_env_dev.temperature), 1)
            humidity = round(float(_env_dev.relative_humidity), 1)
        except Exception:
            # DHT22는 종종 읽기에 실패한다. 이번 판만 건너뛰고 이전 값을 유지한다.
            temp, humidity = _env_cache["temp"], _env_cache["humidity"]

    if humidity is None:
        humidity = read_moisture()   # 온·습도 센서가 없으면 토양수분으로 대체

    _env_cache.update({"t": now, "temp": temp, "humidity": humidity})
    return {"temp": temp, "humidity": humidity,
            "source": ENV_SENSOR or ("토양수분(A1)" if HARDWARE_AVAILABLE else None)}


def sensor_status():
    """센서가 왜 안 잡히는지 화면에서 바로 볼 수 있게 진단 정보를 모은다."""
    pct, volts, why = read_moisture(raw=True)
    env = read_environment()
    return {
        "i2c": HARDWARE_AVAILABLE,
        "i2c_error": None if HARDWARE_AVAILABLE else globals().get("_HW_ERR"),
        "adc": "ADS1115" if HARDWARE_AVAILABLE else None,
        "env_sensor": ENV_SENSOR,
        "env_error": ENV_ERROR if ENV_SENSOR is None else None,
        "temp": env["temp"],
        "humidity": env["humidity"],
        "humidity_source": env["source"],
        "moisture_percent": pct,
        "moisture_volts": round(volts, 3) if volts is not None else None,
        "moisture_error": why,
        "calibration": {"wet_v": MOISTURE_WET_V, "dry_v": MOISTURE_DRY_V},
        "dht_pin": DHT_PIN_NAME,
    }


# 상태별 저장 파일명(한글). 수집된 원시 CSV는 이 이름으로 data/raw/ 아래에 저장된다.
# 정상/수분부족 = 지속 상태, 자극 = 순간 이벤트
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
    - 자극(잎 흔들기): 짧고 진폭 큰 액션포텐셜 유사 스파이크가 불규칙하게 발생 (순간 이벤트)
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


def _install_stop_handler():
    """SIGTERM/SIGINT를 KeyboardInterrupt로 바꿔, 웹의 '중지' 버튼으로 끊어도
    수집 루프가 정상 경로로 빠져나와 지금까지 모은 데이터를 저장하게 한다."""
    def _raise(signum, frame):
        raise KeyboardInterrupt()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, _raise)
        except (ValueError, OSError):
            pass   # 메인 스레드가 아니면 등록할 수 없다


def collect(state, duration_sec, sample_rate_hz, out_dir, progress_sec=5.0):
    """
    지정한 상태(state)에 대해 duration_sec 동안 sample_rate_hz로 샘플링하여
    out_dir/<상태>.csv 로 저장한다. (timestamp, voltage) 2개 컬럼.

    duration_sec가 0 이하이면 **중지할 때까지 무제한**으로 모은다.
    중간에 Ctrl+C(또는 SIGTERM)로 끊으면 그때까지 모은 만큼만 저장한다.
    progress_sec마다 진행 상황을 출력해 웹 로그에서 살아 있는지 보이게 한다.
    """
    if state not in VALID_STATES:
        raise ValueError(f"state는 {list(VALID_STATES)} 중 하나여야 합니다.")

    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, KOR_FILENAMES[state])

    unlimited = duration_sec is None or duration_sec <= 0
    n_samples = 0 if unlimited else int(duration_sec * sample_rate_hz)
    interval = 1.0 / sample_rate_hz

    mode_str = "HARDWARE (AD8232+ADS1115)" if HARDWARE_AVAILABLE else "SIMULATION (no I2C hardware detected)"
    length_str = "무제한 (중지할 때까지)" if unlimited else f"{duration_sec}s (총 {n_samples} 샘플)"
    print(f"[sensor_control] 모드: {mode_str}")
    print(f"[sensor_control] 상태='{state}' 샘플링={sample_rate_hz}Hz 길이={length_str} -> {out_path}")

    rows = []
    stopped = False
    start = time.time()
    next_report = progress_sec

    def report(i):
        done = i * interval
        if unlimited:
            print(f"[sensor_control] 수집 중… {done:.0f}초 ({i} 샘플)")
        else:
            print(f"[sensor_control] 수집 중… {done:.0f}/{duration_sec:.0f}초 "
                  f"({i}/{n_samples} 샘플, {100.0 * i / max(1, n_samples):.0f}%)")

    try:
        if HARDWARE_AVAILABLE:
            i = 0
            while unlimited or i < n_samples:
                rows.append((round(i * interval, 6), read_sample_hardware()))
                i += 1
                # 실제 하드웨어 샘플링 주기에 맞춰 페이싱
                elapsed = time.time() - start
                target = i * interval
                if target > elapsed:
                    time.sleep(min(target - elapsed, interval))
                if i * interval >= next_report:
                    report(i)
                    next_report += progress_sec
        else:
            # 시뮬레이션은 실시간 페이싱이 필요 없어 numpy로 한 번에 만들지만, 중간에
            # 멈출 수 있어야 하므로 progress_sec 길이의 덩어리로 나눠 생성한다.
            chunk = max(1, int(progress_sec * sample_rate_hz))
            i = 0
            while unlimited or i < n_samples:
                take = chunk if unlimited else min(chunk, n_samples - i)
                t_array = (np.arange(i, i + take)) * interval
                v_array = simulate_batch(t_array, state)
                rows.extend(zip(np.round(t_array, 6), v_array))
                i += take
                report(i)
                if unlimited:
                    # 무제한 시뮬레이션은 실제 시간에 맞춰 흘러가게 한다
                    # (안 그러면 순식간에 메모리를 다 쓴다).
                    time.sleep(progress_sec)
    except KeyboardInterrupt:
        stopped = True
        print(f"[sensor_control] ⏹ 중지 요청 — 지금까지 모은 {len(rows)} 샘플을 저장합니다.")

    if not rows:
        print("[sensor_control] 모은 데이터가 없어 저장하지 않습니다.")
        return None

    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["timestamp_sec", "voltage"])
        writer.writerows(rows)

    print(f"[sensor_control] 저장 완료{' (중지됨)' if stopped else ''}: "
          f"{out_path} ({len(rows)} rows, {len(rows) * interval:.1f}초)")
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
    parser.add_argument("--duration", type=float, default=30.0,
                        help="수집 시간(초), 기본 30초. 0이면 중지할 때까지 무제한")
    parser.add_argument("--rate", type=float, default=250.0, help="샘플링 주기(Hz), 100~1000 권장")
    parser.add_argument("--out", default="../data/raw", help="저장 폴더")
    args = parser.parse_args()

    _install_stop_handler()
    collect(args.state, args.duration, args.rate, args.out)


if __name__ == "__main__":
    main()
