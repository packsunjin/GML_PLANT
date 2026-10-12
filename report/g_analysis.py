# G 항목 + 표9 재계산. 파이에서: nice -n 19 venv/bin/python3 -I g_analysis.py
import os, sys, json, time, glob
import numpy as np, pandas as pd
ROOT = "/home/sunjin/GML_PLANT"
sys.path.insert(0, ROOT + "/src")
from PIL import Image
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.decomposition import PCA
from sklearn.svm import SVC
from sklearn.ensemble import RandomForestClassifier
from sklearn.model_selection import GridSearchCV, GroupKFold
from sklearn.inspection import permutation_importance
from sklearn.metrics import confusion_matrix

out = {}
TEST = {"자극_1", "자극_11", "자극_12", "자극_3", "자극_4", "정상_11", "정상_13", "정상_21",
        "정상_24", "정상_28", "정상_30", "정상_6", "정상_7", "정상_9"}       # 표10과 같은 평가 파일
d = pd.read_csv(ROOT + "/data/touch/features.csv")
sess = d["source_file"].str.replace(".csv", "", regex=False)
te = sess.isin(TEST).values; tr = ~te
y = (d["label"] == "자극").astype(int).values
FEATS = [c for c in d.columns if c not in ("img_name", "source_file", "label")]
out["n"] = dict(total=len(d), feats=len(FEATS), train=int(tr.sum()), test=int(te.sum()),
                test_touch=int(y[te].sum()), test_quiet=int((1 - y[te]).sum()))

# 픽셀 입력: train.py 와 같이 224x224 회색조
def img(row):
    cls = row["label"]
    return np.asarray(Image.open(f"{ROOT}/data/touch/spectrogram/{cls}/{row['img_name']}").convert("L"),
                      dtype=np.float32).ravel() / 255.0
XP = np.stack([img(r) for _, r in d.iterrows()])
XF = d[FEATS].values.astype(float)
groups = sess.values
cv = GroupKFold(n_splits=3)

def evaluate(X, name, pca):
    res = {}
    pre = [("sc", StandardScaler())] + ([("pca", PCA(n_components=30, random_state=42))] if pca else [])
    svm = GridSearchCV(Pipeline(pre + [("svm", SVC())]),
                       {"svm__kernel": ["linear", "rbf"], "svm__C": [0.1, 1, 10], "svm__gamma": ["scale", "auto"]},
                       cv=cv, n_jobs=2)
    rf = GridSearchCV(Pipeline(pre + [("rf", RandomForestClassifier(random_state=42))]),
                      {"rf__n_estimators": [100, 200], "rf__max_depth": [None, 10, 20]}, cv=cv, n_jobs=2)
    for mname, g in (("SVM", svm), ("RandomForest", rf)):
        g.fit(X[tr], y[tr], groups=groups[tr])
        p = g.predict(X[te])
        cm = confusion_matrix(y[te], p, labels=[0, 1])
        res[mname] = dict(params=g.best_params_, correct=int((p == y[te]).sum()),
                          touch_hit=int(cm[1, 1]), quiet_hit=int(cm[0, 0]), cm=cm.tolist())
    out[name] = res
    return rf

rf_feat = evaluate(XF, "features17", pca=False)
evaluate(XP, "pixel224", pca=True)

# 문턱 하나 규칙(학습 데이터에서 찾음)
best = None
for j, c in enumerate(FEATS):
    x = XF[:, j]
    for s in (1, -1):
        for t in np.unique(np.quantile(x[tr], np.linspace(0.01, 0.99, 99))):
            p = s * x > s * t
            ba = (p[tr & (y == 1)].mean() + (~p[tr & (y == 0)]).mean()) / 2
            if best is None or ba > best[0]: best = (ba, c, s, float(t))
_, c, s, t = best
p = s * XF[:, FEATS.index(c)] > s * t
out["threshold"] = dict(feature=c, sign=s, thr=t, correct=int((p[te] == y[te]).sum()),
                        touch_hit=int((p & te & (y == 1)).sum()), quiet_hit=int((~p & te & (y == 0)).sum()))

# 특징 중요도 (특징 입력 RF)
est = rf_feat.best_estimator_
imp = est.named_steps["rf"].feature_importances_
pi = permutation_importance(est, XF[te], y[te], n_repeats=30, random_state=42, scoring="balanced_accuracy")
order = np.argsort(-pi.importances_mean)
out["importance"] = [dict(feat=FEATS[k], rf_imp=round(float(imp[k]), 4),
                          perm_mean=round(float(pi.importances_mean[k]), 4),
                          perm_sd=round(float(pi.importances_std[k]), 4)) for k in order]
out["rf_imp_rank"] = [FEATS[k] for k in np.argsort(-imp)][:8]

# 추론 시간: 10초 창 하나의 전처리+특징+예측 (파이 5, 100회)
import preprocess, spectro_render, feature_extraction
raw = pd.read_csv(ROOT + "/data/touch/raw/자극_1.csv")["voltage"].to_numpy(float)
fs = 250.0; seg = raw[: int(10 * fs)]
svm_feat = None
times = []
from sklearn.svm import SVC as _S
model = Pipeline([("sc", StandardScaler()), ("svm", _S(kernel="linear", C=10))]).fit(XF[tr], y[tr])
for _ in range(100):
    t0 = time.perf_counter()
    filt = preprocess.bandpass_filter(seg, fs)
    Sxx, f, tt = spectro_render.compute_spectrogram(filt, fs, high=20.0)
    feats = feature_extraction.extract_features(filt, Sxx, f, tt, fs=fs, raw_window=seg, baseline=float(seg.mean()))
    model.predict(np.asarray(feats, float).reshape(1, -1))
    times.append(time.perf_counter() - t0)
out["infer_ms"] = dict(median=round(float(np.median(times)) * 1000, 2), p90=round(float(np.percentile(times, 90)) * 1000, 2))

# 표9 재계산: Ⅳ-7 과 같은 기준(0.1초 평균 > 주변 30초 중앙값 + 60 mV, 0.2초 이상)
v = np.loadtxt(ROOT + "/data/plant/live_20260915_070020.csv", delimiter=",", skiprows=1, max_rows=int(2109 * 250))[:, 1]
a = v[: len(v) // 25 * 25].reshape(-1, 25).mean(1)
w = 300; pad = np.pad(a, (w // 2, w // 2), mode="edge")
ref = np.array([np.median(pad[i:i + w]) for i in range(len(a))])
on = a > ref + 60
ev = []; i = 0
while i < len(on):
    if on[i]:
        j = i
        while j < len(on) and on[j]: j += 1
        if j - i >= 2: ev.append((i, j))
        i = j
    else: i += 1
amp = np.array([a[s:e].max() - ref[s] for s, e in ev]); dur = np.array([(e - s) * 0.1 for s, e in ev])
quiet = np.ones(len(a), bool)
for s, e in ev: quiet[max(0, s - 50):e + 100] = False      # 접촉 앞 5초~뒤 10초 제외
out["table9_2109"] = dict(n=len(ev), amp_median=round(float(np.median(amp)), 1),
                          amp_min=round(float(amp.min()), 1), amp_max=round(float(amp.max()), 1),
                          amp_q1=round(float(np.percentile(amp, 25)), 1), amp_q3=round(float(np.percentile(amp, 75)), 1),
                          dur_median=round(float(np.median(dur)), 1),
                          quiet_sd=round(float(a[quiet].std()), 2), base_median=round(float(np.median(a)), 1))
json.dump({"amps_mv": [round(float(x), 1) for x in amp], "base_mv": out["table9_2109"]["base_median"],
           "ev_start_s": [round(s * 0.1, 1) for s, e in ev]},
          open("/tmp/touch_events_2109.json", "w"), ensure_ascii=False)
print(json.dumps(out, ensure_ascii=False, default=str))
