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
import threading
import time
import sys

import numpy as np

# ----------------------------------------------------------------------
# 하드웨어 접근 시도 (실패 시 시뮬레이션 모드로 자동 전환)
# ----------------------------------------------------------------------
# 출력이 이 범위를 벗어나면 증폭기가 전원 레일에 포화된 것으로 본다
# (preprocess.py 의 품질 게이트와 같은 기준).
RAIL_HIGH_V = 3.0
RAIL_LOW_V = 0.3

HARDWARE_AVAILABLE = False
try:
    import board
    import busio
    import adafruit_ads1x15.ads1115 as ADS
    from adafruit_ads1x15.analog_in import AnalogIn

    # I2C 기본 속도는 100kHz라 250Hz 샘플링에 필요한 왕복을 못 채운다. 400kHz로 올린다.
    try:
        i2c = busio.I2C(board.SCL, board.SDA, frequency=400_000)
    except TypeError:
        i2c = busio.I2C(board.SCL, board.SDA)   # frequency 인자를 안 받는 옛 버전
    ads = ADS.ADS1115(i2c)

    # ⚠️ 기본값(128 SPS + 싱글샷)으로는 250Hz 수집이 불가능하다.
    # 싱글샷은 읽을 때마다 변환을 시작하고 끝날 때까지 기다리므로 한 샘플에 ~8ms,
    # 실측 약 100Hz밖에 안 나온다. 그러면 CSV의 시간축(t=i/250)과 실제가 2.5배
    # 어긋나 모든 주파수 분석이 틀어진다.
    # -> 데이터레이트를 최대(860 SPS)로 올리고 연속 변환 모드로 바꾼다.
    try:
        ads.data_rate = 860
    except Exception:
        pass
    try:
        from adafruit_ads1x15.ads1x15 import Mode
        ads.mode = Mode.CONTINUOUS
    except Exception:
        pass

    # 채널 0(A0) 지정. 라이브러리 버전에 따라 ADS.P0가 없을 수 있어(P0는 곧 정수 0) 안전하게 폴백한다.
    _CH0 = getattr(ADS, "P0", 0)
    chan = AnalogIn(ads, _CH0)  # AD8232 출력 -> A0
    HARDWARE_AVAILABLE = True
except Exception as e:  # ImportError, NotImplementedError(비-Pi 환경), OSError(I2C 없음) 등
    HARDWARE_AVAILABLE = False
    _HW_ERR = str(e)


# I2C 버스 접근을 직렬화한다(수집 루프와 진단이 동시에 건드리지 않도록).
_ads_lock = threading.Lock()


def sensor_status():
    """측정계가 정상 상태인지 화면에서 바로 볼 수 있게 진단 정보를 모은다.

    확인하는 것은 두 가지다 — I2C로 ADC가 잡히는지, 그리고 증폭기 출력의 기준점이
    전원 레일에 붙지 않고 중간 범위에 있는지. 기준점이 레일에 붙어 있으면 입력이
    변해도 출력이 움직이지 않으므로 그 상태로 수집한 데이터는 쓸 수 없다."""
    info = {
        "i2c": HARDWARE_AVAILABLE,
        "i2c_error": None if HARDWARE_AVAILABLE else globals().get("_HW_ERR"),
        "adc": "ADS1115" if HARDWARE_AVAILABLE else None,
        "baseline": None,
        "baseline_verdict": None,
    }
    if not HARDWARE_AVAILABLE:
        return info

    # 기준점을 1초만 재서 평균을 낸다(레일에 붙어 있는지 보는 것이 목적).
    vs = []
    t0 = time.time()
    while time.time() - t0 < 1.0:
        try:
            vs.append(read_sample_hardware())
        except Exception as e:
            info["i2c_error"] = str(e)
            return info
        time.sleep(0.004)
    if not vs:
        return info

    avg = sum(vs) / len(vs)
    info["baseline"] = round(avg, 3)
    if avg > RAIL_HIGH_V:
        info["baseline_verdict"] = "상단 레일 포화 — 전극 연결을 확인하세요"
    elif avg < RAIL_LOW_V:
        info["baseline_verdict"] = "하단 레일 포화 — 전극 연결을 확인하세요"
    else:
        info["baseline_verdict"] = "정상 범위"
    return info


# 상태별 저장 파일명(한글). 수집된 원시 CSV는 이 이름으로 data/raw/ 아래에 저장된다.
# 정상/수분부족 = 지속 상태, 자극 = 순간 이벤트
KOR_FILENAMES = {"정상": "정상.csv", "수분부족": "수분부족.csv", "자극": "자극.csv"}
VALID_STATES = tuple(KOR_FILENAMES.keys())


def read_sample_hardware():
    """ADS1115에서 실측 전위(V) 1개 샘플을 읽어온다."""
    with _ads_lock:
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


def _report_artifacts(values, fs):
    """수집한 신호에 사람이 만든 인공 패턴이 섞였는지 확인해 알려준다.

    ADS1115 채널이 섞이거나 다른 프로세스가 같은 ADC를 동시에 읽으면, 정확히 0V로
    딱 떨어지는 값이 아주 규칙적인 주기로 나타난다. 눈으로는 그럴듯해 보여서
    학습까지 가버리기 쉬우므로 여기서 잡아 준다."""
    if not HARDWARE_AVAILABLE or len(values) < int(fs) * 5:
        return
    v = np.asarray(values, dtype=float)

    zero = np.abs(v) < 0.005          # 0V에 딱 붙은 샘플
    frac = float(zero.mean())
    if frac < 0.002:
        return
    # 주기성 판정은 간격 통계보다 스펙트럼이 안정적이다. 깊은 강하만 잡히면
    # 간격이 한 주기/두 주기로 섞여 통계가 무너지기 때문이다.
    # 0V 여부를 0/1 신호로 보고 스펙트럼에 뾰족한 봉우리가 있으면 주기적이라고 본다.
    mask = zero.astype(float)
    mask -= mask.mean()
    n = 1 << (len(mask).bit_length() - 1)      # 2의 거듭제곱 길이로 자른다
    spec = np.abs(np.fft.rfft(mask[:n])) ** 2
    freqs = np.fft.rfftfreq(n, d=1.0 / fs)
    lo = np.searchsorted(freqs, 0.5)           # 0.5Hz 아래(느린 변동)는 제외
    if lo >= len(spec) - 2:
        return
    peak = int(lo + np.argmax(spec[lo:]))
    ratio = float(spec[peak] / np.mean(spec[lo:]))
    if ratio <= 50:
        return

    f0 = float(freqs[peak])
    # 강하의 '모양'으로 원인을 나눈다.
    #  - 여러 샘플에 걸쳐 부드럽게 내려갔다 올라오면 -> 진짜 아날로그 파형
    #    (AD8232 fast-restore 가 전극 임피던스가 높아 계속 재작동하는 경우)
    #  - 한 샘플만 툭 튀고 이웃은 멀쩡하면 -> ADC 읽기/채널 혼선
    ramp = float(np.mean((np.abs(v) > 0.005) & (v < 0.5 * np.median(v[~zero]))))
    analog = ramp > frac          # 바닥 샘플보다 '내려가는 중' 샘플이 많으면 아날로그

    print(f"[sensor_control] ⚠️  주기적인 0V 강하가 발견됐습니다 "
          f"({f0:.1f}Hz = {1000/f0:.0f}ms 간격, 전체의 {100*frac:.1f}%).")
    if analog:
        print("    여러 샘플에 걸쳐 부드럽게 내려갔다 올라옵니다 = 실제 아날로그 파형입니다.")
        print("    AD8232 의 fast-restore 가 계속 재작동하는 것으로 보입니다.")
        print("    전극 임피던스가 너무 높을 때 나타납니다 — 접촉을 개선하세요:")
        print("    · 동봉된 심전도 패드는 사람 피부용이라 잎에는 거의 안 통합니다.")
        print("    · 알루미늄 포일로 잎자루를 감싸고 그 위에 클립을 무세요.")
        print(f"    · 접촉이 좋아질수록 이 비율({100*frac:.1f}%)이 떨어집니다. 0에 가까우면 성공입니다.")
    else:
        print("    한 샘플만 튀는 모양입니다 = ADC 읽기/채널 혼선으로 보입니다.")
    _report_i2c_users()


# 문헌이 보고한 토마토 활동전위 진폭(약 21mV, Volkov et al. 2018)을 기준 삼는다.
# 바탕잡음의 이 배수를 넘는 튐은 식물 전위로 설명하기 어렵다.
SPIKE_SIGMA = 20.0


def _report_motion_artifacts(values, fs):
    """배선·전극이 물리적으로 흔들려 생긴 튐을 찾아 알려준다.

    잎을 건드릴 때 리드선도 같이 흔들리면 접촉 저항이 순간적으로 변해 큰 스파이크가
    생기는데, 파형만 봐서는 식물 반응과 구분되지 않는다. 더 나쁜 것은 preprocess 의
    이벤트 선별이 '피크투피크가 큰 창'을 자극으로 고른다는 점이다 — 즉 이 아티팩트가
    섞이면 걸러지기는커녕 **그것만 골라서 학습된다.** 그래서 수집 직후에 잡아 준다.

    판정 기준은 바탕잡음 대비 배수다. 문헌의 식물 활동전위는 바탕잡음의 몇 배 수준인
    반면, 배선을 흔들어 생기는 튐은 수십~수백 배에 이르러 크기만으로도 구분된다."""
    if not HARDWARE_AVAILABLE or len(values) < int(fs) * 3:
        return
    v = np.asarray(values, dtype=float)

    # 중앙값 기준 편차(MAD)로 바탕잡음을 잰다. 평균/표준편차를 쓰면 튐 자체가
    # 기준을 부풀려 정작 그 튐을 못 잡는다.
    med = float(np.median(v))
    mad = float(np.median(np.abs(v - med)))
    sigma = mad * 1.4826           # MAD를 정규분포 표준편차로 환산
    if sigma <= 0:
        return

    dev = np.abs(v - med)
    spikes = dev > SPIKE_SIGMA * sigma
    if not spikes.any():
        return

    worst = float(dev.max() / sigma)
    # 튐이 몇 번 있었는지 — 연속된 샘플은 한 번으로 센다
    bursts = int(np.count_nonzero(np.diff(spikes.astype(np.int8)) == 1)) + int(spikes[0])
    span = len(v) / fs

    print(f"[sensor_control] ⚠️  바탕잡음의 {SPIKE_SIGMA:.0f}배를 넘는 튐이 "
          f"{bursts}회 발견됐습니다 (최대 {worst:.0f}배, {span:.0f}초 동안).")
    print(f"    바탕잡음 표준편차 {1000*sigma:.1f} mV 기준으로 최대 {1000*float(dev.max()):.0f} mV 까지 튀었습니다.")
    print("    이 정도 크기는 식물 전위로 설명하기 어렵습니다 — 문헌의 토마토 활동전위는")
    print("    약 21 mV 수준입니다. 배선·전극이 흔들려 생긴 접촉 잡음일 가능성이 큽니다.")
    print("    ▸ 확인법: 식물은 그대로 두고 리드선만 흔들어 다시 재보세요.")
    print("      그때도 같은 튐이 나오면 식물 신호가 아닙니다.")
    print("    ▸ 이 상태로 학습하면 안 됩니다. preprocess 의 이벤트 선별은 '큰 창'을")
    print("      자극으로 고르므로, 이 튐이 오히려 학습 데이터로 선택됩니다.")
    print("    ▸ 대책: 리드선을 고정해 움직이지 않게 하고, 자극은 전극에서 먼 부위에")
    print("      비전도성 도구로 주세요.")


def _report_i2c_users():
    """I2C 장치를 열고 있는 다른 프로세스를 찾아 알려준다.

    "웹으로 수집했는데도 오염됐다"는 상황에서, 정말 다른 프로세스가 같은 ADC를
    붙잡고 있는지 추측하지 않고 확인하기 위한 것. /proc 만 뒤지므로 추가 설치가 필요 없다."""
    me = os.getpid()
    users = []
    try:
        for pid in os.listdir("/proc"):
            if not pid.isdigit() or int(pid) == me:
                continue
            fd_dir = f"/proc/{pid}/fd"
            try:
                for fd in os.listdir(fd_dir):
                    target = os.readlink(os.path.join(fd_dir, fd))
                    if target.startswith("/dev/i2c-"):
                        with open(f"/proc/{pid}/cmdline", "rb") as f:
                            cmd = f.read().replace(b"\0", b" ").decode("utf-8", "replace").strip()
                        users.append((pid, target, cmd[:90]))
                        break
            except (PermissionError, FileNotFoundError, OSError):
                continue
    except Exception:
        return

    if users:
        print("    ⚠️  같은 I2C 장치를 열고 있는 다른 프로세스가 있습니다:")
        for pid, dev, cmd in users:
            print(f"       PID {pid}  {dev}  {cmd}")
        print("    이 프로세스를 끄고 다시 수집하면 깨끗해집니다.")
    else:
        print("    (I2C 를 열고 있는 다른 프로세스는 없습니다 — 다른 원인입니다)")


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

    def report(i, elapsed=None):
        # 경과 시간은 실제 시계 기준으로 보여준다. 하드웨어가 느리면 '샘플 수'와
        # '실제 초'가 어긋나는데, 그걸 숨기면 원인을 못 찾는다.
        done = elapsed if elapsed is not None else i * interval
        rate = (i / done) if done > 0 else 0.0
        slow = f"  ⚠️ 실측 {rate:.0f}Hz" if rate < sample_rate_hz * 0.9 else ""
        if unlimited:
            print(f"[sensor_control] 수집 중… {done:.0f}초 ({i} 샘플){slow}")
        else:
            print(f"[sensor_control] 수집 중… {done:.0f}/{duration_sec:.0f}초 "
                  f"({i}/{n_samples} 샘플, {100.0 * i / max(1, n_samples):.0f}%){slow}")

    try:
        if HARDWARE_AVAILABLE:
            i = 0
            while unlimited or i < n_samples:
                # 시간축은 '가정한 격자(i/fs)'가 아니라 실제 경과 시간을 적는다.
                # 하드웨어가 목표 속도를 못 따라가도 CSV의 시간이 거짓말하지 않게 하기 위함.
                # (i/fs 로 적으면 100Hz로 잰 것을 250Hz라고 우기게 되어 주파수가 다 틀어진다)
                now_t = time.time() - start
                rows.append((round(now_t, 6), read_sample_hardware()))
                i += 1
                # 실제 하드웨어 샘플링 주기에 맞춰 페이싱
                elapsed = time.time() - start
                target = i * interval
                if target > elapsed:
                    time.sleep(min(target - elapsed, interval))
                if elapsed >= next_report:
                    report(i, elapsed)
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
                report(i, i * interval)
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

    _report_artifacts([r[1] for r in rows], sample_rate_hz)
    _report_motion_artifacts([r[1] for r in rows], sample_rate_hz)

    span = float(rows[-1][0]) if rows else 0.0
    actual = (len(rows) / span) if span > 0 else float(sample_rate_hz)
    print(f"[sensor_control] 저장 완료{' (중지됨)' if stopped else ''}: "
          f"{out_path} ({len(rows)} rows, {span:.1f}초, 실측 {actual:.1f}Hz)")
    if HARDWARE_AVAILABLE and actual < sample_rate_hz * 0.9:
        print(f"[sensor_control] ⚠️  목표 {sample_rate_hz:.0f}Hz 인데 실제로는 {actual:.1f}Hz 로 읽혔습니다.")
        print("    ADS1115 가 못 따라가는 것입니다. 시간축은 실제 시각으로 저장했으니 데이터는")
        print("    유효하고, preprocess 가 CSV 의 timestamp 로 샘플레이트를 다시 계산합니다.")
        print(f"    수집 자체를 맞추려면:  --rate {actual:.0f}  로 다시 수집하세요.")
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
