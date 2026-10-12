# 보고서 숫자 재검산 (B 항목). 파이에서: venv/bin/python3 -I recheck.py
import numpy as np, json
from scipy import signal
R = "/home/sunjin/GML_PLANT/data/"
FS = 250.0
out = {}

def load(p):
    d = np.loadtxt(R + p, delimiter=",", skiprows=1)
    return d[:, 0], d[:, 1] * 1000.0

# ── 1단계 ──
t, v = load("bench/dc_shorted_30min_v2.csv")
n30 = int(30 * FS)
out["s1"] = dict(n=len(v), dur_s=round(t[-1] - t[0], 2), mean=round(v.mean(), 4),
                 drift=round(v[-n30:].mean() - v[:n30].mean(), 5),
                 min_step_uV=round(np.min(np.diff(np.unique(v))) * 1000, 4))
med = np.median(v)
idx = np.where(np.abs(v - med) > 1.0)[0]           # 1 mV 넘게 튄 표본
ev = []
for i in idx:
    if not ev or t[i] - ev[-1][1] > 3: ev.append([t[i], t[i], abs(v[i] - med)])
    else: ev[-1][1] = t[i]; ev[-1][2] = max(ev[-1][2], abs(v[i] - med))
out["s1"]["spikes"] = [[round(a, 1), round(c, 2)] for a, b, c in ev]
out["s1"]["spike_n"] = len(ev)
out["s1"]["spike_gaps"] = [round(ev[k + 1][0] - ev[k][0], 1) for k in range(len(ev) - 1)]
# 1분 평균 지그재그: 튐 구간(±3초) 빼고 다시
m = int(60 * FS)
mm = v[: len(v) // m * m].reshape(-1, m).mean(1)
mask = np.ones(len(v), bool)
for a, b, c in ev:
    mask &= ~((t >= a - 3) & (t <= b + 3))
vv = np.where(mask, v, np.nan)
mm2 = np.nanmean(vv[: len(v) // m * m].reshape(-1, m), 1)
out["s1"]["min_means"] = np.round(mm, 4).tolist()
out["s1"]["min_means_nospike"] = np.round(mm2, 4).tolist()
out["s1"]["spike_minute"] = [int(a // 60) for a, b, c in ev]

# ── 2단계 ──
t, v = load("bench/stage2_electrode_2h.csv")
def win(a, b): return v[(t >= a * 60) & (t < b * 60)]
def drift(a, b):
    w = win(a, b); return round(w[-n30:].mean() - w[:n30].mean(), 2)
out["s2"] = dict(n=len(v), dur_s=round(t[-1], 1),
                 drift_0_120=drift(0, 120.01), drift_38=drift(38, 120.01), drift_60=drift(60, 120.01), drift_90=drift(90, 120.01))
s = (t >= 90 * 60)
out["s2"]["slope_90_120_polyfit"] = round(np.polyfit(t[s] / 60, v[s], 1)[0], 4)
out["s2"]["slope_90_120_endpoint"] = round(out["s2"]["drift_90"] / 30, 4)
mm = v[: len(v) // int(60 * FS) * int(60 * FS)].reshape(-1, int(60 * FS)).mean(1)
out["s2"]["min_means_25_55"] = {k: round(mm[k], 1) for k in range(25, 56)}
dm = np.diff(mm)
out["s2"]["min_diff_25_55"] = {k + 1: round(dm[k], 1) for k in range(25, 55)}
# 28~40분 기록 간격
s = (t >= 28 * 60) & (t < 40 * 60)
dt = np.diff(t[s])
out["s2"]["gap_28_40"] = dict(max_dt=round(dt.max(), 4), median_dt=round(np.median(dt), 5),
                              n_gt_0_02=int((dt > 0.02).sum()), rate=round(1 / np.mean(dt), 3))
sec = v[: len(v) // 250 * 250].reshape(-1, 250).mean(1)       # 1초 평균
seg = sec[28 * 60:40 * 60]
d1 = np.diff(seg)
k = np.argsort(-np.abs(d1))[:5]
out["s2"]["sec_jumps_28_40"] = [[round((28 * 60 + int(i) + 1) / 60, 2), round(d1[i], 1)] for i in k]
# 93.8분 단발
s = (t >= 88 * 60) & (t < 100 * 60)
w = v[s]; tt = t[s]
w10 = w[: len(w) // 25 * 25].reshape(-1, 25).mean(1); t10 = tt[: len(w) // 25 * 25].reshape(-1, 25).mean(1)
base = signal.medfilt(w10, 301)
j = np.argmax(np.abs(w10 - base))
out["s2"]["event_88_100"] = dict(t_min=round(t10[j] / 60, 2), dev_mV=round(w10[j] - base[j], 1))

# ── 식물 ──
v = np.loadtxt(R + "plant/live_20260915_070020.csv", delimiter=",", skiprows=1)[:, 1]
out["plant"] = dict(n=len(v), dur_s=round(len(v) / FS, 1))
a = v[: len(v) // 25 * 25].reshape(-1, 25).mean(1)             # 0.1초 평균
def count_touch(sec, thresh, mode):
    x = a[: int(sec * 10)]
    if mode == "local":
        w = 300; pad = np.pad(x, (w // 2, w // 2), mode="edge")
        ref = np.array([np.median(pad[i:i + w]) for i in range(len(x))])
    else:
        ref = np.full(len(x), np.median(x))
    on = x > ref + thresh
    n = 0; i = 0
    while i < len(on):
        if on[i]:
            j = i
            while j < len(on) and on[j]: j += 1
            if j - i >= 2: n += 1
            i = j
        else: i += 1
    return n
out["plant"]["touch_counts"] = {f"{s}s_{m}_{th}": count_touch(s, th, m)
                                for s in (1719, 2109) for m, th in (("local", 60), ("global", 80))}
# 기준선 하강 15~21분 (1분 중앙값)
mins = {k: round(float(np.median(a[k * 600:(k + 1) * 600])), 1) for k in range(13, 22)}
out["plant"]["min_median_13_21"] = mins
# 비접촉 sd: 대역별
q = v[int(600 * FS):int(840 * FS)]                            # 10~14분 (그림5 조용한 구간)
out["plant"]["quiet_10_14"] = dict(sd_raw01=round(float(a[6000:8400].std()), 2))
for lo, hi in ((0.01, 1), (0.5, 20)):
    sos = signal.butter(2 if lo < 0.5 else 4, [lo, hi], "bandpass", fs=FS, output="sos")
    seg = v[int(300 * FS):int(1200 * FS)]                      # 5~20분, 여유 두고 거름
    y = signal.sosfiltfilt(sos, seg)[int(300 * FS):int(540 * FS)]   # 10~14분
    out["plant"]["quiet_10_14"][f"sd_{lo}_{hi}"] = round(float(y.std()), 2)
# 10 Hz 줄과 상용전원: 조용한 10분(5~15분) 긴 FFT
seg = v[int(300 * FS):int(900 * FS)]
f, P = signal.welch(seg - seg.mean(), fs=FS, nperseg=len(seg), window="hann")
def peak(lo, hi):
    s = (f >= lo) & (f <= hi); i = np.argmax(P[s]); return round(float(f[s][i]), 4)
fm = peak(59, 61); f10 = peak(9.5, 10.5)
out["plant"]["fft"] = dict(res_hz=round(f[1] - f[0], 5), mains=fm, line10=f10,
                           alias_240=round(abs(4 * fm - FS), 4),
                           pct_55_65=round(float(P[(f >= 55) & (f <= 65)].sum() / P[f > 0].sum() * 100), 1))
# 그림6 70 mV 계단(1255~1345초 안의 작은 계단 높이)
x = a[12550:13450]
out["plant"]["fig6_levels"] = np.round(np.percentile(x, [10, 50, 75, 90]), 1).tolist()
print(json.dumps(out, ensure_ascii=False))
