#!/usr/bin/env python3
"""
check_sensors.py
================
센서가 왜 안 잡히는지 한 번에 확인하는 진단 스크립트.

    cd ~/project/src
    python3 check_sensors.py

여기 나온 출력을 그대로 복사해서 보내주면 원인을 바로 짚을 수 있습니다.
"""

import glob
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

    line("I2C 버스 (ADS1115 / AHT20)")
    print(run("ls /dev/i2c-* 2>/dev/null") or "(I2C 장치 파일 없음 -> raspi-config 에서 I2C 켜세요)")
    print(run("i2cdetect -y 1 2>&1 | head -12") or "(i2cdetect 없음: sudo apt install -y i2c-tools)")
    print("\n0x48 = ADS1115 / 0x38 = AHT20 이 보여야 합니다.")

    line("DHT22 커널 드라이버 (파이 5 권장)")
    cfg = "/boot/firmware/config.txt"
    if not os.path.isfile(cfg):
        cfg = "/boot/config.txt"
    print("설정 파일 :", cfg)
    got = run(f"grep -n 'dht' {cfg} 2>/dev/null")
    print("dtoverlay :", got or "(없음)")
    devs = sorted(glob.glob("/sys/bus/iio/devices/iio:device*"))
    print("IIO 장치  :", devs or "(없음)")
    for d in devs:
        # dht11 커널 드라이버는 첫 읽기가 자주 타임아웃난다. 3초 간격으로 여러 번 시도한다.
        import time
        ok = 0
        for i in range(6):
            try:
                with open(os.path.join(d, "in_temp_input")) as fh:
                    t = float(fh.read().strip()) / 1000
                with open(os.path.join(d, "in_humidityrelative_input")) as fh:
                    h = float(fh.read().strip()) / 1000
                print(f"  시도 {i+1}: 온도 {t:.1f} °C / 습도 {h:.1f} %")
                ok += 1
            except Exception as e:
                print(f"  시도 {i+1}: 실패 ({e})")
            time.sleep(3)
        print(f"  -> 6번 중 {ok}번 성공")
        if ok == 0:
            print("  ⚠️  장치는 있는데 센서가 응답하지 않습니다 = 배선/센서 문제.")
            print("      · config.txt 의 gpiopin= 번호와 실제로 꽂은 핀이 같은지")
            print("      · VCC=3V3(물리 1번), GND=공통(물리 6번) 인지")
            print("      · 맨 센서(4핀)면 DATA-VCC 사이 10kΩ 풀업이 있는지")
        elif ok < 6:
            print("  ✅ 읽힙니다. (DHT22 는 원래 실패가 잦아 코드가 재시도합니다)")
    if not devs:
        print("\n  -> 켜는 법:")
        print(f"     echo 'dtoverlay=dht11,gpiopin=4' | sudo tee -a {cfg}")
        print("     sudo reboot")

    line("파이썬 라이브러리")
    for mod in ("board", "busio", "adafruit_ads1x15", "adafruit_dht", "adafruit_ahtx0"):
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
        for k in ("i2c", "i2c_error", "adc", "env_sensor", "env_method", "iio_device",
                  "env_error", "env_read_error", "temp", "humidity", "humidity_source",
                  "moisture_percent", "moisture_volts", "moisture_error", "dht_pin"):
            print(f"  {k:18s}: {st.get(k)}")

        line("AD8232 출력 전압 (전극 상태 확인)")
        if sc.HARDWARE_AVAILABLE:
            import time
            vs = []
            for _ in range(10):
                vs.append(sc.chan.voltage)
                time.sleep(0.2)
            print("  " + "  ".join(f"{v:.3f}" for v in vs))
            avg = sum(vs) / len(vs)
            print(f"  평균 {avg:.3f} V")
            if avg > 2.8 or avg < 0.4:
                print("  ⚠️  전원 레일에 붙어 있습니다 = 전극이 안 붙었거나(leads-off) 배선 문제.")
                print("      RA/LA/RL 3개 모두 연결됐는지, GND 공통인지 확인하세요.")
            elif 1.2 < avg < 2.1:
                print("  ✅ 정상 범위(전원의 절반 근처)입니다.")
            else:
                print("  ? 애매한 값입니다. 전극 접촉을 다시 확인해 보세요.")
        else:
            print("  (I2C 하드웨어가 없어 건너뜁니다)")
    except Exception as e:
        print(f"  sensor_control 로드 실패: {type(e).__name__}: {e}")

    print("\n" + "=" * 50)
    print("이 출력을 그대로 복사해서 보내주세요.")


if __name__ == "__main__":
    main()
