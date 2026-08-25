# -*- coding: utf-8 -*-
"""그림4 — 신호 처리 및 학습 파이프라인."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from figlib import P, paper_svg, pbox, parrow, esc

W, H = 900, 250
MID = 104
b = []

b.append(pbox(26, MID - 32, 106, 64, "원시 CSV", ["250 Hz", "전압 시계열"]))
b.append(parrow(132, MID, 168))
b.append(pbox(168, MID - 32, 122, 64, "대역통과 + 노치",
              ["0.5 – 20 Hz", "50/60 Hz 자동판별"]))
b.append(parrow(290, MID, 326))
b.append(pbox(326, MID - 32, 112, 64, "품질 게이트",
              ["레일 포화 제거", "이벤트 창 선별"]))

# 두 갈래
b.append(f'<line x1="438" y1="{MID}" x2="456" y2="{MID}" stroke="{P["ink"]}" stroke-width="0.9"/>')
b.append(f'<line x1="456" y1="48" x2="456" y2="160" stroke="{P["ink"]}" stroke-width="0.9"/>')
for yy in (48, 160):
    b.append(parrow(456, yy, 486))
b.append(pbox(486, 20, 138, 56, "스펙트로그램", ["224 × 224 px → 50,176 차원"]))
b.append(pbox(486, 132, 138, 56, "명시적 특징", ["통계 7 + 주파수 7 → 14 차원"]))

# 합류
b.append(f'<line x1="624" y1="48" x2="646" y2="48" stroke="{P["ink"]}" stroke-width="0.9"/>')
b.append(f'<line x1="624" y1="160" x2="646" y2="160" stroke="{P["ink"]}" stroke-width="0.9"/>')
b.append(f'<line x1="646" y1="48" x2="646" y2="160" stroke="{P["ink"]}" stroke-width="0.9"/>')
b.append(parrow(646, MID, 682))
b.append(pbox(682, MID - 32, 150, 64, "SVM · RandomForest",
              ["GridSearchCV", "세션 단위 분할"], accent=P["accent2"], sw=1.4))

b.append(f'<text x="382" y="212" font-size="9" fill="{P["gray"]}" text-anchor="middle">'
         f'10초 창 · 2초 이동 (80 % 중첩)</text>')
b.append(f'<line x1="26" y1="222" x2="874" y2="222" stroke="{P["light"]}" stroke-width="0.7"/>')
b.append(f'<text x="26" y="240" font-size="9" fill="{P["gray"]}">'
         f'학습과 실시간 추론이 같은 변환 함수를 공유하므로, 학습할 때와 서빙할 때 '
         f'처리가 달라지는 문제가 구조적으로 생기지 않는다.</text>')

open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "FIG_PIPE.svg"),
     "w", encoding="utf-8").write(
    paper_svg(W, H, "".join(b), "신호 처리 및 학습 파이프라인"))
print("FIG_PIPE.svg")
