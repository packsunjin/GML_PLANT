"""build_touch_dataset.py — 실시간 기록에서 손 접촉 구간을 찾아 학습용 데이터셋을 만든다.

    venv/bin/python3 src/build_touch_dataset.py data/plant/live_20260915_070020.csv

결과: data/touch/raw/자극_N.csv, 정상_N.csv (+ .meta.json)
      → 그대로 preprocess.py / train.py 에 넣는다(--raw_dir data/touch/raw).

⚠️ 이 데이터셋의 "자극"은 **손 접촉**이다. 식물의 자극 반응이 아니다.
   손이 닿으면 약 280 mV 계단이 생겨 식물 반응(문헌 60–70 mV)보다 크고, 핀·선
   움직임과도 같은 모양이라 둘을 가를 수 없다(CLAUDE.md 0절 ⑰). 그래서 이것으로
   학습한 모델은 "누가 식물을 만지고 있나"를 판별한다. 보고서에는 "접촉 감지"로 쓴다.

라벨 정하는 법
- 0.1초 평균이 주변 30초 중앙값보다 THRESH_MV 넘게 올라간 순간을 접촉으로 본다.
  (기준선이 분 단위로 수십 mV 표류해서 전체 중앙값 대신 주변 중앙값을 쓴다)
- 접촉이 GAP_S 안에 이어지면 한 덩어리로 묶는다.
- 접촉 구간: 덩어리 시작 PRE_S 전 ~ 끝 POST_S 후 → 자극_N.csv
- 비접촉 구간: 접촉 덩어리에서 PRE_S 이상, 손 뗀 뒤 RECOVER_S 이상 떨어진 곳.
  손을 떼면 기준선 아래로 떨어졌다가 수 초에 걸쳐 돌아오므로 그 꼬리는 뺀다.
  최대 CHUNK_S 씩 잘라 정상_N.csv. 파일 하나가 세션 하나라서, 학습·평가가
  같은 구간의 겹친 창을 나눠 갖지 않는다.
"""
import os, sys, json
import numpy as np

FS = 250.0
THRESH_MV = 60.0
LOCAL_WIN_S = 30.0
GAP_S = 8.0
PRE_S = 5.0
POST_S = 5.0
RECOVER_S = 10.0
CHUNK_S = 60.0
MIN_LEN_S = 10.0          # preprocess 창이 10초라 이보다 짧으면 창이 1개도 안 나온다


def main(src):
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    out = os.path.join(root, "data", "touch", "raw")
    os.makedirs(out, exist_ok=True)
    for f in os.listdir(out):
        os.remove(os.path.join(out, f))

    d = np.loadtxt(src, delimiter=",", skiprows=1)
    v = d[:, 1]                                         # mV
    n = len(v)
    m = int(FS * 0.1)
    a = v[: n // m * m].reshape(-1, m).mean(axis=1)     # 0.1초 평균
    w = int(LOCAL_WIN_S / 0.1)
    pad = np.pad(a, (w // 2, w // 2), mode="edge")
    local = np.array([np.median(pad[i:i + w]) for i in range(len(a))])
    on = a > local + THRESH_MV

    # 접촉 사건 (켜짐 시작, 꺼짐 끝) — 0.1초 단위 인덱스
    ev = []
    i = 0
    while i < len(on):
        if on[i]:
            j = i
            while j < len(on) and on[j]:
                j += 1
            if j - i >= 2:                              # 0.2초 이상
                ev.append([i, j])
            i = j
        else:
            i += 1
    # 덩어리로 묶기
    cl = []
    for s, e in ev:
        if cl and s - cl[-1][1] <= GAP_S / 0.1:
            cl[-1][1] = e
        else:
            cl.append([s, e])

    T = len(a) * 0.1
    seg_touch = [(max(0.0, s * 0.1 - PRE_S), min(T, e * 0.1 + POST_S)) for s, e in cl]
    blocked = [(max(0.0, s * 0.1 - PRE_S), min(T, e * 0.1 + RECOVER_S)) for s, e in cl]
    seg_quiet = []
    cur = 0.0
    for b0, b1 in sorted(blocked) + [(T, T)]:
        if b0 - cur >= MIN_LEN_S:
            k = cur
            while b0 - k >= MIN_LEN_S:
                seg_quiet.append((k, min(b0, k + CHUNK_S)))
                k += CHUNK_S
        cur = max(cur, b1)

    def write(state, segs):
        kept = 0
        for idx, (t0, t1) in enumerate(segs, 1):
            if t1 - t0 < MIN_LEN_S:
                continue
            i0, i1 = int(t0 * FS), int(t1 * FS)
            name = f"{state}_{idx}.csv"
            ts = np.arange(i1 - i0) / FS
            np.savetxt(os.path.join(out, name), np.column_stack([ts, v[i0:i1] / 1000.0]),
                       delimiter=",", header="timestamp_sec,voltage", comments="", fmt="%.6f")
            meta = {"mode": "hardware", "kind": "touch_dataset",
                    "label": "접촉" if state == "자극" else "비접촉",
                    "label_note": "이 데이터셋의 '자극'은 손 접촉이다. 식물 자극 반응이 아니다(0절 ⑰)",
                    "source": os.path.basename(src), "from_sec": round(t0, 1), "to_sec": round(t1, 1),
                    "frontend": {"name": "dc", "pga": 2, "gain": 1.0, "dc_coupled": True,
                                 "input_span_mv": 2048.0}}
            with open(os.path.join(out, name[:-4] + ".meta.json"), "w", encoding="utf-8") as f:
                json.dump(meta, f, ensure_ascii=False, indent=1)
            kept += 1
        return kept

    nt = write("자극", seg_touch)
    nq = write("정상", seg_quiet)
    print(f"원본 {os.path.basename(src)} {n / FS:.0f}초")
    print(f"접촉 사건 {len(ev)}개 → 덩어리 {len(cl)}개 → 접촉 파일 {nt}개")
    print(f"비접촉 파일 {nq}개 (최대 {CHUNK_S:.0f}초씩)")
    print(f"저장: {out}")


if __name__ == "__main__":
    main(sys.argv[1])
