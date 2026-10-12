# -*- coding: utf-8 -*-
"""보고서 그림 전체를 실측 데이터에서 바로 PNG 로 만든다 (matplotlib, 400 dpi).

    cd ~/GML_PLANT/report && ../venv/bin/python3 figs.py            # 파이에서
    ../venv/bin/python3 figs.py --ad8232 /tmp/ad8232                # 그림4 도 다시 만들 때

데이터가 파이에만 있으므로 파이에서 돌린다. 그림 라벨은 한글(서식2: 그림 안 내용·캡션 모두 한글 원칙).
값을 손으로 옮기지 않는다 — 본문 숫자와 그림이 어긋나지 않게 원본 CSV 에서 다시 계산한다.

FIG_AD8232.png 는 측정자가 준 AD8232 스펙트로그램 28장(2026-06-08, 원본 CSV 없음)을
이어 붙인 것이다. 원본 그림은 저장소에 두지 않으므로(사용자 요청) --ad8232 로 폴더를
줄 때만 다시 만들고, 평소에는 있는 PNG 를 그대로 쓴다.
"""
import os, sys, glob, json
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch, Rectangle
from scipy import signal

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DPI = 400
# 한글: Noto Sans CJK 의 .ttc 를 matplotlib 은 첫 글꼴 이름(JP)으로 등록한다. 한글 글리프는 같다.
plt.rcParams.update({"font.family": "Noto Sans CJK JP", "axes.unicode_minus": False, "font.size": 8, "axes.linewidth": 0.6,
                     "xtick.major.width": 0.6, "ytick.major.width": 0.6, "axes.titlesize": 8.5})
INK, GRAY, GREEN, RED, BLUE = "#1b1b1b", "#6b6b6b", "#2e7d4f", "#c0392b", "#1f5f8b"
FS = 250.0
LIVE = os.path.join(ROOT, "data", "plant", "live_20260915_070020.csv")
LIVE_SEC = 2109.0          # 표9·Ⅳ-7 과 같은 구간(데이터셋을 만들 때까지 저장된 길이)


def save(fig, name):
    p = os.path.join(HERE, name)
    fig.savefig(p, dpi=DPI, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    print("  ", name)


def load_bench(name):
    d = np.loadtxt(os.path.join(ROOT, "data", "bench", name), delimiter=",", skiprows=1)
    return d[:, 1] * 1000.0                                     # mV


def minute_means(v):
    n = int(FS * 60)
    return v[: len(v) // n * n].reshape(-1, n).mean(axis=1)


def load_live(sec=LIVE_SEC):
    n = int(sec * FS)
    v = np.loadtxt(LIVE, delimiter=",", skiprows=1, max_rows=n)[:, 1]
    return v                                                    # mV


# ── 그림1: 측정 시스템 ────────────────────────────────
def fig_sys():
    fig, ax = plt.subplots(figsize=(7.2, 2.5))
    ax.set_xlim(-1, 100); ax.set_ylim(0, 34); ax.axis("off")

    def box(x, y, w, h, title, sub="", edge=INK):
        ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.3,rounding_size=0.8",
                                    fc="white", ec=edge, lw=0.9))
        ax.text(x + w / 2, y + h - 2.6, title, ha="center", va="top", fontsize=8.2, weight="bold")
        if sub:
            ax.text(x + w / 2, y + h - 6.4, sub, ha="center", va="top", fontsize=5.9, color=GRAY,
                    linespacing=1.35)

    def arrow(x0, y0, x1, y1):
        ax.annotate("", (x1, y1), (x0, y0), arrowprops=dict(arrowstyle="-|>", lw=0.8, color=INK))

    box(0, 17, 19, 15, "토마토 줄기", "전극1(위) · 전극2(아래)\n점퍼선 핀, 간격 3 cm\n줄기 관통")
    box(25, 17, 18, 15, "MCP6002 (1개)", "증폭기 2개 내장\n각각 전압 폴로어(이득 1배)\n고역통과 필터 없음")
    box(49, 17, 21, 15, "ADS1115", "16비트, A0 − A1 차동\n회로 점검: ±0.256 V\n전극 측정: ±2.048 V")
    box(76, 17, 23, 15, "라즈베리파이 5", "초당 250회 기록\n실시간 스펙트로그램\nSVM · 랜덤포레스트", edge=BLUE)
    arrow(19.6, 24.5, 24.4, 24.5); arrow(43.6, 24.5, 48.4, 24.5); arrow(70.6, 24.5, 75.4, 24.5)
    ax.text(72.5, 25.5, "I2C", ha="center", fontsize=6.5, color=GRAY)

    box(1, 1, 30, 12, "기준 전압 분압", "3.3 V – 10 kΩ – 10 kΩ – 접지\n중점 1.65 V → 흙 기준 전극")
    arrow(16, 13.3, 9.5, 16.4)
    ax.text(33, 7, "0–3.3 V 단전원 회로에서 식물 전위가 1.65 V를\n"
                   "중심으로 위아래로 움직일 수 있게 한다.", fontsize=6.6, color=GRAY, va="center")
    save(fig, "FIG_SYS.png")


# ── 그림2: 신호의 주파수 위치와 필터 대역 ──────────────
def fig_band():
    fig, ax = plt.subplots(figsize=(7.2, 2.7))
    ax.set_xscale("log"); ax.set_xlim(1e-5, 3e3); ax.set_ylim(-0.4, 7.2)
    ax.set_yticks([]); ax.set_xlabel("주파수 (Hz, 로그 눈금)")
    for s in ("left", "right", "top"):
        ax.spines[s].set_visible(False)
    ax.grid(axis="x", which="major", lw=0.4, color="#dddddd")

    def bar(f0, f1, y, label, color, filled=True):
        ax.add_patch(Rectangle((f0, y), f1 - f0, 0.7, fc=color if filled else "none",
                               ec=color, lw=1.0, alpha=0.35 if filled else 1))
        if filled:
            ax.add_patch(Rectangle((f0, y), f1 - f0, 0.7, fc="none", ec=color, lw=1.0))
        if label:
            ax.text(f1 * 1.35, y + 0.35, label, ha="left", va="center", fontsize=6.8)

    ax.text(1.2e-5, 6.75, "신호 (지속시간 T → 주파수 약 1/T)", fontsize=6.8, color=GRAY)
    bar(1e-5, 1e-4, 5.8, "수분 스트레스 (수 시간~수 일)", "#b5651d")
    bar(3e-4, 3e-3, 4.9, "변동전위 (최대 수십 분)", "#b5651d")
    bar(1e-2, 1e-1, 4.0, "활동전위, 일반 식물 (수십 초)", GREEN)
    bar(5e-2, 2e-1, 3.1, "활동전위, 접촉 자극 (약 10초)", GREEN)
    bar(57, 63, 4.9, "상용전원 60 Hz", RED)
    ax.text(1.2e-5, 2.45, "필터가 남기는 범위", fontsize=6.8, color=GRAY)
    bar(1e-2, 1, 1.6, "제안 대역 (0.01–1 Hz)", GREEN, filled=False)
    bar(0.5, 20, 0.7, "기존 전처리 (0.5–20 Hz)", BLUE, filled=False)
    bar(0.5, 40, -0.2, "AD8232 (0.5–40 Hz)", BLUE, filled=False)
    save(fig, "FIG_BAND.png")


# ── 그림3: 1단계·2단계 ───────────────────────────────
def fig_stage():
    s1 = minute_means(load_bench("dc_shorted_30min_v2.csv"))
    # 2단계는 2026-10-12 재측정(60분). 10-07 2시간 기록은 절전 때문에 쓰지 않는다(CLAUDE.md 0절)
    s2 = minute_means(load_bench("stage2_repeat_1012/정상.csv"))
    fig, (a, b) = plt.subplots(1, 2, figsize=(7.2, 2.5), gridspec_kw={"width_ratios": [1, 1.6]})
    a.plot(np.arange(len(s1)) + 0.5, s1, color=BLUE, lw=1.1)
    a.set_title("(가) 1단계: 입력 단락, 30분", loc="left")
    a.set_xlabel("시간 (분)"); a.set_ylabel("전압 (mV, 1분 평균)")
    a.ticklabel_format(axis="y", useOffset=False)
    b.plot(np.arange(len(s2)) + 0.5, s2, color=GREEN, lw=1.1)
    b.axhspan(s2[0] - 5, s2[0] + 5, color=RED, alpha=0.10, lw=0)
    b.text(31, s2[0] - 1, "합격 범위 (시작값 ±5 mV)", color=RED, fontsize=6.6)
    b.set_title("(나) 2단계: 수돗물 속 점퍼선 핀, 60분", loc="left")
    b.set_xlabel("시간 (분)"); b.set_ylabel("전압 (mV, 1분 평균)")
    for x in (a, b):
        x.grid(lw=0.3, color="#e3e3e3")
    fig.tight_layout(w_pad=2.0)
    save(fig, "FIG_STAGE.png")


# ── 그림4: AD8232 스펙트로그램 28장 ────────────────────
def fig_ad8232(src):
    from PIL import Image
    files = sorted(glob.glob(os.path.join(src, "stimulus_*.png")))
    if not files:
        print("   AD8232 원본 없음 — FIG_AD8232.png 는 그대로 둔다"); return
    fig, axs = plt.subplots(2, 14, figsize=(7.2, 1.35))
    for k, ax in enumerate(axs.flat):
        ax.axis("off")
        if k < len(files):
            ax.imshow(Image.open(files[k]).convert("RGB")); ax.set_title(str(k + 1), fontsize=5.5, pad=1.5)
    fig.subplots_adjust(wspace=0.08, hspace=0.25, left=0, right=1, top=0.9, bottom=0)
    save(fig, "FIG_AD8232.png")


# ── 그림5: 손 접촉 ──────────────────────────────────
def fig_touch():
    v = load_live()
    m = int(FS * 0.1)
    a10 = v[: len(v) // m * m].reshape(-1, m).mean(axis=1)
    t10 = np.arange(len(a10)) * 0.1
    amps = json.load(open(os.path.join(HERE, "touch_events.json"), encoding="utf-8"))
    fig = plt.figure(figsize=(7.2, 4.3))
    gs = fig.add_gridspec(2, 2, height_ratios=[1, 1], width_ratios=[1.4, 1], hspace=0.55, wspace=0.28)
    a = fig.add_subplot(gs[0, :])
    a.plot(t10 / 60, a10, color=GREEN, lw=0.5)
    a.axhline(amps["base_mv"], color=GRAY, ls="--", lw=0.6)
    a.set_title(f"(가) 식물 기록 0–{LIVE_SEC/60:.1f}분 (0.1초 평균) — 솟은 곳이 손 접촉", loc="left")
    a.set_xlabel("시간 (분)"); a.set_ylabel("전압 (mV)")
    # (b) 한 번 접촉 확대 — 1182.1 s 사건
    t0 = 1182.1
    sel = (t10 > t0 - 8) & (t10 < t0 + 9)
    b = fig.add_subplot(gs[1, 0])
    b.plot(t10[sel] - t0, a10[sel], color=GREEN, lw=1.0)
    b.axvspan(0, 0.7, color=RED, alpha=0.15, lw=0)
    b.set_title("(나) 접촉 한 번 확대 (색칠 = 손이 닿은 동안)", loc="left")
    b.set_xlabel("접촉 시점부터 (초)"); b.set_ylabel("전압 (mV)")
    c = fig.add_subplot(gs[1, 1])
    c.hist(amps["amps_mv"], bins=np.arange(0, 425, 25), color=GREEN, ec="white", lw=0.6)
    c.axvspan(26.8, 39.8, color=BLUE, alpha=0.25, lw=0)
    c.text(108, c.get_ylim()[1] * 0.80, "← 문헌 접촉\n   활동전위", fontsize=6.2, color=BLUE)
    c.set_title(f"(다) 계단 높이, {len(amps['amps_mv'])}건", loc="left")
    c.set_xlabel("접촉 시 상승 폭 (mV)"); c.set_ylabel("횟수")
    for x in (a, b, c):
        x.grid(lw=0.3, color="#e3e3e3")
    save(fig, "FIG_TOUCH.png")


# ── 그림6: DC 회로 스펙트로그램 ───────────────────────
def fig_spec():
    from PIL import Image
    v = load_live()
    sos = signal.butter(4, [0.5, 20], btype="bandpass", fs=FS, output="sos")
    t0, t1 = 1255.0, 1345.0                         # 접촉이 이어진 90초 (그림5(a) 21분 부근)
    seg = v[int((t0 - 30) * FS): int((t1 + 30) * FS)]
    f_seg = signal.sosfiltfilt(sos, seg)[int(30 * FS): -int(30 * FS)]
    raw = seg[int(30 * FS): -int(30 * FS)]
    f, tt, S = signal.spectrogram(f_seg, fs=FS, nperseg=512, noverlap=448)
    keep = f <= 20
    fig = plt.figure(figsize=(7.2, 4.6))
    gs = fig.add_gridspec(3, 14, height_ratios=[0.7, 1.3, 0.95], hspace=0.65, wspace=0.08)
    a = fig.add_subplot(gs[0, :])
    m = int(FS * 0.1)
    a10 = raw[: len(raw) // m * m].reshape(-1, m).mean(axis=1)
    a.plot(np.arange(len(a10)) * 0.1, a10, color=GREEN, lw=0.7)
    a.set_xlim(0, t1 - t0); a.set_ylabel("mV"); a.set_xticklabels([])
    a.set_title(f"(가) 전압, 식물 기록 {t0/60:.1f}–{t1/60:.1f}분", loc="left")
    a.grid(lw=0.3, color="#e3e3e3")
    b = fig.add_subplot(gs[1, :])
    b.pcolormesh(tt, f[keep], 10 * np.log10(S[keep] + 1e-12), shading="auto", cmap="viridis")
    b.set_xlim(0, t1 - t0); b.set_xlabel("시간 (초)"); b.set_ylabel("주파수 (Hz)")
    b.set_title("(나) 0.5–20 Hz 대역통과 후 스펙트로그램 (분류기 입력 대역)", loc="left")
    tiles = {k: sorted(glob.glob(os.path.join(ROOT, "data", "touch", "spectrogram", k, "*.png")))
             for k in ("자극", "정상")}
    pick = []
    for k in ("자극", "정상"):
        fl = tiles[k]
        idx = np.linspace(0, len(fl) - 1, 7).round().astype(int) if fl else []
        pick += [(k, fl[i]) for i in idx]
    for j, (k, p) in enumerate(pick):
        ax = fig.add_subplot(gs[2, j]); ax.axis("off")
        ax.imshow(Image.open(p).convert("RGB"))
    fig.text(0.125, 0.285, "(다) 분류기에 넣는 10초 창 — 왼쪽 7장 접촉, 오른쪽 7장 비접촉",
             fontsize=8.5)
    save(fig, "FIG_SPEC.png")


if __name__ == "__main__":
    src = sys.argv[sys.argv.index("--ad8232") + 1] if "--ad8232" in sys.argv else None
    fig_sys(); fig_band(); fig_stage()
    if src:
        fig_ad8232(src)
    fig_touch(); fig_spec()
    print("완료")
