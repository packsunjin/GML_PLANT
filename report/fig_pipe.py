# -*- coding: utf-8 -*-
"""그림4 — 신호 처리 및 학습 파이프라인."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from figlib import C, txt, rect, line, svg

W, H = 900, 300
b = []


def stage(x, y, w, h, title, lines, accent=None, strong=False):
    acc = accent or C["line"]
    o = [rect(x, y, w, h, "#fff", rx=7),
         rect(x, y, w, h, "none", rx=7, stroke=acc, sw=1.8 if strong else 1.2),
         txt(x + w / 2, y + 26, title, 13.5, C["ink"], "middle", 700)]
    for i, s in enumerate(lines):
        o.append(txt(x + w / 2, y + 46 + i * 16, s, 11, C["muted"], "middle"))
    return "".join(o)


def arrow(x0, y, x1, col=None):
    c = col or C["line"]
    return (line(x0, y, x1 - 8, y, c, 2) +
            f'<path d="M{x1-9},{y-4.5} L{x1},{y} L{x1-9},{y+4.5} Z" fill="{c}"/>')


MID = 128
b.append(stage(20, MID - 38, 116, 76, "원시 CSV", ["250 Hz", "전압 시계열"]))
b.append(arrow(136, MID, 168))
b.append(stage(168, MID - 38, 130, 76, "대역통과 + 노치",
               ["0.5 – 20 Hz", "50/60 Hz 자동판별"], C["pass"]))
b.append(arrow(298, MID, 330))
b.append(stage(330, MID - 38, 124, 76, "품질 게이트",
               ["레일 포화 제거", "이벤트 창 선별"], C["warn"]))

# 두 갈래
b.append(line(454, MID, 476, MID, C["line"], 2))
b.append(line(476, 60, 476, 196, C["line"], 2))
for yy in (60, 196):
    b.append(arrow(476, yy, 506))
b.append(stage(506, 24, 152, 72, "스펙트로그램",
               ["224 × 224 px", "→ 50,176 차원"]))
b.append(stage(506, 160, 152, 72, "명시적 특징",
               ["통계 7 + 주파수 7", "→ 14 차원"]))

# 합류
b.append(line(658, 60, 686, 60, C["line"], 2))
b.append(line(658, 196, 686, 196, C["line"], 2))
b.append(line(686, 60, 686, 196, C["line"], 2))
b.append(arrow(686, MID, 716))
b.append(stage(716, MID - 38, 164, 76, "SVM · RandomForest",
               ["GridSearchCV", "세션 단위 분할"], C["pass"], strong=True))

# 창 설정은 두 갈래가 갈리기 전에 정해진다 — 그 자리에만 한 번 적는다
b.append(txt(392, 248, "10초 창 · 2초 이동 (80 % 중첩)", 11.5, C["muted"], "middle"))
b.append(line(20, 268, 880, 268, C["line"], 1))
b.append(txt(20, 288,
             "학습과 실시간 추론이 같은 변환 함수를 공유한다 — 학습할 때와 서빙할 때 "
             "처리가 달라지는 문제가 구조적으로 생기지 않는다.",
             11.5, C["muted"]))

open("FIG_PIPE.svg", "w", encoding="utf-8").write(
    svg(W, H, "".join(b), "신호 처리 및 학습 파이프라인"))
print("FIG_PIPE.svg")
