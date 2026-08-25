"""
frontend.py
===========
전극에서 ADC까지의 **아날로그 앞단**을 갈아끼울 수 있게 감싼 모듈.

이 프로젝트는 두 가지 앞단을 쓴다.

  ad8232 — 심전도용 생체전위 증폭기. 이득 1100배가 고정이고 0.5~40Hz 아날로그
           필터가 실리콘 안에 박혀 있다. 지금까지의 모든 측정이 이 구성이다.
  dc     — 오피앰프 버퍼 + ADS1115 차동입력. 아날로그 증폭이 없고 고역통과가
           없다(DC 결합). 느린 신호를 재려면 이쪽이어야 한다.

왜 나눴나
---------
AD8232 의 0.5Hz 고역통과는 수분 스트레스(수 시간 규모, 10⁻⁴ Hz 수준)를 원리적으로
지운다. 소프트웨어로 복구할 수 없어서 앞단 자체를 바꿔야 한다. 그런데 두 구성은
이득·대역·측정 범위가 전부 달라서, 한 코드에 조건문으로 섞으면 어느 쪽 기준으로
계산된 값인지 알 수 없게 된다. 그래서 '앞단이 무엇을 할 수 있는가'를 이 모듈이
값으로 들고 있고, 나머지 코드는 그 값을 물어보게 했다.

고르는 법
---------
    GML_FRONTEND=dc python3 main.py --web
    python3 sensor_control.py --frontend dc --state 정상

지정하지 않으면 ad8232 (지금까지의 구성) 이다.
"""

import os
import threading

# I2C 버스 접근을 직렬화한다(수집 루프와 진단이 동시에 건드리지 않도록).
_i2c_lock = threading.Lock()


class Frontend:
    """앞단 하나가 갖춰야 할 것. 하위 클래스가 read() 와 사양 값을 채운다."""

    name = "?"
    title = "?"
    gain = 1.0              # V/V — 전극 전압이 ADC 까지 오며 몇 배가 되는가
    hpf_hz = 0.0            # 아날로그 고역통과 차단(0 이면 DC 결합)
    lpf_hz = 0.0            # 아날로그 저역통과 차단
    input_span_mv = None    # 잘리지 않고 잴 수 있는 전극 간 전압(± mV)
    resolution_uv = None    # 전극 입력 환산 분해능
    # ADC 가 보는 값이 이 밖으로 나가면 잘린 것으로 본다. 앞단마다 완전히 다르다 —
    # AD8232 는 0~3.3V 단전원 출력이고, DC 차동은 ±PGA범위다.
    rail_high_v = None
    rail_low_v = None
    available = False
    error = None

    def read(self):
        """전극 신호 한 샘플(V, ADC 가 본 값)."""
        raise NotImplementedError

    def to_input_mv(self, v):
        """ADC 가 본 값(V) -> 전극 사이 전압(mV).
        문헌의 식물 전위(mV)와 비교하려면 반드시 이 환산을 거쳐야 한다."""
        return 1000.0 * float(v) / self.gain

    def describe(self):
        return {
            "name": self.name, "title": self.title,
            "gain": self.gain, "band": [self.hpf_hz, self.lpf_hz],
            "dc_coupled": self.hpf_hz == 0.0,
            "input_span_mv": self.input_span_mv,
            "rail": [self.rail_low_v, self.rail_high_v],
            "resolution_uv": self.resolution_uv,
            "available": self.available, "error": self.error,
        }


# ── ① AD8232 (지금까지의 구성) ────────────────────────────────────────
class AD8232Frontend(Frontend):
    """심전도용 생체전위 증폭기. 이득과 대역이 소자에 고정돼 있다.

    총 이득 1100배 = 계측증폭기 100배 x 저역통과단 11배(데이터시트 심전도 구성).
    3.3V 단전원에서 기준점이 중앙이라고 보면, 전극 사이 전압이 ±1.2mV만 넘어도
    출력이 레일에 부딪혀 잘린다. 문헌이 표면 전극으로 보고하는 식물 전위는
    수 mV~수십 mV라 이 범위를 넘는다 — 큰 신호가 '작게' 보이는 게 아니라
    아예 잘려서 레일 포화로 나타난다."""

    name = "ad8232"
    title = "AD8232 (심전도용 증폭기)"
    gain = 1100.0
    hpf_hz = 0.5
    lpf_hz = 40.0
    input_span_mv = 1.23        # (3.3/2 - 여유) / 1100
    # 3.3V 단전원 출력. 아래 값은 전원 전압에서 짐작한 것이고, 실측한 이 보드의
    # 진짜 한계는 2.57~2.60V / -0.59~-0.61V 였다. 그래서 값 판정만으로는 부족하고
    # '출력이 안 움직이는지'를 함께 봐야 한다(sensor_control.sensor_status 참고).
    rail_high_v = 3.0
    rail_low_v = 0.3

    def __init__(self):
        self._chan = None
        # 사양 값은 하드웨어가 붙어 있는지와 무관하다. 시뮬레이션에서도
        # '이 앞단으로는 무엇을 잴 수 있는가' 를 물어볼 수 있어야 한다.
        self.resolution_uv = self.to_input_mv(4.096 * 2 / 65536) * 1000
        try:
            import board, busio
            import adafruit_ads1x15.ads1115 as ADS
            from adafruit_ads1x15.analog_in import AnalogIn
            try:
                i2c = busio.I2C(board.SCL, board.SDA, frequency=400_000)
            except TypeError:
                i2c = busio.I2C(board.SCL, board.SDA)
            ads = ADS.ADS1115(i2c)
            # 싱글샷 128SPS 로는 250Hz 를 못 채운다(한 샘플에 8ms 이상).
            try: ads.data_rate = 860
            except Exception: pass
            try:
                from adafruit_ads1x15.ads1x15 import Mode
                ads.mode = Mode.CONTINUOUS
            except Exception: pass
            self._chan = AnalogIn(ads, getattr(ADS, "P0", 0))   # 증폭기 출력 -> A0
            self.available = True
        except Exception as e:
            self.error = str(e)

    def read(self):
        with _i2c_lock:
            return self._chan.voltage


# ── ② DC 결합 (오피앰프 버퍼 + ADS1115 차동입력) ──────────────────────
class DCFrontend(Frontend):
    """아날로그 증폭도 고역통과도 없는 앞단.

    전극 --> 오피앰프 전압 폴로어(이득 1배) --> ADS1115 차동입력(A0-A1)

    버퍼는 증폭하지 않고 임피던스만 낮춘다. 그래서
      · 아날로그 이득이 1배라 오프셋으로 레일에 붙을 일이 없다.
      · 고역통과가 없어 수 시간짜리 느린 변화가 그대로 남는다.
        (AD8232 의 0.5Hz 고역통과가 지우던 수분 스트레스 대역)
      · 증폭은 ADS1115 의 PGA 가 디지털로 맡는다.

    PGA 를 16배로 두면 측정 범위 ±256mV, 분해능 7.8µV 다. 문헌의 토마토
    활동전위 21.2mV 가 범위의 8% 라 잘리지 않고 들어온다 — AD8232 의 ±1.23mV
    범위에서는 17배를 넘어가 잘리던 값이다.

    ⚠️ 기준 전위 — 단전원이라 전극 전위를 전원 중간(1.65V)에 걸어 줘야 한다.
       100kΩ 두 개로 만든 분압을 버퍼로 받아 흙 전극에 연결한다.
    """

    name = "dc"
    title = "DC 결합 (버퍼 + ADS1115 차동)"
    gain = 1.0              # 아날로그 증폭 없음
    hpf_hz = 0.0            # DC 결합 — 고역통과 없음
    lpf_hz = 0.0            # 설정된 저역통과 없음(샘플레이트가 한계)

    # ADS1115 PGA 설정별 측정 범위(V). 16배가 이 용도에 맞다.
    PGA_RANGE_V = {1: 4.096, 2: 2.048, 4: 1.024, 8: 0.512, 16: 0.256}

    def __init__(self, pga=16):
        self._chan = None
        self.pga = pga if pga in self.PGA_RANGE_V else 16
        span_v = self.PGA_RANGE_V[self.pga]
        self.input_span_mv = span_v * 1000.0
        self.resolution_uv = span_v * 2 / 65536 * 1e6
        # 차동 입력이라 0 을 중심으로 ±span 이 측정 범위이고, 그 끝이 곧 포화다.
        # 여유를 조금 두어 끝에 닿기 전에 경고가 나오게 한다.
        self.rail_high_v = span_v * 0.98
        self.rail_low_v = -span_v * 0.98
        try:
            import board, busio
            import adafruit_ads1x15.ads1115 as ADS
            from adafruit_ads1x15.analog_in import AnalogIn
            try:
                i2c = busio.I2C(board.SCL, board.SDA, frequency=400_000)
            except TypeError:
                i2c = busio.I2C(board.SCL, board.SDA)
            ads = ADS.ADS1115(i2c)
            ads.gain = self.pga
            try: ads.data_rate = 860
            except Exception: pass
            try:
                from adafruit_ads1x15.ads1x15 import Mode
                ads.mode = Mode.CONTINUOUS
            except Exception: pass
            # 차동 입력: A0 와 A1 의 **차이**를 잰다. 공통으로 들어오는 잡음은
            # 양쪽에 똑같이 실리므로 빼면서 사라진다(계측증폭기가 하던 일).
            p0 = getattr(ADS, "P0", 0)
            p1 = getattr(ADS, "P1", 1)
            self._chan = AnalogIn(ads, p0, p1)
            self.available = True
        except Exception as e:
            self.error = str(e)

    def read(self):
        with _i2c_lock:
            return self._chan.voltage

    def describe(self):
        d = super().describe()
        d["pga"] = self.pga
        return d


_REGISTRY = {"ad8232": AD8232Frontend, "dc": DCFrontend}
DEFAULT = "ad8232"
_active = None


def choose(name=None):
    """앞단을 고른다. 이름이 없으면 GML_FRONTEND 환경변수, 그것도 없으면 ad8232."""
    global _active
    name = (name or os.environ.get("GML_FRONTEND") or DEFAULT).lower()
    if name not in _REGISTRY:
        raise ValueError(f"모르는 앞단: {name} (가능: {', '.join(_REGISTRY)})")
    _active = _REGISTRY[name]()
    return _active


def active():
    return _active if _active is not None else choose()


def names():
    return list(_REGISTRY)
