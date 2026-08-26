# -*- coding: utf-8 -*-
"""Fig. — DC-coupled front end (MCP6002 buffers + ADS1115 differential).

왜 이 회로인가
--------------
AD8232 는 0.5Hz 고역통과가 실리콘에 박혀 있어 수분 스트레스(10⁻⁴ Hz)를 지운다.
버퍼는 **증폭하지 않고 임피던스만 낮춘다** — 그래서 고역통과가 없고, 이득 1배라
오프셋으로 레일에 붙지도 않는다. 증폭은 ADS1115 의 PGA 가 디지털로 맡는다.

참조 채널이 그림의 절반을 차지하는 이유는, 그것이 없으면 느린 신호에서
'식물이 마른 것'과 '전극이 표류한 것'을 구분할 수 없기 때문이다.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from figlib import P, paper_svg, pbox, parrow, esc

W, H = 940, 348
INK, GRAY, ACC = P["ink"], P["gray"], P["accent"]

EX, EW, EH = 20, 132, 28          # 전극 박스
BUF = 172                          # 버퍼 왼쪽 x
ADS_X, ADS_W = 300, 152            # ADS1115
ROWS = [("electrode 1", "stem, upper",  38, False),
        ("electrode 2", "stem, lower", 104, False),
        ("electrode 3", "dummy",       186, True),
        ("electrode 4", "dummy",       252, True)]
b = []


def wire(x0, y0, x1, y1, col=None, dash=False):
    d = ' stroke-dasharray="3 2.5"' if dash else ''
    return (f'<line x1="{x0:.1f}" y1="{y0:.1f}" x2="{x1:.1f}" y2="{y1:.1f}" '
            f'stroke="{col or INK}" stroke-width="0.9"{d}/>')


def node(x, y, col=None):
    return f'<circle cx="{x:.1f}" cy="{y:.1f}" r="2.3" fill="{col or INK}"/>'


def opamp(x, cy):
    """전압 폴로어. 삼각형 + 출력에서 −입력으로 되돌아가는 되먹임."""
    y = cy - 20
    o = [f'<path d="M{x},{y} L{x},{y+40} L{x+46},{cy} Z" fill="none" '
         f'stroke="{INK}" stroke-width="1.1"/>',
         f'<text x="{x+11}" y="{y+15}" font-size="9.5" fill="{GRAY}">+</text>',
         f'<text x="{x+11}" y="{y+36}" font-size="9.5" fill="{GRAY}">−</text>',
         f'<text x="{x+15}" y="{cy+4}" font-size="7.5" fill="{GRAY}">×1</text>',
         # 되먹임 경로
         wire(x + 46, cy, x + 58, cy), wire(x + 58, cy, x + 58, cy + 30),
         wire(x + 58, cy + 30, x - 10, cy + 30), wire(x - 10, cy + 30, x - 10, cy + 10),
         wire(x - 10, cy + 10, x, cy + 10), node(x + 46, cy)]
    return "".join(o)


# ── 전극 → 버퍼 → ADS1115 ──────────────────────────────────────
for name, sub, y, dummy in ROWS:
    cy = y + EH / 2
    col = ACC if dummy else INK
    dash = ' stroke-dasharray="3 2.5"' if dummy else ''
    b.append(f'<rect x="{EX}" y="{y}" width="{EW}" height="{EH}" fill="none" '
             f'stroke="{col}" stroke-width="0.9"{dash}/>')
    b.append(f'<text x="{EX+10}" y="{y+12}" font-size="9" font-weight="700" '
             f'fill="{col}">{esc(name)}</text>')
    b.append(f'<text x="{EX+10}" y="{y+23}" font-size="8" fill="{GRAY}">{esc(sub)}</text>')
    b.append(wire(EX + EW, cy, BUF, cy))
    b.append(opamp(BUF, cy))
    b.append(wire(BUF + 46, cy, ADS_X, cy))

for lbl, cy in [("A0", 52), ("A1", 118), ("A2", 200), ("A3", 266)]:
    b.append(f'<text x="{ADS_X-8}" y="{cy-5}" font-size="8" fill="{GRAY}" '
             f'text-anchor="end">{esc(lbl)}</text>')

# 두 채널 묶음 표시 — 선을 그으면 A0 와 A1 을 이어놓은 것처럼 보이므로 글자만 둔다.
for y0, y1, lbl, col in [(52, 118, "plant pair  A0 − A1", INK),
                         (200, 266, "reference pair  A2 − A3", ACC)]:
    b.append(f'<text x="{ADS_X-10}" y="{(y0+y1)/2+3:.0f}" font-size="8.5" fill="{col}" '
             f'text-anchor="end" font-style="italic">{esc(lbl)}</text>')

b.append(pbox(ADS_X, 34, ADS_W, 248, "ADS1115",
              ["16-bit ΔΣ, differential", "PGA 16 → ±256 mV",
               "resolution 7.81 µV", "", "two differential pairs —",
               "A2−A3 is the last one"], accent=INK, sw=1.4))

b.append(parrow(ADS_X + ADS_W, 158, 512, "I²C 400 kHz"))
b.append(pbox(512, 112, 152, 92, "Raspberry Pi 5",
              ["5 Hz for slow runs", "records voltage_ref", "subtracts before training"]))

# ── 채널 구분 라벨 ─────────────────────────────────────────────
b.append(f'<text x="{EX}" y="30" font-size="9.5" font-weight="700">PLANT</text>')
b.append(f'<text x="{EX}" y="172" font-size="9.5" font-weight="700" fill="{ACC}">'
         f'REFERENCE — dead stem or gel only, no plant</text>')

# ── 중간 전위 바이어스 ─────────────────────────────────────────
BX, TOP = 726, 52
MY = TOP + 92                      # 분압 중점
GY = TOP + 190                     # 접지 레일
b.append(f'<text x="{BX-14}" y="{TOP-18}" font-size="9.5" font-weight="700">MID-SUPPLY BIAS</text>')
b.append(f'<text x="{BX-14}" y="{TOP+4}" font-size="9">3.3 V</text>')
b.append(wire(BX, TOP + 8, BX, TOP + 30))
for i in range(2):
    ry = TOP + 30 + i * 96
    b.append(f'<rect x="{BX-8}" y="{ry}" width="16" height="38" fill="none" '
             f'stroke="{INK}" stroke-width="0.9"/>')
    b.append(f'<text x="{BX+14}" y="{ry+23}" font-size="8.5" fill="{GRAY}">100 kΩ</text>')
b.append(wire(BX, TOP + 68, BX, TOP + 126))          # R1 밑 ~ R2 위 (중점 통과)
b.append(wire(BX, TOP + 164, BX, GY))                # R2 밑 ~ 접지

# 중점에서 오른쪽으로 — 흙 전극
b.append(node(BX, MY))
b.append(wire(BX, MY, BX + 96, MY))
b.append(f'<text x="{BX+100}" y="{MY-4}" font-size="9.5" font-weight="700">1.65 V</text>')
b.append(f'<text x="{BX+100}" y="{MY+9}" font-size="8.5" fill="{GRAY}">→ soil electrode</text>')

# 중점에서 아래로 — 0.1uF (R2 옆으로 비켜서)
CX = BX + 56
b.append(node(CX, MY))
b.append(wire(CX, MY, CX, MY + 26))
b.append(f'<line x1="{CX-11}" y1="{MY+26}" x2="{CX+11}" y2="{MY+26}" stroke="{INK}" stroke-width="1.2"/>')
b.append(f'<line x1="{CX-11}" y1="{MY+32}" x2="{CX+11}" y2="{MY+32}" stroke="{INK}" stroke-width="1.2"/>')
b.append(f'<text x="{CX+16}" y="{MY+33}" font-size="8.5" fill="{GRAY}">0.1 µF</text>')
b.append(wire(CX, MY + 32, CX, GY))

# 공통 접지
b.append(wire(BX, GY, CX, GY))
GC = (BX + CX) / 2
b.append(f'<line x1="{GC-14}" y1="{GY}" x2="{GC+14}" y2="{GY}" stroke="{INK}" stroke-width="1.5"/>')
b.append(f'<line x1="{GC-8}" y1="{GY+5}" x2="{GC+8}" y2="{GY+5}" stroke="{INK}" stroke-width="1.2"/>')
b.append(f'<line x1="{GC-3}" y1="{GY+10}" x2="{GC+3}" y2="{GY+10}" stroke="{INK}" stroke-width="1.2"/>')
b.append(f'<text x="{GC+20}" y="{GY+8}" font-size="8.5" fill="{GRAY}">GND</text>')

# ── 아래 설명 두 줄 ────────────────────────────────────────────
b.append(f'<text x="{EX}" y="{H-26}" font-size="8.6" fill="{GRAY}">'
         f'Buffers do not amplify (×1); they only lower the source impedance. '
         f'There is no high-pass stage, so 10⁻⁴ Hz survives to the ADC.</text>')
b.append(f'<text x="{EX}" y="{H-12}" font-size="8.6" fill="{ACC}">'
         f'The reference pair sits in the same environment with no plant, so subtracting it '
         f'cancels the drift common to both pairs.</text>')

open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "FIG_DC.svg"),
     "w", encoding="utf-8").write(
    paper_svg(W, H, "".join(b), "DC-coupled front end with reference channel"))
print("FIG_DC.svg")
