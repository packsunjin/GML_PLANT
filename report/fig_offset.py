# -*- coding: utf-8 -*-
"""그림11 — 전극 임피던스가 만드는 직류 오프셋과 증폭기 허용 입력 범위."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from figlib import C, LogX, txt, rect, line, svg

W, H = 900, 288
L, R = 200, 640
ax = LogX(-2.1, 1.6, L, R)           # 0.01 ~ 40 mV
ROW_H, TOP = 56, 66
LIMIT = 1.23                          # 허용 입력 ±1.23 mV

# (전극 임피던스, 만들어지는 오프셋 mV)
ROWS = [("1 MΩ",   0.025), ("100 MΩ", 2.5), ("1 GΩ", 25.0)]

b = []
xlim = ax(LIMIT)
bottom = TOP + len(ROWS) * ROW_H

# 허용 범위(왼쪽)를 배경으로
b.append(rect(L, TOP - 24, xlim - L, bottom - TOP + 16, C["passbg"]))
b.append(line(xlim, TOP - 24, xlim, bottom, C["pass"], 1.8, "5 4"))
b.append(txt(xlim - 8, TOP - 32, "허용 입력 한계 ±1.23 mV", 13, C["pass"], "end", 700))
b.append(txt(xlim + 8, TOP - 32, "이 밖 → 레일 포화", 12, C["block"], "start", 700))

for i, (z, off) in enumerate(ROWS):
    y = TOP + i * ROW_H
    cy = y + 16
    ok = off <= LIMIT
    col = C["pass"] if ok else C["block"]
    bg = C["passbg"] if ok else C["blockbg"]
    x1 = ax(off)
    b.append(rect(L, y, x1 - L, 32, bg, rx=3))
    b.append(rect(L, y, x1 - L, 32, "none", rx=3, stroke=col, sw=1.3))
    b.append(txt(L - 16, cy + 1, f"전극 {z}", 14, C["ink"], "end", 700))
    b.append(txt(L - 16, cy + 17, "오프셋 전류 25 pA", 10.5, C["muted"], "end"))
    b.append(txt(x1 + 10, cy - 2, f"{off:g} mV", 13, col, "start", 700))
    note = "허용 범위 안" if ok else f"한계의 {off/LIMIT:.0f}배 → 레일 포화"
    b.append(txt(x1 + 10, cy + 15, note, 11.5, col, "start"))

# 축
ay = bottom + 12
b.append(line(L - 8, ay, R + 8, ay, C["line"], 1.2))
for e, lab in [(-2, "0.01"), (-1, "0.1"), (0, "1"), (1, "10")]:
    x = ax(10.0 ** e)
    b.append(line(x, ay, x, ay + 5, C["line"], 1.2))
    b.append(txt(x, ay + 20, lab, 11, C["muted"], "middle"))
b.append(txt((L + R) / 2, ay + 42, "전극 사이 직류 오프셋 (mV, 로그 축)", 12, C["muted"], "middle"))

open("FIG_OFFSET.svg", "w", encoding="utf-8").write(
    svg(W, H, "".join(b), "전극 임피던스가 만드는 직류 오프셋"))
print("FIG_OFFSET.svg")
