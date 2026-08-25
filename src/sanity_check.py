"""
sanity_check.py
===============
분석 절차가 '구분할 정보가 없는 입력'에서 우연 이상의 정확도를 만들어내지 않는지 확인한다.

세 상태를 통계적으로 완전히 동일하게 만든 신호(같은 분산의 백색잡음 + 같은 전원 성분)를
생성해 preprocess -> train 을 그대로 돌린다. 3분류이므로 정확도가 33% 부근이어야 정상이다.

이 검사가 필요한 이유:
  · 음성 대조군(알루미늄 호일 등) 실험이 의미를 가지려면, 대조군에서 낮은 정확도가
    나올 때 그것이 '신호가 없어서'임이 보장되어야 한다. 절차가 입력과 무관하게 높은
    정확도를 만들어내면 대조 자체가 성립하지 않는다.
  · 슬라이딩 창은 서로 겹치므로, train/test 경계에서 겹치는 창을 충분히 버리지 않으면
    같은 신호를 학습과 평가에 함께 쓰게 되어 정확도가 부풀려진다. 실제로 창을 2초에서
    10초로 바꿨을 때 이 문제가 있었고, 이 검사가 그런 회귀를 잡아낸다.

실행:
    python sanity_check.py
"""

import os
import csv
import sys
import shutil
import tempfile
import subprocess

import numpy as np

STATES = ["정상", "수분부족", "자극"]
DURATION_SEC = 120.0
SAMPLE_RATE = 250.0
# 3분류 우연 수준은 33%. 잡음이라 흔들리므로 여유를 두고, 학습 목표치(70%)와는
# 확실히 구분되는 선으로 잡는다.
MAX_CHANCE_ACC = 0.55


def make_identical_states(raw_dir, seed=7):
    """세 상태를 구분할 정보가 전혀 없는 CSV 3개를 만든다."""
    os.makedirs(raw_dir, exist_ok=True)
    rng = np.random.default_rng(seed)
    n = int(DURATION_SEC * SAMPLE_RATE)
    t = np.arange(n) / SAMPLE_RATE
    for st in STATES:
        # 상태마다 난수는 다르지만 '분포'는 같다 -> 구분할 근거가 없다
        v = 0.004 * rng.standard_normal(n) + 0.002 * np.sin(2 * np.pi * 60 * t)   # 국내 상용전원 60Hz
        with open(os.path.join(raw_dir, f"{st}.csv"), "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["timestamp_sec", "voltage"])
            w.writerows(zip(np.round(t, 6), v))


def check_split_no_overlap():
    """train/test 분할이 시간적으로 겹치지 않는지 직접 확인한다.

    우연 수준 검사(아래)는 이 문제를 못 잡는다. 잡음 데이터에는 외울 구조가 없어서
    겹쳐도 정확도가 오르지 않기 때문이다. 그래서 겹침 자체를 눈으로 센다.

    창 W초를 S초씩 밀면 거리가 W/S 미만인 창끼리 겹치므로, 학습 구간의 마지막 창과
    평가 구간의 첫 창은 시작점이 최소 W/S 만큼 떨어져 있어야 한다."""
    import train as T
    ok = True
    for window_sec, step_sec in [(2.0, 1.0), (10.0, 2.0), (10.0, 1.0), (15.0, 3.0)]:
        fs = SAMPLE_RATE
        step = int(step_sec * fs)
        n_per_state = 60
        offs, groups, ys = [], [], []
        for gi, st in enumerate(STATES):
            for k in range(n_per_state):
                offs.append(k * step); groups.append(st); ys.append(gi)
        offs = np.array(offs); groups = np.array(groups); ys = np.array(ys)
        X = offs.reshape(-1, 1).astype(float)      # 값 = 창 시작 오프셋
        guard = T.overlap_guard(window_sec, step_sec)
        Xtr, Xte, _, _ = T.chronological_group_split(X, ys, groups, offs,
                                                     test_size=0.3, guard=guard)
        need = int(window_sec * fs)                # 이만큼 떨어져야 겹치지 않는다
        tr_set = set(Xtr.ravel())
        te_set = set(Xte.ravel())
        worst = None
        for st in STATES:
            # 분할은 상태(그룹)별로 이뤄지므로 상태마다 따로 경계를 본다
            tr_g = [v for v, g in zip(X.ravel(), groups) if g == st and v in tr_set]
            te_g = [v for v, g in zip(X.ravel(), groups) if g == st and v in te_set]
            if not tr_g or not te_g:
                continue
            gap = min(te_g) - max(tr_g)
            worst = gap if worst is None else min(worst, gap)
        mark = "OK " if worst is not None and worst >= need else "NG "
        if worst is None or worst < need:
            ok = False
        print(f"  [{mark}] 창 {window_sec}초/스텝 {step_sec}초 · guard {guard}창 → "
              f"경계 간격 {worst}샘플 (필요 {need}샘플)")
    return ok


def _run(cmd):
    p = subprocess.run(cmd, capture_output=True, text=True, cwd=os.path.dirname(os.path.abspath(__file__)))
    if p.returncode != 0:
        print(p.stdout[-2000:])
        print(p.stderr[-2000:])
        raise SystemExit(f"❌ 실행 실패: {' '.join(cmd)}")
    return p.stdout


def main():
    print("[sanity] ① 분할 겹침 검사 — train/test 창이 시간적으로 겹치지 않는가")
    split_ok = check_split_no_overlap()
    if not split_ok:
        print("❌ 학습 구간과 평가 구간의 창이 겹칩니다. 정확도가 부풀려집니다.")
        print("   train.py 의 overlap_guard() 를 확인하세요 (필요 개수 = ceil(창/스텝) - 1).")
        return 1
    print("✅ 겹치지 않습니다.\n")

    print("[sanity] ② 우연 수준 검사 — 구분할 정보가 없는 입력에서 정확도가 오르지 않는가")
    work = tempfile.mkdtemp(prefix="gml_sanity_")
    try:
        raw = os.path.join(work, "raw")
        print("[sanity] 세 상태가 통계적으로 동일한 신호 생성 중…")
        make_identical_states(raw)

        print("[sanity] 전처리…")
        _run([sys.executable, "preprocess.py",
              "--raw_dir", raw,
              "--out_dir", os.path.join(work, "spec"),
              "--features_csv", os.path.join(work, "features.csv")])

        print("[sanity] 학습…")
        out = _run([sys.executable, "train.py", "--task", "3종",
                    "--spectrogram_dir", os.path.join(work, "spec"),
                    "--features_csv", os.path.join(work, "features.csv"),
                    "--models_dir", os.path.join(work, "models")])

        accs = [float(l.split(":")[1]) for l in out.splitlines() if l.startswith("Accuracy :")]
        if not accs:
            raise SystemExit("❌ 학습 출력에서 정확도를 찾지 못했습니다.")
        worst = max(accs)
        print()
        for a in accs:
            print(f"  정확도 {a:.4f}")
        print(f"\n[sanity] 최고 정확도 {worst:.4f} (우연 수준 0.3333, 허용 상한 {MAX_CHANCE_ACC})")
        if worst > MAX_CHANCE_ACC:
            print("❌ 구분할 정보가 없는 입력에서 우연보다 크게 높은 정확도가 나왔습니다.")
            print("   train/test 분할이 시간적으로 겹치고 있을 가능성이 큽니다.")
            print("   train.py 의 overlap_guard() 와 preprocess 의 창/스텝 설정을 확인하세요.")
            return 1
        print("✅ 정상 — 절차가 잡음에서 정확도를 만들어내지 않습니다.")
        return 0
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
