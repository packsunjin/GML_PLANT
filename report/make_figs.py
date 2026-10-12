# -*- coding: utf-8 -*-
"""보고서 그림 전체를 다시 만든다 — figs.py 를 부른다.

    python make_figs.py                       # 파이에서 (실측 데이터가 파이에 있다)
    python make_figs.py --ad8232 /tmp/ad8232  # 그림3(AD8232 28장)도 다시 만들 때

예전에는 SVG 를 손으로 그려 크롬으로 PNG 를 떴다. 지금은 figs.py 가 원본 CSV 에서
바로 PNG(400 dpi)를 만든다. 본문 숫자와 그림이 같은 데이터에서 나와야 어긋나지 않는다.
그림 순서는 build_docx.py 의 PLATES, tpl.html 의 {{FIG_*}} 순서와 같아야 한다.
"""
import subprocess, sys, os

HERE = os.path.dirname(os.path.abspath(__file__))
FIGS = ["FIG_BAND", "FIG_SYS", "FIG_AD8232", "FIG_STAGE", "FIG_TOUCH", "FIG_SPEC"]   # 본문 등장 순서

subprocess.run([sys.executable, os.path.join(HERE, "figs.py")] + sys.argv[1:], check=True, cwd=HERE)
print("\n완료 — tpl.html 은 {{FIG_*}} 자리에 이 PNG 를 넣는다:", ", ".join(FIGS))
