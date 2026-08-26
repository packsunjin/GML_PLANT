# -*- coding: utf-8 -*-
"""Fig. 9 — AD8232 analog filter response vs. plant signal bands.

바로잡은 내용
------------
한때 이 그림은 세 신호가 **모두** 통과대역 밖에 있는 것으로 그렸다. 활동전위의
전파 속도(5.6 cm/min)로 0.03 Hz 를 계산한 결과였는데, 그것은 신호가 전극 사이를
건너가는 데 걸리는 시간이지 전극 하나가 보는 파형의 지속시간이 아니다.

문헌이 보고하는 식물 과도 전기신호의 지속시간은 0.025~2초이고[9], 이를 주파수로
환산하면 약 0.5~40 Hz — 공교롭게도 AD8232 의 통과대역과 거의 같다. 즉 활동전위의
빠른 과도 성분은 **통과한다.** 차단되는 것은 변동전위와 수분 스트레스다.

이 구분이 실제 결과와 맞는다. 자극(활동전위)은 분류가 되었고 수분부족만 끝내
측정되지 않았다.
"""
import sys, os, math
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from figlib import P, paper_svg, axes, esc

W, H = 940, 268
X0, X1 = 62, 782
Y0, Y1 = 20, 200
FH, FL = 0.5, 40.0
EXP_LO, EXP_HI = -5.0, 2.4
DB_LO, DB_HI = -170.0, 16.0


def fx(f):
    return X0 + (math.log10(f) - EXP_LO) / (EXP_HI - EXP_LO) * (X1 - X0)


def fy(db):
    return Y1 - (db - DB_LO) / (DB_HI - DB_LO) * (Y1 - Y0)


def mag_db(f):
    hp = (f / FH) ** 2 / math.sqrt(1 + (f / FH) ** 4)
    lp = 1.0 / math.sqrt(1 + (f / FL) ** 4)
    return 20 * math.log10(max(hp * lp, 1e-12))


b = []

# passband shading
b.append(f'<rect x="{fx(FH):.1f}" y="{Y0}" width="{fx(FL)-fx(FH):.1f}" '
         f'height="{Y1-Y0}" fill="{P["fill"]}"/>')

# response curve
pts = []
for i in range(461):
    e = EXP_LO + (EXP_HI - EXP_LO) * i / 460
    f = 10 ** e
    pts.append(f"{fx(f):.1f},{max(fy(mag_db(f)), Y0):.1f}")
b.append(f'<polyline points="{" ".join(pts)}" fill="none" stroke="{P["ink"]}" '
         f'stroke-width="1.4"/>')

# axes
xt = [(e, fx(10.0 ** e)) for e in range(-5, 3)]
SUP = {-5: "10⁻⁵", -4: "10⁻⁴", -3: "10⁻³", -2: "10⁻²", -1: "10⁻¹",
       0: "10⁰", 1: "10¹", 2: "10²"}
minor = [fx(m * 10.0 ** e) for e in range(-5, 3) for m in range(2, 10)
         if X0 <= fx(m * 10.0 ** e) <= X1]
yt = [(d, fy(d)) for d in (0, -40, -80, -120, -160)]
b.append(axes(X0, Y0, X1, Y1, xt, yt, "Frequency (Hz)", "Gain (dB)",
              xfmt=lambda e: SUP[e], yfmt=str, minor=minor))

# signal bands, drawn as spans below the axis
BANDS = [
    ("Water stress",  1e-5, 1e-3, False),   # hours – days
    ("VP",            1e-3, 1e-2, False),   # minutes – tens of minutes [13]
    ("AP transient",  0.5,  40.0, True),    # 0.025–2 s duration [9]
]
BY = Y1 + 66
for i, (name, lo, hi, passes) in enumerate(BANDS):
    y = BY + i * 17
    col = P["ink"] if passes else P["accent"]
    x0, x1 = fx(lo), fx(hi)
    dash = "" if passes else ' stroke-dasharray="3 2"'
    b.append(f'<line x1="{x0:.1f}" y1="{y:.1f}" x2="{x1:.1f}" y2="{y:.1f}" '
             f'stroke="{col}" stroke-width="{3.2 if passes else 2.4}"{dash}/>')
    for xx in (x0, x1):
        b.append(f'<line x1="{xx:.1f}" y1="{y-3.5:.1f}" x2="{xx:.1f}" y2="{y+3.5:.1f}" '
                 f'stroke="{col}" stroke-width="1"/>')
    b.append(f'<text x="{x1+7:.1f}" y="{y+3.3:.1f}" font-size="8.5" fill="{col}">'
             f'{esc(name)}</text>')

b.append(f'<text x="{X0}" y="{BY-16:.1f}" font-size="8.5" fill="{P["gray"]}">'
         f'signal bands   (solid = within passband, dashed = rejected)</text>')

open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "FIG_BAND.svg"),
     "w", encoding="utf-8").write(
    paper_svg(W, H, "".join(b), "AD8232 filter response and plant signal bands"))
print("FIG_BAND.svg")
