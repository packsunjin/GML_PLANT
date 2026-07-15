"""
font_utils.py
=============
matplotlib에서 한글(정상/스트레스, 축 이름, confusion matrix 라벨)이 깨지지 않도록,
시스템에 실제 설치된 한글/CJK 폰트를 자동으로 찾아 기본 폰트로 지정한다.

핵심: 설치돼 있지 않은 폰트 이름을 rcParams에 억지로 넣으면 matplotlib이 글자를 그릴
때마다 `findfont: Font family '...' not found` 경고를 쏟아낸다. 그래서 여기서는
"실제로 설치된 폰트만" 지정하고, 하나도 없으면 아무것도 지정하지 않아(기본 폰트 유지)
경고가 나지 않게 한다.

라즈베리파이 OS에서는 `sudo apt install fonts-noto-cjk`로 'Noto Sans CJK KR'가 깔린다.
새로 폰트를 설치했는데도 못 찾으면 matplotlib 캐시를 지우고 다시 실행: `rm -rf ~/.cache/matplotlib`
"""

import matplotlib
from matplotlib import font_manager

# 우선순위: 한글 전용 -> CJK 통합 -> 나눔/윈도우/맥 계열
KOREAN_FONT_CANDIDATES = [
    "Noto Sans KR", "Noto Sans CJK KR",
    "NanumGothic", "NanumBarunGothic", "Nanum Gothic",
    "Malgun Gothic", "AppleGothic", "UnDotum", "Baekmuk Gulim",
]


def find_korean_font():
    """설치된 한글 폰트 중 우선순위가 가장 높은 것의 이름을 반환한다. 없으면 None."""
    available = {f.name for f in font_manager.fontManager.ttflist}
    for name in KOREAN_FONT_CANDIDATES:
        if name in available:
            return name
    return None


def setup_korean_font():
    """설치된 한글 폰트를 matplotlib 기본 폰트로 지정한다.
    반환: 사용한 폰트 이름(str), 또는 한글 폰트가 없으면 None(기본 폰트 유지, 경고 없음)."""
    matplotlib.rcParams["axes.unicode_minus"] = False  # 음수 부호 깨짐 방지
    name = find_korean_font()
    if name is not None:
        matplotlib.rcParams["font.family"] = name
    return name
