"""bias_check.py
================
바이어스 노드가 살아 있는지 본다. 단극(GND 기준)으로 절대 전압을 읽는다.

**왜 따로 필요한가**
평소 측정은 차동(A0-A1)이다. 그런데 1단계처럼 두 입력을 같은 노드에 단락시켜
두면 차동은 무엇이 잘못돼 있어도 0 이 나온다. 실제로 바이어스 분압에 저항 대신
커패시터가 꽂혀 노드가 떠 있었는데도 차동은 0.219 mV 로 조용했다(CLAUDE.md 0절 ⑩).
공통 성분이 전부 상쇄됐기 때문이다. 그래서 절대 전압을 따로 봐야 한다.

합격 기준 — A0·A1 이 **1.65 V(3.3 V 의 절반) 근처**이고, 20초 동안 흔들림이
수 mV 안에 머문다. 저항 분압이면 그렇게 나온다. 떠 있으면 엉뚱한 값에서
수백 mV 폭으로 방황한다.

    venv/bin/python3 src/bias_check.py
"""
import time

import board, busio
import adafruit_ads1x15.ads1115 as ADS
from adafruit_ads1x15.analog_in import AnalogIn

VDD = 3.3
EXPECT = VDD / 2            # 분압이 살아 있을 때 폴로어 출력
SETTLE_TOL_V = 0.010        # 20초 흔들림이 이 안이면 "눌려 있다"고 본다
NEAR_TOL_V = 0.10           # 기대값에서 이만큼 안이면 합격


def _pins():
    """라이브러리 버전에 따라 ADS.P0 이 없다. frontend.py 와 같은 방식으로 푼다."""
    return [getattr(ADS, f"P{i}", i) for i in range(4)]


def main():
    i2c = busio.I2C(board.SCL, board.SDA)
    ads = ADS.ADS1115(i2c)
    ads.gain = 1            # ±4.096 V — 3.3 V 계를 다 담는다
    P = _pins()

    labels = ["A0 (폴로어 A 출력)", "A1 (폴로어 B 출력)", "A2", "A3"]
    print(f"기대값: 분압이 살아 있으면 A0/A1 이 {EXPECT:.2f} V 근처\n")

    reads = []
    for p, label in zip(P, labels):
        ch = AnalogIn(ads, p)
        vs = [ch.voltage for _ in range(8)]
        avg = sum(vs) / len(vs)
        reads.append(avg)
        print(f"{label:22s} {avg:7.4f} V   (min {min(vs):.4f} / max {max(vs):.4f})")

    print(f"\nA0 을 20초 추적 (떠 있으면 한 방향으로 흐르거나 크게 방황한다)")
    ch = AnalogIn(ads, P[0])
    t0 = time.time()
    track = []
    while time.time() - t0 < 20:
        v = ch.voltage
        track.append(v)
        print(f"  {time.time() - t0:5.1f}s  {v:7.4f} V")
        time.sleep(2)

    swing = max(track) - min(track)
    near = abs(reads[0] - EXPECT) < NEAR_TOL_V and abs(reads[1] - EXPECT) < NEAR_TOL_V
    steady = swing < SETTLE_TOL_V

    print(f"\n20초 흔들림 폭: {swing * 1000:.1f} mV")
    if near and steady:
        print("합격 — 바이어스 분압이 살아 있다.")
    elif not near:
        print(f"불합격 — A0/A1 이 {EXPECT:.2f} V 에서 너무 멀다. "
              "분압에 저항이 아니라 커패시터가 꽂혀 있는지 보라(CLAUDE.md ⑩).")
    else:
        print("불합격 — 값은 맞지만 너무 흔들린다. 접촉과 배선을 보라.")


if __name__ == "__main__":
    main()
