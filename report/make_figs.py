# -*- coding: utf-8 -*-
"""보고서 그림 전체를 다시 만든다.

    python make_figs.py          # SVG 생성 + PNG 렌더링(3배, 약 470dpi)

그림을 값에서 만들어 내는 이유: 예전에는 SVG 문자열을 figs.json 에 넣어 두었는데,
본문과 어긋나도 알아채기 어려웠다. 실제로 그림9 가 활동전위를 '통과대역 안'으로
그려, "세 신호 모두 통과대역 아래에 있다"는 본문 결론과 정면으로 어긋나 있었다.
"""
import subprocess, sys, os

HERE = os.path.dirname(os.path.abspath(__file__))
FIGS = ["fig_hw.py", "fig_pipe.py", "fig_band.py", "fig_offset.py"]

for f in FIGS:
    subprocess.run([sys.executable, os.path.join(HERE, f)], check=True, cwd=HERE)
subprocess.run([sys.executable, os.path.join(HERE, "render.py")], check=True, cwd=HERE)
print("\n완료 — tpl.html 은 {{FIG_*}} 자리에 이 PNG 를 넣는다.")
