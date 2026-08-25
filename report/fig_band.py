# -*- coding: utf-8 -*-
"""Fig. 9 — AD8232 analog filter response vs. plant signal bands."""
import sys, os, math
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from figlib import P, paper_svg, axes, esc

W, H = 760, 300
X0, X1 = 62, 700
Y0, Y1 = 20, 214
FH, FL = 0.5, 40.0
EXP_LO, EXP_HI = -5.0, 2.4
DB_LO, DB_HI = -180.0, 16.0


def fx(f):
    return X0 + (math.log10(f) - EXP_LO) / (EXP_HI - EXP_LO) * (X1 - X0)


def fy(db):
    return Y1 - (db - DB_LO) / (DB_HI - DB_LO) * (Y1 - Y0)


def mag_db(f):
    hp = (f / FH) ** 2 / math.sqrt(1 + (f / FH) ** 4)
    lp = 1.0 / math.sqrt(1 + (f / FL) ** 4)
    return 20 * math.log10(max(hp * lp, 1e-12))


b = []

# passband
b.append(f'<rect x="{fx(FH):.1f}" y="{Y0}" width="{fx(FL)-fx(FH):.1f}" '
         f'height="{Y1-Y0}" fill="{P["fill"]}"/>')
b.append(f'<text x="{(fx(FH)+fx(FL))/2:.1f}" y="{Y0+11}" font-size="8.5" '
         f'fill="{P["gray"]}" text-anchor="middle">passband</text>')

# response
pts = []
for i in range(461):
    e = EXP_LO + (EXP_HI - EXP_LO) * i / 460
    f = 10 ** e
    pts.append(f"{fx(f):.1f},{max(fy(mag_db(f)), Y0):.1f}")
b.append(f'<polyline points="{" ".join(pts)}" fill="none" stroke="{P["ink"]}" '
         f'stroke-width="1.4"/>')

# signal markers, labelled directly
MARKS = [("water stress", 1e-4, 6, "start"),
         ("VP", 1e-3, 6, "start"),
         ("AP", 3e-2, 6, "start"),
         ("50/60 Hz", 60.0, -8, "end")]
for name, f, dy, anc in MARKS:
    db = mag_db(f)
    x, y = fx(f), fy(db)
    b.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="3" fill="#fff" '
             f'stroke="{P["accent"]}" stroke-width="1.3"/>')
    ax_ = x + (5 if anc == "start" else -5)
    b.append(f'<text x="{ax_:.1f}" y="{y+dy:.1f}" font-size="8.5" '
             f'fill="{P["accent"]}" text-anchor="{anc}">{esc(name)}</text>')
    b.append(f'<text x="{ax_:.1f}" y="{y+dy+11:.1f}" font-size="8" '
             f'fill="{P["gray"]}" text-anchor="{anc}">{esc(f"{db:.0f} dB")}</text>')

# axes
xt = [(e, fx(10.0 ** e)) for e in range(-5, 3)]
SUP = {-5: "10⁻⁵", -4: "10⁻⁴", -3: "10⁻³", -2: "10⁻²", -1: "10⁻¹",
       0: "10⁰", 1: "10¹", 2: "10²"}
minor = [fx(m * 10.0 ** e) for e in range(-5, 3) for m in range(2, 10)
         if X0 <= fx(m * 10.0 ** e) <= X1]
yt = [(d, fy(d)) for d in (0, -40, -80, -120, -160)]
b.append(axes(X0, Y0, X1, Y1, xt, yt, "Frequency (Hz)", "Gain (dB)",
              xfmt=lambda e: SUP[e], yfmt=str, minor=minor))

open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "FIG_BAND.svg"),
     "w", encoding="utf-8").write(
    paper_svg(W, H, "".join(b), "AD8232 filter response"))
print("FIG_BAND.svg")
