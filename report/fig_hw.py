# -*- coding: utf-8 -*-
"""그림1 — 측정 시스템 하드웨어 구성.

예전 그림은 이득을 '100'으로 적었다. 계측증폭기 단만의 값이고, 본문이 쓰는
총 이득은 1100배(계측증폭기 100 x 저역통과단 11)라 서로 어긋났다.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from figlib import C, txt, rect, line, svg

W, H = 900, 300
b = []


def box(x, y, w, h, title, lines, accent, fill):
    o = [rect(x, y, w, h, fill, rx=7),
         rect(x, y, w, h, "none", rx=7, stroke=accent, sw=1.6),
         rect(x, y, w, 4, accent, rx=2),
         txt(x + w / 2, y + 30, title, 15, C["ink"], "middle", 700)]
    for i, s in enumerate(lines):
        o.append(txt(x + w / 2, y + 52 + i * 18, s, 11.5, C["muted"], "middle"))
    return "".join(o)


def arrow(x0, y, x1, label=None, color=None):
    col = color or C["muted"]
    o = [line(x0, y, x1 - 9, y, col, 2),
         f'<path d="M{x1-10},{y-5} L{x1},{y} L{x1-10},{y+5} Z" fill="{col}"/>']
    if label:
        o.append(txt((x0 + x1) / 2, y - 10, label, 11, col, "middle", 700))
    return "".join(o)


# 전극 3개
EL = [("RA", "줄기 상부", C["pass"]),
      ("LA", "줄기 하부", C["pass"]),
      ("RL", "흙 (기준)", C["warn"])]
for i, (n, sub, col) in enumerate(EL):
    y = 60 + i * 62
    b.append(rect(24, y, 132, 46, C["slab"], rx=7))
    b.append(rect(24, y, 4, 46, col, rx=2))
    b.append(txt(40, y + 22, n, 14, C["ink"], "start", 700))
    b.append(txt(40, y + 38, sub, 11, C["muted"]))
    b.append(arrow(156, y + 23, 214))

b.append(box(214, 60, 196, 170, "AD8232",
             ["생체전위 증폭기",
              "이득 1100배",
              "= 계측증폭기 100 × 필터단 11",
              "아날로그 대역 0.5 – 40 Hz",
              "RLD 능동 피드백 내장"],
             C["block"], "#fff"))

b.append(arrow(410, 145, 508, "OUTPUT 0–3.3 V"))
b.append(box(508, 84, 168, 122, "ADS1115",
             ["16비트 ADC · A0 채널",
              "860 SPS · 연속변환",
              "PGA ±4.096 V"],
             C["pass"], "#fff"))

b.append(arrow(676, 145, 762, "I²C 400 kHz"))
b.append(box(762, 84, 114, 122, "라즈베리파이 5",
             ["I2C1 · 3·5번 핀",
              "Flask 대시보드",
              "250 Hz 수집"],
             C["pass"], "#fff"))

# 아래 한 줄 — 이 그림에서 가장 중요한 제약
b.append(line(24, 258, 876, 258, C["line"], 1))
b.append(txt(24, 280,
             "RL은 단순 접지가 아니라 RLD 피드백의 출력단이다. 이 전극이 기준 전위를 못 잡으면 "
             "증폭기 출력이 전원 레일에 붙어 측정 자체가 성립하지 않는다.",
             11.5, C["muted"]))

open("FIG_HW.svg", "w", encoding="utf-8").write(
    svg(W, H, "".join(b), "측정 시스템 하드웨어 구성"))
print("FIG_HW.svg")
