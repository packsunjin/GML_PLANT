#!/usr/bin/env python3
"""
check_sensors.py
================
센서가 왜 안 잡히는지 한 번에 확인하는 진단 스크립트.

    cd ~/project/src
    python3 check_sensors.py

여기 나온 출력을 그대로 복사해서 보내주면 원인을 바로 짚을 수 있습니다.
"""

import os
import subprocess
import sys


def line(title):
    print(f"\n=== {title} " + "=" * max(0, 44 - len(title)))


def run(cmd):
    try:
        out = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=15)
        return (out.stdout + out.stderr).strip()
    except Exception as e:
        return f"(실행 실패: {e})"


def main():
    line("시스템")
    print("파이 모델 :", run("cat /proc/device-tree/model 2>/dev/null | tr -d '\\0'") or "(알 수 없음)")
    print("커널      :", run("uname -r"))
    print("파이썬    :", sys.version.split()[0], "|", sys.executable)

    line("I2C 버스 (ADS1115)")
    print(run("ls /dev/i2c-* 2>/dev/null") or "(I2C 장치 파일 없음 -> raspi-config 에서 I2C 켜세요)")
    print(run("i2cdetect -y 1 2>&1 | head -12") or "(i2cdetect 없음: sudo apt install -y i2c-tools)")
    print("\n0x48 = ADS1115 가 보여야 합니다.")

    line("파이썬 라이브러리")
    for mod in ("board", "busio", "adafruit_ads1x15"):
        try:
            __import__(mod)
            print(f"  {mod:24s} OK")
        except Exception as e:
            print(f"  {mod:24s} 없음/실패 ({type(e).__name__}: {e})")

    line("sensor_control 이 본 결과")
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import sensor_control as sc
        st = sc.sensor_status()
        for k in ("i2c", "i2c_error", "adc", "baseline", "baseline_verdict"):
            print(f"  {k:18s}: {st.get(k)}")

        line("AD8232 전극 상태 (10초 측정)")
        if sc.HARDWARE_AVAILABLE:
            import time
            import numpy as np
            # 전극을 바꿀 때마다 5분씩 수집하지 않아도 되게, 10초만 재서
            # 기준점과 fast-restore 강하 비율을 바로 보여준다.
            fs, dur = 250.0, 10.0
            vs = []
            t0 = time.time()
            i = 0
            while time.time() - t0 < dur:
                vs.append(sc.chan.voltage)
                i += 1
                nxt = t0 + i / fs
                if nxt > time.time():
                    time.sleep(nxt - time.time())
            v = np.asarray(vs, dtype=float)
            avg = float(np.mean(v))
            print(f"  {len(v)}샘플 / {time.time()-t0:.1f}초  (실효 {len(v)/(time.time()-t0):.0f}Hz)")
            print(f"  기준점 평균 {avg:.3f} V   표준편차 {v.std():.4f} V   범위 {v.min():.3f}~{v.max():.3f}")

            if avg > sc.RAIL_HIGH_V or avg < sc.RAIL_LOW_V:
                print("  ❌ 전원 레일에 붙어 있습니다 = 전극이 안 붙었거나 배선 문제.")
                print("     RA/LA/RL 3개 모두 연결됐는지, GND 공통인지 확인하세요.")
            else:
                print("  ✅ 기준점이 레일에 붙지 않았습니다 (정상 범위)")

            # fast-restore 강하 비율 — 이 숫자가 전극 접촉의 품질 지표다.
            med = float(np.median(v))
            drop = float(np.mean(v < med * 0.5))
            print(f"\n  fast-restore 강하 비율: {100*drop:.2f}%   <- 이 숫자를 보세요")
            if drop < 0.001:
                print("  ✅ 접촉이 좋습니다. 이 상태로 수집하세요.")
            elif drop < 0.01:
                print("  △ 조금 있습니다. 쓸 수는 있지만 더 낮추면 좋습니다.")
            else:
                print("  ❌ 강하가 많습니다. 원인이 둘 중 하나일 수 있습니다:")
                print("     [먼저 확인] 지금 web_dashboard.py(웹 대시보드)가 백그라운드에서")
                print("       같이 돌고 있나요? 그럼 그 프로세스도 같은 I2C 버스를 쓰므로")
                print("       두 프로세스가 동시에 접근해 이 숫자가 부풀려질 수 있습니다 —")
                print("       대시보드를 끄고 이 스크립트만 단독으로 다시 돌려보세요.")
                print("     [그래도 높으면] 전극 접촉 문제입니다. 개선해보세요:")
                print("     · 패드 밑 줄기에 물 한 방울 (젤이 마르면 저항이 확 올라갑니다)")
                print("     · 패드를 줄기에 감아 붙여 닿는 면적을 넓히기")
                print("     · 점퍼선 핀을 줄기에 1~2mm 찔러넣기 (가장 확실)")
            print("  (전극을 바꾸고 이 명령을 다시 돌려 숫자를 비교하세요)")
        else:
            print("  (I2C 하드웨어가 없어 건너뜁니다)")
    except Exception as e:
        print(f"  sensor_control 로드 실패: {type(e).__name__}: {e}")

    print("\n" + "=" * 50)
    print("이 출력을 그대로 복사해서 보내주세요.")


if __name__ == "__main__":
    main()
