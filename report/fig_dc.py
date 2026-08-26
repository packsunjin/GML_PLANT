# -*- coding: utf-8 -*-
"""Fig. — DC-coupled front end (MCP6002 buffers + ADS1115 differential).

논문 회로도로 그린다. 지킬 것:
  · 선 굵기 하나, 색 없음. 회로도에서 색과 굵기는 의미를 갖는데 여기엔 그런 구분이 없다.
  · 부품은 기호로 — 저항은 사각형, 커패시터는 평행선 둘, 접지는 삼단 막대.
  · 전극은 상자가 아니라 단자(빈 동그라미)다.
  · 되먹임은 연산증폭기에 바짝 붙여 돌린다.
  · 설명 문장은 그림에 넣지 않는다. 그건 캡션이 할 일이다.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from figlib import P, paper_svg, esc

W, H = 880, 278
K = P["ink"]
SW = 0.85
b = []


def w(x0, y0, x1, y1=None, dash=False):
    if y1 is None:
        y1 = y0          # 수평선은 y 를 한 번만 쓴다
    d = ' stroke-dasharray="3 2"' if dash else ''
    return (f'<line x1="{x0:.1f}" y1="{y0:.1f}" x2="{x1:.1f}" y2="{y1:.1f}" '
            f'stroke="{K}" stroke-width="{SW}"{d}/>')


def dot(x, y):
    return f'<circle cx="{x:.1f}" cy="{y:.1f}" r="1.9" fill="{K}"/>'


def term(x, y):
    return (f'<circle cx="{x:.1f}" cy="{y:.1f}" r="2.6" fill="#fff" '
            f'stroke="{K}" stroke-width="{SW}"/>')


def t(x, y, s, size=8.4, anchor="start", style=""):
    st = ' font-style="italic"' if style == "i" else ""
    return (f'<text x="{x:.1f}" y="{y:.1f}" font-size="{size}" fill="{K}" '
            f'text-anchor="{anchor}"{st}>{esc(s)}</text>')


def follower(x, cy):
    """전압 폴로어. 출력을 반전입력으로 되돌린다.

    입력은 삼각형 가운데가 아니라 **+ 단자 높이**로 들어가야 한다. 가운데로
    그으면 어느 단자로 들어가는지가 그림에 없다."""
    o = [f'<path d="M{x},{cy-20} L{x},{cy+20} L{x+40},{cy} Z" fill="none" '
         f'stroke="{K}" stroke-width="{SW}"/>',
         t(x + 5, cy - 4, "+", 9), t(x + 5, cy + 13, "−", 9),
         # 되먹임: 출력 → 아래 → 왼쪽 → 반전입력
         w(x + 40, cy, x + 50), w(x + 50, cy, x + 50, cy + 26),
         w(x + 50, cy + 26, x - 12), w(x - 12, cy + 26, x - 12, cy + 8),
         w(x - 12, cy + 8, x), dot(x + 40, cy)]
    return "".join(o)


# ── 전극 → 폴로어 → ADS1115 ────────────────────────────────────
TX, FX, AX = 68, 152, 316
ROWS = [(40, "E1", "stem, upper"), (94, "E2", "stem, lower"),
        (170, "E3", "dummy"), (224, "E4", "dummy")]
for cy, name, sub in ROWS:
    iy = cy - 8                      # + 단자 높이
    b += [term(TX, iy), w(TX + 3, iy, FX), follower(FX, cy),
          w(FX + 40, cy, AX),
          t(TX - 7, iy - 4, name, 8.8, "end"),
          t(TX - 7, iy + 6, sub, 7.4, "end")]

# 채널 구분 — 색이나 굵기로 나누지 않는다. 각 쌍 위에 작은 이탤릭으로 적는다.
b.append(t(6, 16, "plant pair", 8.6, "start", "i"))
b.append(t(6, 146, "reference pair — dummy electrodes, no plant", 8.6, "start", "i"))

# ── ADS1115 ────────────────────────────────────────────────────
b.append(f'<rect x="{AX}" y="24" width="152" height="216" fill="none" '
         f'stroke="{K}" stroke-width="{SW}"/>')
b.append(t(AX + 76, 46, "ADS1115", 9.6, "middle"))
for cy, pin in zip([r[0] for r in ROWS], ["A0", "A1", "A2", "A3"]):
    b.append(t(AX + 6, cy + 3, pin, 8.2))
b += [t(AX + 76, 130, "16-bit, differential", 8.2, "middle"),
      t(AX + 76, 142, "PGA 16   ±256 mV", 8.2, "middle"),
      t(AX + 76, 154, "7.81 µV / LSB", 8.2, "middle")]
b.append(w(AX + 152, 132, AX + 200))
b.append(f'<path d="M{AX+194},129 L{AX+200},132 L{AX+194},135 Z" fill="{K}"/>')
b.append(t(AX + 158, 126, "I²C", 8.2))
b.append(t(AX + 158, 146, "Raspberry Pi 5", 8.2))

# ── 중간 전위 바이어스 ─────────────────────────────────────────
BX, TOP, GND = 690, 40, 236
MY = 138
b.append(w(BX - 26, TOP, BX + 26, TOP))
b.append(t(BX - 26, TOP - 6, "3.3 V", 8.6))
b.append(w(BX, TOP, BX, TOP + 22))
for i, ry in enumerate([TOP + 22, MY + 22]):
    b.append(f'<rect x="{BX-7}" y="{ry}" width="14" height="52" fill="none" '
             f'stroke="{K}" stroke-width="{SW}"/>')
    b.append(t(BX + 12, ry + 30, "100 kΩ", 8.2))
b.append(w(BX, TOP + 74, BX, MY + 22))
b.append(w(BX, MY + 74, BX, GND))

# 중점 -> 흙 전극
b.append(dot(BX, MY))
b.append(w(BX, MY, BX + 118, MY))
b.append(term(BX + 121, MY))
b.append(t(BX + 128, MY - 4, "soil", 8.8))
b.append(t(BX + 128, MY + 6, "reference", 7.4))

# 중점 -> 0.1uF -> 접지
CX = BX + 62
b.append(dot(CX, MY))
b.append(w(CX, MY, CX, MY + 30))
b.append(f'<line x1="{CX-10}" y1="{MY+30}" x2="{CX+10}" y2="{MY+30}" stroke="{K}" stroke-width="1.1"/>')
b.append(f'<line x1="{CX-10}" y1="{MY+36}" x2="{CX+10}" y2="{MY+36}" stroke="{K}" stroke-width="1.1"/>')
b.append(t(CX + 14, MY + 37, "0.1 µF", 8.2))
b.append(w(CX, MY + 36, CX, GND))

# 접지 기호
b.append(w(BX, GND, CX, GND))
GC = (BX + CX) / 2
for i, hw in enumerate((13, 8, 3.5)):
    b.append(f'<line x1="{GC-hw}" y1="{GND+i*4.5}" x2="{GC+hw}" y2="{GND+i*4.5}" '
             f'stroke="{K}" stroke-width="{1.2 if i == 0 else SW}"/>')

# 버퍼 전원 표시 — 회로도라면 있어야 한다
b.append(t(6, 270, "MCP6002 × 2 (unity-gain buffers) · 3.3 V single supply · 0.1 µF decoupling", 8.2))

open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "FIG_DC.svg"),
     "w", encoding="utf-8").write(
    paper_svg(W, H, "".join(b), "DC-coupled front end with reference channel"))
print("FIG_DC.svg")
