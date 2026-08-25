# -*- coding: utf-8 -*-
"""보고서 그림 공통 요소 — 팔레트, 좌표 변환, 자주 쓰는 도형.

그림을 JSON 안의 SVG 문자열로 들고 있으면 고치기가 어렵고, 무엇보다
본문과 어긋났을 때(실제로 그런 일이 있었다) 알아채기 힘들다. 그래서 값에서
그림을 만들어 내는 방식으로 바꿨다.
"""

FONT = "Pretendard,'Malgun Gothic','Apple SD Gothic Neo',sans-serif"

# 인쇄를 전제로 한 팔레트. 배경은 흰색이어야 종이에서 깔끔하다.
C = {
    "bg":     "#ffffff",
    "ink":    "#16211a",   # 본문 글자
    "muted":  "#7b8781",   # 보조 설명
    "line":   "#c8d0ca",   # 축·구분선
    "pass":   "#1f6b3d",   # 통과·정상
    "passbg": "#e4efe8",
    "block":  "#a8322d",   # 차단·문제
    "blockbg":"#f3e2e0",
    "warn":   "#b5761f",   # 잡음·주의
    "warnbg": "#f6ead6",
    "slab":   "#eef1ee",   # 중립 배경 띠
}


def esc(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def txt(x, y, s, size=13, fill=None, anchor="start", weight=400, opacity=None):
    o = f' opacity="{opacity}"' if opacity is not None else ""
    return (f'<text x="{x:.1f}" y="{y:.1f}" font-size="{size}" '
            f'fill="{fill or C["ink"]}" text-anchor="{anchor}" '
            f'font-weight="{weight}"{o}>{esc(s)}</text>')


def rect(x, y, w, h, fill, rx=0, opacity=None, stroke=None, sw=1):
    o = f' opacity="{opacity}"' if opacity is not None else ""
    st = f' stroke="{stroke}" stroke-width="{sw}"' if stroke else ""
    return (f'<rect x="{x:.1f}" y="{y:.1f}" width="{max(w,0):.1f}" '
            f'height="{max(h,0):.1f}" fill="{fill}" rx="{rx}"{o}{st}/>')


def line(x1, y1, x2, y2, stroke=None, sw=1, dash=None):
    d = f' stroke-dasharray="{dash}"' if dash else ""
    return (f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" '
            f'stroke="{stroke or C["line"]}" stroke-width="{sw}"{d}/>')


def svg(w, h, body, title=""):
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" '
            f'role="img" aria-label="{esc(title)}">'
            f'{rect(0,0,w,h,C["bg"])}'
            f'<g font-family="{FONT}">{body}</g></svg>')


class LogX:
    """로그 주파수축. 값 -> 화면 x."""
    import math as _m

    def __init__(self, lo_exp, hi_exp, x0, x1):
        self.a, self.b, self.x0, self.x1 = lo_exp, hi_exp, x0, x1

    def __call__(self, v):
        import math
        t = (math.log10(v) - self.a) / (self.b - self.a)
        return self.x0 + t * (self.x1 - self.x0)

    def ticks(self):
        return list(range(int(self.a), int(self.b) + 1))
