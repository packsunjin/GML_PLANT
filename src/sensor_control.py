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
import datetime
import json
import os
import signal
import threading
import time
import sys

import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import venv_boot; venv_boot.ensure()   # venv 를 안 켜도 알아서 갈아탄다

import numpy as np

# ----------------------------------------------------------------------
# 하드웨어 접근 시도 (실패 시 시뮬레이션 모드로 자동 전환)
# ----------------------------------------------------------------------
# 포화 판정 문턱은 앞단마다 다르다(AD8232 는 0~3.3V 단전원 출력, DC 차동은 ±PGA범위).
# 값은 아래 FRONTEND 에서 가져온다.
# 증폭기가 레일에 붙으면 입력이 변해도 출력이 안 따라가서 흔들림이 사라진다.
# 정상 측정에서는 잡음만으로도 이보다 훨씬 크게 흔들린다(실측 수 mV 이상).
STUCK_STD_V = 0.0008

# ── 아날로그 앞단 ─────────────────────────────────────────────────────
# 어떤 앞단(AD8232 / DC 결합)을 쓰는지에 따라 이득·대역·측정 범위가 전부 다르다.
# 그 값들은 frontend.py 가 들고 있고, 여기서는 물어보기만 한다.
# 고르는 법:  GML_FRONTEND=dc  또는  --frontend dc
import frontend

FRONTEND = frontend.choose()

FRONTEND_GAIN = FRONTEND.gain
ANALOG_HPF_HZ = FRONTEND.hpf_hz      # 0 이면 DC 결합(고역통과 없음)
ANALOG_LPF_HZ = FRONTEND.lpf_hz
INPUT_SPAN_MV = FRONTEND.input_span_mv
RAIL_HIGH_V = FRONTEND.rail_high_v
RAIL_LOW_V = FRONTEND.rail_low_v

# 예전 이름. 다른 모듈이 이 이름으로 가져다 쓰고 있어 그대로 남긴다.
# 다만 값은 지금 고른 앞단의 것이므로, AD8232 가 아닐 수도 있다.
AD8232_GAIN = FRONTEND_GAIN
AD8232_HPF_HZ = ANALOG_HPF_HZ
AD8232_LPF_HZ = ANALOG_LPF_HZ


def to_input_mv(v_out):
    """ADC 가 본 값(V) -> 전극 사이 전압(mV).
    문헌의 식물 전위 값(mV)과 비교하려면 반드시 이 환산을 거쳐야 한다."""
    return FRONTEND.to_input_mv(v_out)


HARDWARE_AVAILABLE = FRONTEND.available
_HW_ERR = FRONTEND.error


def read_sample_hardware():
    """전극 신호 1개 샘플(V)."""
    return FRONTEND.read()


def use_frontend(name, **opts):
    """앞단을 바꾼다. 모듈을 가져올 때 한 번 정해지므로, CLI 인자로 바꾸려면
    여기서 관련 값들을 다시 묶어 줘야 한다(안 그러면 옛 앞단의 이득·레일이 남는다)."""
    global FRONTEND, FRONTEND_GAIN, ANALOG_HPF_HZ, ANALOG_LPF_HZ, INPUT_SPAN_MV
    global RAIL_HIGH_V, RAIL_LOW_V, AD8232_GAIN, AD8232_HPF_HZ, AD8232_LPF_HZ
    global HARDWARE_AVAILABLE, _HW_ERR
    FRONTEND = frontend.choose(name, **opts)
    FRONTEND_GAIN = FRONTEND.gain
    ANALOG_HPF_HZ = FRONTEND.hpf_hz
    ANALOG_LPF_HZ = FRONTEND.lpf_hz
    INPUT_SPAN_MV = FRONTEND.input_span_mv
    RAIL_HIGH_V = FRONTEND.rail_high_v
    RAIL_LOW_V = FRONTEND.rail_low_v
    AD8232_GAIN, AD8232_HPF_HZ, AD8232_LPF_HZ = FRONTEND_GAIN, ANALOG_HPF_HZ, ANALOG_LPF_HZ
    HARDWARE_AVAILABLE = FRONTEND.available
    _HW_ERR = FRONTEND.error
    return FRONTEND


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

    # 기준점을 2초 재서 평균과 흔들림을 함께 본다.
    vs = []
    t0 = time.time()
    while time.time() - t0 < 2.0:
        try:
            vs.append(read_sample_hardware())
        except Exception as e:
            info["i2c_error"] = str(e)
            return info
        time.sleep(0.004)
    if not vs:
        return info

    v = np.asarray(vs, dtype=float)
    avg = float(v.mean())
    std = float(v.std())
    info["baseline"] = round(avg, 3)
    info["std"] = round(std, 5)
    info["min"] = round(float(v.min()), 3)
    info["max"] = round(float(v.max()), 3)

    # 위 RAIL_HIGH_V(3.0)·RAIL_LOW_V(0.3)는 전원 전압에서 짐작한 값이라 보드마다
    # 실제 한계와 다르다. 실측한 이 보드의 출력 한계는 2.57~2.60 V / -0.59~-0.61 V 였고,
    # 그래서 값만 보는 판정은 진짜 포화를 놓친다. 값이 문턱을 넘었는지보다
    # **출력이 아예 움직이지 않는지**가 포화의 확실한 증거다 — 레일에 붙으면
    # 입력이 변해도 출력이 따라가지 않으므로 표준편차가 무너진다.
    info["stuck"] = std < STUCK_STD_V
    if info["stuck"]:
        where = "상단" if avg > (RAIL_HIGH_V + RAIL_LOW_V) / 2 else "하단"
        info["baseline_verdict"] = (
            f"출력이 움직이지 않습니다(흔들림 {std*1000:.2f} mV) — {where} 레일 포화로 보입니다")
    elif avg > RAIL_HIGH_V:
        info["baseline_verdict"] = "상단 레일 포화 — 전극 연결을 확인하세요"
    elif avg < RAIL_LOW_V:
        info["baseline_verdict"] = "하단 레일 포화 — 전극 연결을 확인하세요"
    else:
        info["baseline_verdict"] = "정상 범위"

    # 지금 기준점에서 레일까지 남은 거리 = 잘리지 않고 잴 수 있는 신호 크기.
    # 출력 볼트로만 보면 감이 안 오므로 전극 입력 기준(mV)으로 환산해서 같이 준다.
    headroom_v = min(RAIL_HIGH_V - avg, avg - RAIL_LOW_V)
    info["headroom_mv"] = round(to_input_mv(headroom_v), 3)
    info["gain"] = FRONTEND_GAIN
    info["analog_band"] = [ANALOG_HPF_HZ, ANALOG_LPF_HZ]
    info["frontend"] = FRONTEND.describe()
    return info


# 상태별 저장 파일명(한글). 수집된 원시 CSV는 이 이름으로 data/raw/ 아래에 저장된다.
# 정상/수분부족 = 지속 상태, 자극 = 순간 이벤트
KOR_FILENAMES = {"정상": "정상.csv", "수분부족": "수분부족.csv", "자극": "자극.csv"}
VALID_STATES = tuple(KOR_FILENAMES.keys())


def session_filename(state, session=None):
    """상태 + 세션 번호 -> 저장 파일명.

    세션을 나누는 이유. 상태마다 한 번만 재면, 그 회차의 전극이 어느 방향으로
    표류했는지가 곧 클래스 라벨이 된다. "수분부족이라 전위가 밀린 것"과
    "그날 그 전극이 그렇게 표류한 것"을 가를 방법이 없다.
    (보고서 Ⅳ장 2절 (4) — 1차 학습의 높은 정확도가 실제로 그랬다)

        session=None -> 정상.csv      (첫 회차)
        session=2    -> 정상_2.csv
    """
    base = KOR_FILENAMES[state][:-4]
    return f"{base}.csv" if not session or int(session) <= 1 else f"{base}_{int(session)}.csv"


def next_session(out_dir, state):
    """그 상태의 다음 회차 번호. 이미 있는 파일을 세어 결정한다."""
    n = 1
    while os.path.isfile(os.path.join(out_dir, session_filename(state, n))):
        n += 1
    return n


def count_sessions(out_dir, state):
    """이미 수집된 회차 수."""
    return next_session(out_dir, state) - 1


# 전극 표류의 세기. 30분(1800초)에 표준편차 약 1mV 가 되도록 잡았다.
# (임의보행이라 시간 T 뒤의 표준편차는 이 값 x sqrt(T) 이다)
ELECTRODE_DRIFT_V_PER_RTS = 0.001 / np.sqrt(1800.0)


# 온도·습도처럼 **두 채널에 똑같이** 실리는 표류의 크기(V). 참조 채널을 빼면
# 사라지는 성분이 이것이고, 참조 채널을 두는 이유 자체다.
COMMON_DRIFT_V = 0.002


def _common_drift(t):
    """식물 채널과 참조 채널에 공통으로 실리는 느린 표류.

    t 만의 함수라 두 채널이 정확히 같은 값을 받는다 — 빼면 그대로 사라진다.
    실제로는 온도·습도·전원 변동처럼 두 전극쌍이 같은 환경에 놓여서 생긴다.
    독립 성분(각 채널의 random walk)은 빼도 안 사라지고 오히려 √2 배로 커지므로,
    참조 차감이 이득인지는 '공통 성분이 독립 성분보다 큰가'에 달려 있다."""
    t = np.asarray(t, dtype=float)
    return COMMON_DRIFT_V * (np.sin(2 * np.pi * t / 2400.0)
                             + 0.6 * np.sin(2 * np.pi * t / 700.0 + 1.1))


def _sim_core(t, state, rng=None):
    """상태별 합성 신호를 **전극에서 나오는 전압(V)** 으로 만든다.

    여기서 만드는 값은 앞단을 거치기 **전** 이다. 앞단의 이득·고역통과는
    _sim_through_frontend() 가 따로 적용한다. 이렇게 나눠야 'AD8232 를 쓰면
    이 신호가 어떻게 훼손되는가'를 시뮬레이션에서도 그대로 볼 수 있다.

    크기는 문헌값을 따른다 — 토마토 활동전위 21.2mV[12], 표면 전극 과도신호
    1mV 미만[9], 변동전위 수십 mV[13].
    """
    t = np.asarray(t, dtype=float)
    scalar = (t.ndim == 0)
    t = np.atleast_1d(t)
    n = t.shape
    randn = (rng.standard_normal(n) if rng is not None else np.random.normal(0, 1, n))

    # 상용전원 유도잡음(제거 대상). 국내는 60Hz.
    powerline = 0.0005 * np.sin(2 * np.pi * 60 * t)      # 0.5mV

    # ⚠️ 잡음과 배경 변동은 **세 상태에서 똑같아야 한다.**
    # 상태마다 잡음 크기를 다르게 주면, 분류기가 느린 드리프트가 아니라 잡음 크기로
    # 구분해 버린다. 그러면 0.5Hz 고역통과를 쓰는 앞단에서도 수분부족이 분류되어,
    # "AD8232 로는 수분부족을 못 잰다"는 이 연구의 결론과 어긋나는 가짜 결과가 나온다.
    # 상태 간 차이는 아래 sig 하나로만 준다.
    noise = 0.00015 * randn

    # 전극 자체의 느린 표류(1/f). Ag/AgCl 계면은 온도·습도·분극 때문에 시간당
    # 0.1~1mV 씩 제멋대로 움직인다. **어느 상태에나 있고**, 수분 스트레스와 같은
    # 대역에 있어서 실제 측정에서 가장 큰 방해물이다.
    # 이걸 빼고 백색잡음만 주면, 고역통과가 남기는 미세한 잔류(램프 입력에 대해
    # τ×기울기)를 평균으로 끄집어낼 수 있게 되어, AD8232 로도 수분부족이 분류되는
    # 가짜 결과가 나온다. 실제로는 이 표류에 묻힌다.
    dt = float(np.median(np.diff(t))) if t.size > 1 else 0.004
    walk = np.cumsum(randn) * ELECTRODE_DRIFT_V_PER_RTS * np.sqrt(dt)
    background = 0.0004 * np.sin(2 * np.pi * 0.2 * t) + walk + _common_drift(t)

    if state == "정상":
        sig = 0.0
    elif state == "수분부족":
        # 수분 스트레스의 본체는 **수 시간에 걸친 기준점 이동**이다(10⁻⁴ Hz 수준).
        # 0.5Hz 고역통과를 쓰는 앞단에서는 이 성분이 통째로 사라진다 — 그것이
        # AD8232 로 수분부족을 못 잰 이유이고, DC 결합이 필요한 이유다.
        sig = 0.012 * (1 - np.exp(-t / 3600.0))          # 시간 규모 1시간, 12mV
    elif state == "자극":
        # 접촉 활동전위. 문헌 진폭 21.2mV[12].
        # 반복 간격은 수집 프로토콜(표3: "5분간 10~20초 간격으로 반복 접촉")을 따른다.
        period = 15.0
        phase = (t % period) - 3.0
        sig = 0.0212 * np.exp(-(phase ** 2) / (2 * 2.5 ** 2))
    else:
        raise ValueError(f"알 수 없는 상태: {state}")

    sig = sig + background
    out = sig + powerline + noise
    return float(out[0]) if scalar else out


def _sim_through_frontend(v_in, t, fe=None):
    """전극 전압을 지금 고른 앞단에 통과시킨다.

    앞단이 신호에 하는 일은 두 가지다.
      · 고역통과 — 차단주파수보다 느린 성분을 지운다. AD8232 의 0.5Hz 가
        수분 스트레스(10⁻⁴ Hz)를 통째로 지우는 지점이 여기다.
      · 이득 — 곱한 뒤 레일을 넘으면 잘린다(포화).
    """
    fe = fe or FRONTEND
    v = np.atleast_1d(np.asarray(v_in, dtype=float))
    t = np.atleast_1d(np.asarray(t, dtype=float))

    if fe.hpf_hz > 0 and len(v) > 1:
        # 1차 고역통과를 시간영역에서 근사한다(느린 성분을 뺀다).
        dt = float(np.median(np.diff(t))) if len(t) > 1 else 0.004
        alpha = 1.0 / (1.0 + 2 * np.pi * fe.hpf_hz * dt)
        y = np.empty_like(v); y[0] = 0.0
        for i in range(1, len(v)):
            y[i] = alpha * (y[i - 1] + v[i] - v[i - 1])
        v = y

    out = v * fe.gain
    # 단전원 앞단은 기준점이 전원 중앙에 있다. 차동 앞단은 0 이 중심이다.
    if fe.rail_low_v is not None and fe.rail_low_v >= 0:
        out = out + (fe.rail_high_v + fe.rail_low_v) / 2.0
    if fe.rail_low_v is not None:
        out = np.clip(out, fe.rail_low_v, fe.rail_high_v)   # 레일 포화
    return out


def simulate_sample(t, state):
    """하드웨어가 없을 때 쓰는 합성 신호 1샘플. 지금 고른 앞단을 통과시킨 값이다."""
    return float(_sim_through_frontend(_sim_core(t, state), np.atleast_1d(t))[0])


def simulate_batch(t_array, state):
    """simulate_sample() 의 벡터화 버전(수식 동일)."""
    return _sim_through_frontend(_sim_core(t_array, state), t_array)


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
    # 크기는 반드시 '전극 입력 기준(mV)'으로 환산해서 말해야 한다. 증폭기 출력 볼트를
    # 그대로 문헌값과 비교하면 1100배만큼 틀린 이야기가 된다.
    print(f"    바탕잡음 {to_input_mv(sigma):.4f} mV 기준으로 최대 {to_input_mv(dev.max()):.3f} mV "
          f"까지 튀었습니다 (전극 입력 환산, 출력 {float(dev.max()):.2f}V).")
    print("    배선·전극이 흔들려 생긴 접촉 잡음일 가능성이 큽니다.")
    print("    ▸ 확인법: 식물은 그대로 두고 리드선만 흔들어 다시 재보세요.")
    print("      그때도 같은 튐이 나오면 식물 신호가 아닙니다.")
    print("    ▸ 이 상태로 학습하면 안 됩니다. preprocess 의 이벤트 선별은 '큰 창'을")
    print("      자극으로 고르므로, 이 튐이 오히려 학습 데이터로 선택됩니다.")
    print("    ▸ 대책: 리드선을 고정해 움직이지 않게 하고, 자극은 전극에서 먼 부위에")
    print("      비전도성 도구로 주세요.")


# 전극이 마르면서 생기는 느린 표류의 경보 기준(mV/분).
# 선행연구는 젖은/겔 전극을 몇 시간 붙여 두면 수십~수백 mV의 표류가 쌓인다고 보고한다.
# 짧은 수집 한 번에서 이 기울기를 넘으면, 곧 기준점이 전원 레일에 닿아 신호가 잘린다.
DRIFT_WARN_MV_PER_MIN = 30.0


def _report_drift(rows, fs):
    """수집 구간 전체의 기울기(전극 표류)를 재서 알려준다.

    식물에 붙인 젖은 전극은 시간이 지나면서 마르고, 그 과정에서 전극-식물 임피던스와
    접촉 전위가 변해 느린 표류가 생긴다. 표류 자체는 대역통과 하한(0.5Hz)이 걷어내지만,
    표류가 계속되면 증폭기 기준점이 레일 쪽으로 밀려 결국 신호가 통째로 잘린다.
    수집이 끝난 직후에 알려 줘야 전극을 다시 적시고 재수집할 수 있다."""
    if not HARDWARE_AVAILABLE or len(rows) < int(fs) * 10:
        return
    t = np.asarray([r[0] for r in rows], dtype=float)
    v = np.asarray([r[1] for r in rows], dtype=float)
    span_min = (t[-1] - t[0]) / 60.0
    if span_min <= 0:
        return

    # 1차 추세선의 기울기 = 분당 표류량 (증폭기 출력 기준)
    slope = float(np.polyfit(t, v, 1)[0]) * 60.0 * 1000.0   # mV/분
    total = slope * span_min                                 # 이번 수집 동안의 총 표류
    print(f"[sensor_control] 기준점 표류: 출력 {slope:+.1f} mV/분 "
          f"(전극 입력 환산 {to_input_mv(slope/1000.0)*1000:+.4f} mV/분, "
          f"이번 {span_min*60:.0f}초 동안 출력 {total:+.1f} mV)")
    if abs(slope) < DRIFT_WARN_MV_PER_MIN:
        return

    # 지금 값이 레일까지 얼마나 남았는지, 이 속도면 몇 분 뒤에 닿는지 계산해 준다.
    now = float(v[-1])
    margin_v = (RAIL_HIGH_V - now) if slope > 0 else (now - RAIL_LOW_V)
    minutes = margin_v * 1000.0 / abs(slope)
    print(f"[sensor_control] ⚠️  표류가 큽니다. 이 속도라면 약 {minutes:.0f}분 뒤에 "
          f"{'상단' if slope > 0 else '하단'} 레일에 닿아 신호가 잘립니다.")
    print("    전극이 마르는 것이 가장 흔한 원인입니다(젖은 전극일수록 빠릅니다).")
    print("    ▸ 대책: 전극 접촉면을 다시 적시고, 마르지 않게 덮은 뒤 재수집하세요.")
    print("    ▸ 긴 수집이 필요하면 중간에 한 번씩 센서 진단으로 기준점을 확인하세요.")


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


# ── 전극 안정화 대기 ────────────────────────────────────────────────
# 전극을 붙이거나 꽂는 행위 자체가 식물에 상처·자극 반응을 일으킨다. 문헌은 전극을
# 찔러 넣었을 때 초기 탈분극이 수 분에 걸쳐 진행된다고 보고하며, 젖은/겔 전극은
# 마르면서 계속 표류한다. 그래서 취득 전에 안정화 시간을 두는 것이 표준 절차다.
#
# 다만 "몇 초"라고 못 박으면 조건마다 맞지 않는다. 표면 부착은 금방 가라앉고
# 삽입형은 훨씬 오래 걸린다. 그래서 시간을 정해 두는 대신 기준점이 실제로
# 잠잠해졌는지를 재서, 조건이 충족되면 바로 넘어가고 아니면 더 기다린다.
SETTLE_SEC = 20.0                 # 최소 대기(초)
SETTLE_MAX_SEC = 180.0            # 최대 대기(초). 여기까지도 안 잡히면 경고하고 진행
SETTLE_STABLE_MV_PER_MIN = 30.0   # 기준점 기울기가 이 아래면 안정된 것으로 본다
SETTLE_CHUNK_SEC = 5.0            # 이 간격으로 끊어 재면서 판정한다


def _slope_mv_per_min(times, vals):
    """(시각, 전압) 목록의 1차 추세 기울기를 mV/분으로 돌려준다."""
    if len(times) < 2:
        return 0.0
    t = np.asarray(times, dtype=float); v = np.asarray(vals, dtype=float)
    if t[-1] - t[0] <= 0:
        return 0.0
    return float(np.polyfit(t, v, 1)[0]) * 60.0 * 1000.0


def _settle(settle_sec, sample_rate_hz, max_sec=SETTLE_MAX_SEC):
    """수집 시작 전에 전극이 안정될 때까지 기다린다(하드웨어일 때만).

    최소 settle_sec 은 무조건 기다리고, 그 뒤로는 기준점의 기울기가
    SETTLE_STABLE_MV_PER_MIN 아래로 떨어질 때까지 max_sec 까지 더 기다린다.
    이 구간의 값은 저장하지 않는다."""
    if not HARDWARE_AVAILABLE or settle_sec <= 0:
        return
    print(f"[sensor_control] 전극 안정화 대기 (최소 {settle_sec:.0f}초, 최대 {max_sec:.0f}초)")
    print("    붙인 직후의 접촉 전위·상처 반응이 가라앉기를 기다립니다.")
    interval = 1.0 / max(1.0, sample_rate_hz)
    t0 = time.time()
    times = []; vals = []; head = None
    try:
        while True:
            chunk_end = time.time() + SETTLE_CHUNK_SEC
            while time.time() < chunk_end:
                times.append(time.time() - t0)
                vals.append(read_sample_hardware())
                time.sleep(interval)
            if head is None:
                head = float(np.mean(vals))
            elapsed = time.time() - t0
            # 최근 3청크(약 15초)의 기울기로 판정한다
            n_recent = int(3 * SETTLE_CHUNK_SEC * sample_rate_hz)
            slope = _slope_mv_per_min(times[-n_recent:], vals[-n_recent:])
            avg = float(np.mean(vals[-n_recent:]))
            print(f"    {elapsed:5.0f}초  기준점 {avg:.3f}V  기울기 {slope:+7.1f} mV/분")
            if elapsed >= settle_sec and abs(slope) < SETTLE_STABLE_MV_PER_MIN:
                print(f"[sensor_control] 기준점이 안정됐습니다 ({elapsed:.0f}초 소요).")
                break
            if elapsed >= max_sec:
                print(f"[sensor_control] ⚠️  {max_sec:.0f}초를 기다려도 기준점이 계속 움직입니다 "
                      f"({slope:+.1f} mV/분).")
                print("    전극이 마르고 있거나 접촉이 불안정합니다. 접촉면을 다시 적시고")
                print("    마르지 않게 덮은 뒤 다시 시작하는 것이 좋습니다.")
                break
    except KeyboardInterrupt:
        print("[sensor_control] ⏹ 안정화 대기를 건너뜁니다.")
        return
    if not vals:
        return
    avg = float(np.mean(vals[-int(SETTLE_CHUNK_SEC * sample_rate_hz):]))
    print(f"[sensor_control] 안정화 후 기준점 {avg:.3f}V "
          f"(대기 시작 무렵 {head:.3f}V -> {avg - head:+.3f}V 이동)")
    if avg > RAIL_HIGH_V or avg < RAIL_LOW_V:
        print("[sensor_control] ⚠️  기준점이 전원 레일에 붙어 있습니다. 이대로 모으면")
        print("    파형이 통째로 잘립니다. 전극 접촉을 고치고 다시 시작하세요.")


def _git_rev():
    """수집에 쓰인 코드가 어느 커밋이었는지. 알 수 없으면 None."""
    try:
        import subprocess
        here = os.path.dirname(os.path.abspath(__file__))
        return subprocess.run(["git", "-C", here, "rev-parse", "--short", "HEAD"],
                              capture_output=True, text=True, timeout=5
                              ).stdout.strip() or None
    except Exception:
        return None


def meta_path(csv_path):
    """CSV 옆에 놓이는 출처 파일 경로."""
    return os.path.splitext(csv_path)[0] + ".meta.json"


def load_meta(csv_path):
    """출처 정보를 읽는다. 파일이 없으면 None — '출처 불명'이라는 뜻이다."""
    try:
        with open(meta_path(csv_path), encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def is_simulated(csv_path):
    """이 CSV 가 시뮬레이터 출력인가.
    True=시뮬레이터, False=실측, None=출처 파일이 없어 알 수 없음."""
    m = load_meta(csv_path)
    return None if m is None else (m.get("mode") != "hardware")


def _write_meta(out_path, state, session, sample_rate_hz, rows):
    span = float(rows[-1][0]) if rows else 0.0
    meta = {
        "mode": "hardware" if HARDWARE_AVAILABLE else "simulation",
        "hardware_error": None if HARDWARE_AVAILABLE else _HW_ERR,
        "frontend": FRONTEND.describe(),
        "state": state,
        "session": session,
        "requested_rate_hz": float(sample_rate_hz),
        "actual_rate_hz": (len(rows) / span) if span > 0 else None,
        "has_ref": FRONTEND.has_ref,
        "n_samples": len(rows),
        "duration_sec": span,
        "recorded_at": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
        "git_rev": _git_rev(),
    }
    with open(meta_path(out_path), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    if not HARDWARE_AVAILABLE:
        print(f"[sensor_control] ⚠️  이 파일은 시뮬레이터 출력입니다 — 실측 데이터가 아닙니다.")
        print(f"    {meta_path(out_path)} 에 기록해 두었습니다.")


def collect(state, duration_sec, sample_rate_hz, out_dir, progress_sec=5.0,
            settle_sec=SETTLE_SEC, settle_max_sec=SETTLE_MAX_SEC, session=None):
    """
    지정한 상태(state)에 대해 duration_sec 동안 sample_rate_hz로 샘플링하여
    out_dir/<상태>[_회차].csv 로 저장한다. (timestamp, voltage) 2개 컬럼.
    session 을 주면 그 회차로, 안 주면 비어 있는 다음 회차로 저장한다.

    settle_sec 동안은 전극이 안정되기를 기다렸다가(저장하지 않음) 수집을 시작한다.

    duration_sec가 0 이하이면 **중지할 때까지 무제한**으로 모은다.
    중간에 Ctrl+C(또는 SIGTERM)로 끊으면 그때까지 모은 만큼만 저장한다.
    progress_sec마다 진행 상황을 출력해 웹 로그에서 살아 있는지 보이게 한다.
    """
    if state not in VALID_STATES:
        raise ValueError(f"state는 {list(VALID_STATES)} 중 하나여야 합니다.")

    os.makedirs(out_dir, exist_ok=True)
    # 회차를 안 주면 비어 있는 다음 번호를 쓴다 — 덮어쓰지 않는다.
    if session is None:
        session = next_session(out_dir, state)
    out_path = os.path.join(out_dir, session_filename(state, session))

    unlimited = duration_sec is None or duration_sec <= 0
    n_samples = 0 if unlimited else int(duration_sec * sample_rate_hz)
    interval = 1.0 / sample_rate_hz

    mode_str = (f"HARDWARE ({FRONTEND.title})" if HARDWARE_AVAILABLE
                else f"SIMULATION (no I2C hardware detected) · 앞단 가정: {FRONTEND.title}")
    length_str = "무제한 (중지할 때까지)" if unlimited else f"{duration_sec}s (총 {n_samples} 샘플)"
    if FRONTEND.has_ref:
        mode_str += " · 참조채널 A2-A3 동시 기록"
    print(f"[sensor_control] 모드: {mode_str}")
    print(f"[sensor_control] 상태='{state}' 샘플링={sample_rate_hz}Hz 길이={length_str} -> {out_path}")

    _settle(settle_sec, sample_rate_hz, max_sec=settle_max_sec)

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
                if FRONTEND.has_ref:
                    v, vref = FRONTEND.read_pair()
                    rows.append((round(now_t, 6), v, vref))
                else:
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
                if FRONTEND.has_ref:
                    # 참조 채널은 같은 전극·같은 앞단이지만 식물이 없다. 그래서
                    # 표류·잡음·전원성분은 있고 상태 신호만 없다. 이 차이가 곧
                    # '식물 반응'과 '전극이 그냥 표류한 것'을 가르는 근거다.
                    r_array = simulate_batch(t_array, "정상")
                    rows.extend(zip(np.round(t_array, 6), v_array, r_array))
                else:
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
        header = ["timestamp_sec", "voltage"]
        if FRONTEND.has_ref:
            header.append("voltage_ref")      # 참조(더미) 채널 — 표류만 담긴 값
        writer.writerow(header)
        writer.writerows(rows)

    # ── 출처를 파일 옆에 같이 남긴다 ──────────────────────────────
    # 예전에는 HARDWARE/SIMULATION 을 화면에 print 만 하고 버렸다. 그래서 나중에
    # CSV 만 보고는 그게 식물에서 온 값인지 시뮬레이터가 만든 값인지 구분할
    # 방법이 아예 없었고, 실제로 시뮬레이터 출력이 실측 데이터로 오해된 채
    # 보고서까지 올라갔다. 신호 처리로 사후에 알아내려 하지 말고 수집할 때 적는다.
    _write_meta(out_path, state, session, sample_rate_hz, rows)

    _report_artifacts([r[1] for r in rows], sample_rate_hz)
    _report_motion_artifacts([r[1] for r in rows], sample_rate_hz)
    _report_drift(rows, sample_rate_hz)

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
    parser.add_argument("--duration", type=float, default=120.0,
                        help="수집 시간(초), 기본 120초. 0이면 중지할 때까지 무제한. "
                             "분석 창이 10초라 30초만 모으면 학습 이미지가 열 몇 장뿐이다")
    parser.add_argument("--rate", type=float, default=250.0, help="샘플링 주기(Hz), 100~1000 권장")
    parser.add_argument("--frontend", default=None, choices=frontend.names(),
                        help="아날로그 앞단. ad8232=지금까지의 구성, dc=버퍼+차동입력(DC 결합). "
                             "GML_FRONTEND 환경변수로도 지정할 수 있습니다.")
    parser.add_argument("--out", default="../data/raw", help="저장 폴더")
    parser.add_argument("--ref", action="store_true",
                        help="참조(더미) 채널 A2-A3 를 같이 기록한다. 식물 없이 전극쌍만 "
                             "담근 채널이라, 여기서 나온 표류는 식물 반응이 아니다. "
                             "차동쌍을 번갈아 읽으므로 채널당 샘플레이트가 절반이 된다.")
    parser.add_argument("--session", type=int, default=None,
                        help="회차 번호. 안 주면 비어 있는 다음 회차로 저장합니다. "
                             "상태마다 2회 이상 나눠 재야 전극 조건과 식물 상태를 가를 수 있습니다.")
    parser.add_argument("--settle", type=float, default=SETTLE_SEC,
                        help=f"수집 전 전극 안정화 최소 대기(초), 기본 {SETTLE_SEC:.0f}초. 0이면 대기 없음")
    parser.add_argument("--settle-max", type=float, default=SETTLE_MAX_SEC,
                        help=f"안정화 최대 대기(초), 기본 {SETTLE_MAX_SEC:.0f}초. "
                             "기준점이 잠잠해지면 이보다 일찍 넘어간다")
    args = parser.parse_args()
    if args.frontend or args.ref:
        # --ref 만 주고 --frontend 를 안 주면 지금 앞단을 그대로 다시 만든다.
        use_frontend(args.frontend or FRONTEND.name, ref=args.ref)
    if args.ref and not FRONTEND.has_ref:
        print(f"[sensor_control] ⚠️  {FRONTEND.title} 은(는) 참조 채널을 지원하지 않습니다 "
              "— 참조 없이 수집합니다. (DC 결합 앞단에서만 됩니다: --frontend dc --ref)")

    _install_stop_handler()
    collect(args.state, args.duration, args.rate, args.out,
            settle_sec=args.settle, settle_max_sec=args.settle_max,
            session=args.session)


if __name__ == "__main__":
    main()
