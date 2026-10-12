# 2단계(전극 표류) 1차(10-07, 2시간)와 재측정(10-12, 1시간)을 같은 방법으로 비교한다.
#   파이에서: venv/bin/python3 -I report/stage2_compare.py <재측정.csv>
import sys, json
import numpy as np
from scipy import signal

FS = 250.0
R = "/home/sunjin/GML_PLANT/data/bench/"

def load(p):
    d = np.loadtxt(p, delimiter=",", skiprows=1)
    return d[:, 0] - d[0, 0], d[:, 1] * 1000.0

def summary(t, v, minutes):
    n30 = int(30 * FS); m = int(60 * FS)
    mm = v[: len(v) // m * m].reshape(-1, m).mean(1)
    def drift(a, b):
        w = v[(t >= a * 60) & (t < b * 60)]
        return round(float(w[-n30:].mean() - w[:n30].mean()), 2) if len(w) > 2 * n30 else None
    out = dict(dur_min=round(t[-1] / 60, 2), rate=round(len(v) / t[-1], 3),
               max_gap_ms=round(float(np.diff(t).max()) * 1000, 2),
               start=round(float(v[: n30].mean()), 2), end=round(float(v[-n30:].mean()), 2),
               min_means=np.round(mm, 1).tolist(),
               min_diff=np.round(np.diff(mm), 1).tolist(),
               # 분 단위 변화의 오르내림과 1분 sd 중앙값(1차 보고서와 같은 방법)
               updown_0_30=[int((np.diff(mm[:31]) > 0).sum()), int((np.diff(mm[:31]) < 0).sum())],
               sd1min_med_0_30=round(float(np.median([v[k * m:(k + 1) * m].std() for k in range(min(30, len(mm)))])), 2),
               drifts={f"{a}-{b}": drift(a, b) for a, b in ((0, minutes), (30, minutes), (minutes - 30, minutes))})
    s = t >= (minutes - 30) * 60
    out["slope_last30"] = round(float(np.polyfit(t[s] / 60, v[s], 1)[0]), 3)
    # 마지막 30분 대역 잡음 (1차와 같게: 10 Hz 로 솎은 뒤 2차 버터워스, 앞뒤로)
    x = v[s][: int(s.sum()) // 25 * 25].reshape(-1, 25).mean(1)
    for lo, hi in ((0.01, 1.0), (0.02, 0.5)):
        sos = signal.butter(2, [lo, hi], "bandpass", fs=10.0, output="sos")
        out[f"sd_{lo}_{hi}_last30"] = round(float(signal.sosfiltfilt(sos, x).std()), 2)
    return out

res = {}
t, v = load(R + "stage2_electrode_2h.csv")
res["first_0_60"] = summary(t[t < 3600], v[t < 3600], 60)    # 1차의 앞 60분 — 재측정과 같은 길이로
t2, v2 = load(sys.argv[1])
res["repeat"] = summary(t2, v2, int(round(t2[-1] / 60)))
print(json.dumps(res, ensure_ascii=False))
