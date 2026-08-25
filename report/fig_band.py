# -*- coding: utf-8 -*-
"""그림9 — 식물 전기신호 대역과 AD8232 통과대역의 불일치.

본문(Ⅳ장 6절 (1))의 결론은 "세 신호 **모두** 0.5Hz 고역통과 아래에 있다"이다.
예전 그림은 활동전위를 통과대역 안(0.5~40Hz)에 그려 본문과 정면으로 어긋났다.
세포 수준의 탈분극 지속시간(0.025~2초)을 쓴 탓인데, 전극에서 실제로 보이는
시간 규모를 정하는 것은 조직을 따라 전파되는 속도(5.6cm/min)다.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from figlib import C, LogX, txt, rect, line, svg

W, H = 900, 300
L, R = 176, 812                      # 그래프 영역 좌우
ax = LogX(-4.3, 2.1, L, R)           # 10^-4.3 ~ 10^2.1 Hz
ROW_H, TOP = 46, 62

# (이름, 부제, 대역 lo~hi, 통과여부, 근거)
BANDS = [
    ("수분 스트레스", "수 시간~수 일 규모",      1e-4,  1e-3, False),
    ("변동전위 (VP)", "지속 수 분~수십 분 [13]", 3e-4,  1e-2, False),
    ("활동전위 (AP)", "전파 5.6 cm/min [12]",   1e-2, 6e-2, False),
    ("상용전원 잡음",  "60 Hz 유도잡음",          55,    65,  True),
]

b = []
PASS_LO, PASS_HI = 0.5, 40.0
px0, px1 = ax(PASS_LO), ax(PASS_HI)
body_h = TOP + len(BANDS) * ROW_H

# 통과대역 띠 — 배경으로 먼저 깐다
b.append(rect(px0, TOP - 26, px1 - px0, body_h - TOP + 20, C["passbg"]))
b.append(line(px0, TOP - 26, px0, body_h - 6, C["pass"], 1.6, "5 4"))
b.append(line(px1, TOP - 26, px1, body_h - 6, C["pass"], 1.6, "5 4"))
b.append(txt((px0 + px1) / 2, TOP - 34, "AD8232 통과대역 0.5 – 40 Hz (회로에 고정)",
             13, C["pass"], "middle", 700))

for i, (name, sub, lo, hi, passes) in enumerate(BANDS):
    y = TOP + i * ROW_H
    cy = y + 13
    fill = C["warnbg"] if passes else C["blockbg"]
    edge = C["warn"] if passes else C["block"]
    x0, x1 = ax(lo), ax(hi)
    b.append(rect(x0, y, max(x1 - x0, 3), 26, fill, rx=3))
    b.append(rect(x0, y, max(x1 - x0, 3), 26, "none", rx=3, stroke=edge, sw=1.2))
    b.append(txt(L - 14, cy - 1, name, 14, C["ink"], "end", 700))
    b.append(txt(L - 14, cy + 15, sub, 11, C["muted"], "end"))
    # 60Hz 막대는 폭이 좁아 오른쪽에 라벨을 붙이면 그림 밖으로 나간다.
    # 통과대역 안쪽(왼쪽)에 붙여 잘리지 않게 한다.
    if passes:
        b.append(txt(x0 - 10, cy + 4, "통과 — 잡음만 들어온다", 12, edge, "end", 700))
    else:
        b.append(txt(x1 + 9, cy + 4, "차단", 12, edge, "start", 700))

# 축
ay = body_h + 8
b.append(line(L - 8, ay, R + 8, ay, C["line"], 1.2))
SUP = {-4: "10⁻⁴", -3: "10⁻³", -2: "10⁻²", -1: "10⁻¹", 0: "1", 1: "10", 2: "100"}
for e in ax.ticks():
    x = ax(10.0 ** e)
    b.append(line(x, ay, x, ay + 5, C["line"], 1.2))
    b.append(txt(x, ay + 20, SUP[e], 11, C["muted"], "middle"))
b.append(txt((L + R) / 2, ay + 42, "주파수 (Hz, 로그 축)", 12, C["muted"], "middle"))

open("FIG_BAND.svg", "w", encoding="utf-8").write(
    svg(W, H, "".join(b), "식물 전기신호 대역과 AD8232 통과대역의 불일치"))
print("FIG_BAND.svg")
