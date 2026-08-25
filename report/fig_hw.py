# -*- coding: utf-8 -*-
"""그림1 — 측정 시스템 하드웨어 구성.

예전 그림은 이득을 '100'으로 적었다. 계측증폭기 단만의 값이고, 본문이 쓰는
총 이득은 1100배(계측증폭기 100 x 저역통과단 11)라 서로 어긋났다.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from figlib import P, paper_svg, pbox, parrow, esc

W, H = 900, 200
MID = 108
b = []

# 전극 3개
EL = [("RA", "stem, upper"), ("LA", "stem, lower"), ("RL", "soil (ref.)")]
for i, (n, sub) in enumerate(EL):
    y = 44 + i * 46
    b.append(f'<rect x="26" y="{y}" width="104" height="34" fill="none" '
             f'stroke="{P["gray"]}" stroke-width="0.9"/>')
    b.append(f'<text x="38" y="{y+15}" font-size="10.5" font-weight="700">{n}</text>')
    b.append(f'<text x="38" y="{y+27}" font-size="8.8" fill="{P["gray"]}">{esc(sub)}</text>')
    b.append(parrow(130, y + 17, 176))

b.append(pbox(176, 44, 168, 138, "AD8232",
              ["biopotential front end",
               "gain 1100 (IA 100 × filter 11)",
               "analog band 0.5 – 40 Hz",
               "RLD active feedback"],
              accent=P["accent"], sw=1.4))

b.append(parrow(344, MID, 438, "0–3.3 V"))
b.append(pbox(438, 66, 150, 94, "ADS1115",
              ["16-bit ADC, ch. A0", "860 SPS, continuous", "PGA ±4.096 V"]))

b.append(parrow(588, MID, 674, "I²C 400 kHz"))
b.append(pbox(674, 66, 150, 94, "Raspberry Pi 5",
              ["I2C1 (pins 3, 5)", "250 Hz acquisition", "Flask dashboard"]))

open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "FIG_HW.svg"),
     "w", encoding="utf-8").write(
    paper_svg(W, H, "".join(b), "측정 시스템 하드웨어 구성"))
print("FIG_HW.svg")
