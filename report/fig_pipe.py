# -*- coding: utf-8 -*-
"""그림4 — 신호 처리 및 학습 파이프라인."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from figlib import P, paper_svg, pbox, parrow, esc

W, H = 900, 226
MID = 104
b = []

b.append(pbox(26, MID - 32, 106, 64, "Raw CSV", ["250 Hz", "voltage series"]))
b.append(parrow(132, MID, 168))
b.append(pbox(168, MID - 32, 122, 64, "Bandpass + notch",
              ["0.5 – 20 Hz", "50/60 Hz auto-detect"]))
b.append(parrow(290, MID, 326))
b.append(pbox(326, MID - 32, 112, 64, "Quality gate",
              ["reject rail saturation", "select event windows"]))

# 두 갈래
b.append(f'<line x1="438" y1="{MID}" x2="456" y2="{MID}" stroke="{P["ink"]}" stroke-width="0.9"/>')
b.append(f'<line x1="456" y1="48" x2="456" y2="160" stroke="{P["ink"]}" stroke-width="0.9"/>')
for yy in (48, 160):
    b.append(parrow(456, yy, 486))
b.append(pbox(486, 20, 138, 56, "Spectrogram", ["224 × 224 px → 50,176 dim."]))
b.append(pbox(486, 132, 138, 56, "Explicit features", ["7 time + 7 spectral → 14 dim."]))

# 합류
b.append(f'<line x1="624" y1="48" x2="646" y2="48" stroke="{P["ink"]}" stroke-width="0.9"/>')
b.append(f'<line x1="624" y1="160" x2="646" y2="160" stroke="{P["ink"]}" stroke-width="0.9"/>')
b.append(f'<line x1="646" y1="48" x2="646" y2="160" stroke="{P["ink"]}" stroke-width="0.9"/>')
b.append(parrow(646, MID, 682))
b.append(pbox(682, MID - 32, 150, 64, "SVM · RandomForest",
              ["GridSearchCV", "session-wise split"], accent=P["accent2"], sw=1.4))

b.append(f'<text x="382" y="212" font-size="9" fill="{P["gray"]}" text-anchor="middle">'
         f'10 s window, 2 s hop (80 % overlap)</text>')
open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "FIG_PIPE.svg"),
     "w", encoding="utf-8").write(
    paper_svg(W, H, "".join(b), "신호 처리 및 학습 파이프라인"))
print("FIG_PIPE.svg")
