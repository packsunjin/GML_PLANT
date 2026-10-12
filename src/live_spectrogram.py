"""live_spectrogram.py — 식물 신호를 실시간 스펙트로그램으로 보는 페이지.

    GML_PGA=2 venv/bin/python3 src/live_spectrogram.py
    (PC 에서)  ssh -N -L 8000:127.0.0.1:8000 gml   →  http://localhost:8000

- ADC 를 이 프로세스 하나만 연다. 다른 수집기와 같이 돌리면 같은 파일·같은 칩을
  둘이 건드려 기록이 깨진다(0절 ⑯ 뒤 치기 측정에서 실제로 깨졌다).
- 원신호를 data/plant/live_<시각>.csv 로 계속 저장하고, "쳤어" 버튼을 누른 순간을
  같은 이름의 .marks.csv 에 표본 번호로 남긴다. 시계 하나(파이)만 쓴다.
"""
import os, sys, time, threading, json, datetime
os.environ.setdefault("GML_PGA", "2")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np
from flask import Flask, Response, request, jsonify
import frontend

FS = 250.0                 # 표본화 주파수
NFFT = 1024                # 창 4.1초 → 주파수 칸 0.24 Hz
HOP = int(FS * 0.25)       # 0.25초마다 한 줄
KEEP = int(FS * 900)       # 메모리에 15분 보관

fe = frontend.choose()
if not fe.available:
    sys.exit(f"ADC 를 열 수 없음: {fe.error}")

buf = np.zeros(KEEP, dtype=np.float32)
n_total = 0                 # 지금까지 읽은 표본 수
lock = threading.Lock()
marks = []                  # 표본 번호

stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
root = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "plant")
os.makedirs(root, exist_ok=True)
csv_path = os.path.join(root, f"live_{stamp}.csv")
mark_path = os.path.join(root, f"live_{stamp}.marks.csv")
with open(csv_path, "w") as f:
    f.write("sample,voltage_mV\n")
with open(mark_path, "w") as f:
    f.write("sample,seconds,wallclock,cond,kind\n")
with open(os.path.join(root, f"live_{stamp}.meta.json"), "w", encoding="utf-8") as f:
    json.dump({"mode": "hardware", "frontend": fe.describe(), "fs": FS,
               "started": datetime.datetime.now().isoformat(timespec="seconds"),
               "what": "실시간 스펙트로그램 페이지로 기록. 표시와 저장을 한 프로세스가 한다"},
              f, ensure_ascii=False, indent=2)


def reader():
    global n_total
    pending = []
    t0 = time.perf_counter()
    k = 0
    while True:
        target = t0 + k / FS
        dt = target - time.perf_counter()
        if dt > 0:
            time.sleep(dt)
        v = fe.read() * 1000.0
        with lock:
            buf[n_total % KEEP] = v
            n_total += 1
            idx = n_total - 1
        pending.append(f"{idx},{v:.5f}\n")
        if len(pending) >= int(FS):            # 1초마다 파일에 붙인다
            with open(csv_path, "a") as f:
                f.writelines(pending)
            pending = []
        k += 1


def window(last_idx, length):
    """last_idx 로 끝나는 length 개 표본 (없으면 None)."""
    first = last_idx - length + 1
    if first < 0 or n_total - first > KEEP:
        return None
    ids = np.arange(first, last_idx + 1) % KEEP
    return buf[ids].astype(np.float64)


HANN = np.hanning(NFFT)
FREQ = np.fft.rfftfreq(NFFT, 1 / FS)

app = Flask(__name__)


@app.get("/")
def page():
    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "live_spectrogram.html"),
              encoding="utf-8") as f:
        return Response(f.read(), mimetype="text/html")


@app.get("/frame")
def frame():
    """since(마지막으로 받은 줄 번호) 이후의 새 줄들과 최근 파형을 돌려준다."""
    since = int(request.args.get("since", -1))
    with lock:
        n = n_total
    last_col = (n - 1) // HOP - 1            # 완성된 마지막 줄
    first_col = max(since + 1, last_col - 240)
    cols = []
    for c in range(first_col, last_col + 1):
        w = window((c + 1) * HOP - 1, NFFT)
        if w is None:
            continue
        spec = np.abs(np.fft.rfft((w - w.mean()) * HANN)) ** 2
        cols.append(np.round(10 * np.log10(spec + 1e-12), 1).tolist())
    # 최근 60초 파형: 0.1초 평균 (60 Hz 를 대부분 지운다)
    trace = []
    tw = window(n - 1, int(FS * 60)) if n > FS * 60 else window(n - 1, n) if n > 0 else None
    if tw is not None:
        m = int(FS * 0.1)
        tw = tw[: len(tw) // m * m]
        trace = np.round(tw.reshape(-1, m).mean(axis=1), 2).tolist()
    return jsonify({"col0": first_col if cols else since + 1, "cols": cols,
                    "freq_step": FS / NFFT, "hop_s": HOP / FS, "n": n, "fs": FS,
                    "trace": trace, "trace_dt": 0.1,
                    "marks": [m for m in marks if m >= n - FS * 300]})


@app.post("/mark")
def mark():
    # 대조 측정(E)용 — 어떤 조건(cond)의 구간 시작(start)인지 자극(hit)인지 같이 남긴다.
    body = request.get_json(silent=True) or {}
    cond = str(body.get("cond", "")).replace(",", " ")[:40]
    kind = "start" if body.get("kind") == "start" else "hit"
    with lock:
        idx = n_total - 1
    if kind == "hit":
        marks.append(idx)
    with open(mark_path, "a", encoding="utf-8") as f:
        f.write(f"{idx},{idx / FS:.2f},{datetime.datetime.now().isoformat(timespec='seconds')},{cond},{kind}\n")
    return jsonify({"ok": True, "sample": idx, "seconds": round(idx / FS, 2), "count": len(marks)})


if __name__ == "__main__":
    threading.Thread(target=reader, daemon=True).start()
    print(f"기록 파일: {csv_path}")
    app.run(host="127.0.0.1", port=8000, threaded=True)
