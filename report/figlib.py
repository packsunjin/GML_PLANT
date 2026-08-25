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


# ── 논문 스타일 ────────────────────────────────────────────────────────
# 학술지 그림의 문법은 발표 슬라이드와 다르다.
#   · 가는 선(0.7~1.5), 둥근 모서리·그림자 없음
#   · 색은 최소한으로. 강조는 하나만
#   · 글자는 작고 일정하게(8.5~10). 설명은 그림 안이 아니라 캡션에 쓴다
#   · 축은 눈금을 밖으로 짧게, 테두리는 왼쪽·아래만
P = {
    "ink":    "#111111",
    "gray":   "#666666",
    "light":  "#aaaaaa",
    "fill":   "#ececec",
    "accent": "#a5342b",
    "accent2":"#1b4f72",
}
PF = "'Helvetica Neue',Helvetica,Arial,'Malgun Gothic',sans-serif"


def paper_svg(w, h, body, title=""):
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" '
            f'role="img" aria-label="{esc(title)}">'
            f'<rect x="0" y="0" width="{w}" height="{h}" fill="#ffffff"/>'
            f'<g font-family="{PF}" fill="{P["ink"]}">{body}</g></svg>')


def axes(x0, y0, x1, y1, xticks, yticks, xlabel="", ylabel="",
         xfmt=str, yfmt=str, minor=None):
    """왼쪽·아래만 있는 축. xticks/yticks 는 (값, 화면좌표) 목록."""
    o = [f'<path d="M{x0},{y0} L{x0},{y1} L{x1},{y1}" fill="none" '
         f'stroke="{P["ink"]}" stroke-width="0.9"/>']
    for v, px in xticks:
        o.append(f'<line x1="{px:.1f}" y1="{y1}" x2="{px:.1f}" y2="{y1+4}" '
                 f'stroke="{P["ink"]}" stroke-width="0.9"/>')
        o.append(f'<text x="{px:.1f}" y="{y1+16}" font-size="9.5" fill="{P["ink"]}" '
                 f'text-anchor="middle">{esc(xfmt(v))}</text>')
    for px in (minor or []):
        o.append(f'<line x1="{px:.1f}" y1="{y1}" x2="{px:.1f}" y2="{y1+2.5}" '
                 f'stroke="{P["light"]}" stroke-width="0.7"/>')
    for v, py in yticks:
        o.append(f'<line x1="{x0-4}" y1="{py:.1f}" x2="{x0}" y2="{py:.1f}" '
                 f'stroke="{P["ink"]}" stroke-width="0.9"/>')
        o.append(f'<text x="{x0-8}" y="{py+3.2:.1f}" font-size="9.5" fill="{P["ink"]}" '
                 f'text-anchor="end">{esc(yfmt(v))}</text>')
    if xlabel:
        o.append(f'<text x="{(x0+x1)/2:.1f}" y="{y1+34}" font-size="10" '
                 f'fill="{P["ink"]}" text-anchor="middle">{esc(xlabel)}</text>')
    if ylabel:
        cy = (y0 + y1) / 2
        o.append(f'<text x="{x0-40}" y="{cy:.1f}" font-size="10" fill="{P["ink"]}" '
                 f'text-anchor="middle" transform="rotate(-90 {x0-40} {cy:.1f})">'
                 f'{esc(ylabel)}</text>')
    return "".join(o)


def pbox(x, y, w, h, title, lines, accent=None, sw=0.9):
    """논문용 블록 — 채움 없이 가는 테두리만."""
    col = accent or P["ink"]
    o = [f'<rect x="{x}" y="{y}" width="{w}" height="{h}" fill="none" '
         f'stroke="{col}" stroke-width="{sw}"/>',
         f'<text x="{x+w/2:.1f}" y="{y+18}" font-size="10.5" font-weight="700" '
         f'text-anchor="middle">{esc(title)}</text>']
    for i, s in enumerate(lines):
        o.append(f'<text x="{x+w/2:.1f}" y="{y+34+i*13}" font-size="8.8" '
                 f'fill="{P["gray"]}" text-anchor="middle">{esc(s)}</text>')
    return "".join(o)


def parrow(x0, y, x1, label=None):
    o = [f'<line x1="{x0}" y1="{y}" x2="{x1-6}" y2="{y}" stroke="{P["ink"]}" '
         f'stroke-width="0.9"/>',
         f'<path d="M{x1-6.5},{y-3} L{x1},{y} L{x1-6.5},{y+3} Z" fill="{P["ink"]}"/>']
    if label:
        o.append(f'<text x="{(x0+x1)/2:.1f}" y="{y-6}" font-size="8.5" '
                 f'fill="{P["gray"]}" text-anchor="middle">{esc(label)}</text>')
    return "".join(o)
