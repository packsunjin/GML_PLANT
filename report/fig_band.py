# -*- coding: utf-8 -*-
"""그림9 — AD8232 아날로그 필터의 주파수 응답과 식물 전기신호가 놓인 대역.

막대로 '차단됨'이라고 적는 대신 전달함수를 직접 그린다. 몇 dB 깎이는지가
숫자로 읽히고, 세 신호가 통과대역 밖이라는 본문 주장이 곡선 위에서 확인된다.

전달함수: 2극 고역통과(fc=0.5Hz) x 2극 저역통과(fc=40Hz), 버터워스 기준
    |H(f)| = (f/f_h)^2 / sqrt(1+(f/f_h)^4)  ·  1/sqrt(1+(f/f_l)^4)
"""
import sys, os, math
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from figlib import P, paper_svg, axes, esc

W, H = 900, 330
X0, X1 = 74, 660
Y0, Y1 = 24, 236
FH, FL = 0.5, 40.0
EXP_LO, EXP_HI = -5.0, 2.4
DB_LO, DB_HI = -180.0, 14.0


def fx(f):
    return X0 + (math.log10(f) - EXP_LO) / (EXP_HI - EXP_LO) * (X1 - X0)


def fy(db):
    return Y1 - (db - DB_LO) / (DB_HI - DB_LO) * (Y1 - Y0)


def mag_db(f):
    hp = (f / FH) ** 2 / math.sqrt(1 + (f / FH) ** 4)
    lp = 1.0 / math.sqrt(1 + (f / FL) ** 4)
    return 20 * math.log10(max(hp * lp, 1e-12))


b = []

# 통과대역 음영
b.append(f'<rect x="{fx(FH):.1f}" y="{Y0}" width="{fx(FL)-fx(FH):.1f}" '
         f'height="{Y1-Y0}" fill="{P["fill"]}"/>')
b.append(f'<text x="{(fx(FH)+fx(FL))/2:.1f}" y="{Y0+12}" font-size="9" '
         f'fill="{P["gray"]}" text-anchor="middle">통과대역 0.5–40 Hz</text>')

# -3 dB 보조선
b.append(f'<line x1="{X0}" y1="{fy(-3):.1f}" x2="{X1}" y2="{fy(-3):.1f}" '
         f'stroke="{P["light"]}" stroke-width="0.7" stroke-dasharray="3 3"/>')
b.append(f'<text x="{X1-2}" y="{fy(-3)-4:.1f}" font-size="8.5" fill="{P["gray"]}" '
         f'text-anchor="end">−3 dB</text>')

# 전달함수 곡선
pts = []
n = 460
for i in range(n + 1):
    e = EXP_LO + (EXP_HI - EXP_LO) * i / n
    f = 10 ** e
    pts.append(f"{fx(f):.1f},{max(fy(mag_db(f)), Y0):.1f}")
b.append(f'<polyline points="{" ".join(pts)}" fill="none" stroke="{P["ink"]}" '
         f'stroke-width="1.5"/>')

# 세 신호가 놓인 자리
MARKS = [("수분 스트레스", 1e-4), ("변동전위 (VP)", 1e-3), ("활동전위 (AP)", 3e-2)]
for name, f in MARKS:
    db = mag_db(f)
    x, y = fx(f), fy(db)
    b.append(f'<line x1="{x:.1f}" y1="{y:.1f}" x2="{x:.1f}" y2="{Y1}" '
             f'stroke="{P["accent"]}" stroke-width="0.8" stroke-dasharray="2 3"/>')
    b.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="3.2" fill="#fff" '
             f'stroke="{P["accent"]}" stroke-width="1.4"/>')

# 60Hz 잡음
x60, y60 = fx(60.0), fy(mag_db(60.0))
b.append(f'<circle cx="{x60:.1f}" cy="{y60:.1f}" r="3.2" fill="{P["accent2"]}"/>')

# 축
xt = [(e, fx(10.0 ** e)) for e in range(-5, 3)]
SUP = {-5: "10⁻⁵", -4: "10⁻⁴", -3: "10⁻³", -2: "10⁻²", -1: "10⁻¹",
       0: "1", 1: "10", 2: "10²"}
minor = [fx(m * 10.0 ** e) for e in range(-5, 3) for m in range(2, 10)
         if X0 <= fx(m * 10.0 ** e) <= X1]
yt = [(d, fy(d)) for d in (0, -40, -80, -120, -160)]
b.append(axes(X0, Y0, X1, Y1, xt, yt, "주파수 (Hz)", "이득 (dB)",
              xfmt=lambda e: SUP[e], yfmt=str, minor=minor))

# 범례
lx, ly = X1 + 24, Y0 + 24
b.append(f'<text x="{lx}" y="{ly}" font-size="9.5" font-weight="700">신호별 감쇠량</text>')
rows = [(n_, f_, mag_db(f_), P["accent"], False) for n_, f_ in MARKS]
rows.append(("60 Hz 유도잡음", 60.0, mag_db(60.0), P["accent2"], True))
for i, (name, f, db, col, filled) in enumerate(rows):
    yy = ly + 18 + i * 26
    if filled:
        b.append(f'<circle cx="{lx+5}" cy="{yy-3.5}" r="3.2" fill="{col}"/>')
    else:
        b.append(f'<circle cx="{lx+5}" cy="{yy-3.5}" r="3.2" fill="#fff" '
                 f'stroke="{col}" stroke-width="1.4"/>')
    b.append(f'<text x="{lx+15}" y="{yy}" font-size="9">{esc(name)}</text>')
    b.append(f'<text x="{lx+15}" y="{yy+12}" font-size="8.5" fill="{P["gray"]}">'
             f'{esc(f"{f:g} Hz  ·  {db:+.0f} dB")}</text>')

open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "FIG_BAND.svg"),
     "w", encoding="utf-8").write(
    paper_svg(W, H, "".join(b), "AD8232 아날로그 필터의 주파수 응답"))
print("FIG_BAND.svg")
