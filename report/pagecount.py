# -*- coding: utf-8 -*-
"""
생성된 .docx 의 document.xml 을 직접 읽어 쪽수를 추정한다.
한글은 한 글자가 정확히 1em 폭이라 폭 계산이 잘 맞고, 줄바꿈 규칙만 근사하면 된다.
soffice 가 동작하지 않아 렌더링으로 세지 못하므로 이 방법을 쓴다.
"""
import sys, re, zipfile, math
import xml.etree.ElementTree as ET

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
WP = "{http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing}"

PAGE_W, PAGE_H = 11906, 16838          # twips
MAR = 1134
COL_PT = (PAGE_W - 2 * MAR) / 20.0     # 481.9 pt
BODY_PT = (PAGE_H - 2 * MAR) / 20.0    # 728.5 pt
LINEBOX = 1.20                         # 폰트 기본 줄상자 / 글자크기

def text_width(s, pt):
    """문자열의 폭(pt). 한글·한자·전각은 1em, 그 외는 0.5em 으로 본다."""
    em = 0.0
    for ch in s:
        o = ord(ch)
        if (0xAC00 <= o <= 0xD7A3) or (0x3000 <= o <= 0x30FF) or \
           (0x4E00 <= o <= 0x9FFF) or (0xFF00 <= o <= 0xFF60) or \
           (0x2460 <= o <= 0x24FF) or (0x2160 <= o <= 0x217F) or ch in "■·—…":
            em += 1.0
        elif ch == ' ':
            em += 0.28
        else:
            em += 0.5
    return em * pt

def para_height(p):
    """문단 하나의 세로 높이(pt)와 강제 쪽나눔 여부."""
    pr = p.find(W + "pPr")
    sz, line, before, after, ind = 22, 240, 0, 0, 0
    if pr is not None:
        sp = pr.find(W + "spacing")
        if sp is not None:
            line = int(sp.get(W + "line", 240))
            before = int(sp.get(W + "before", 0))
            after = int(sp.get(W + "after", 0))
        idt = pr.find(W + "ind")
        if idt is not None:
            ind = int(idt.get(W + "left", 0))
    # 런의 글자 크기 중 가장 큰 값을 쓴다
    sizes = [int(e.get(W + "val")) for e in p.iter(W + "sz") if e.get(W + "val")]
    if sizes:
        sz = max(sizes)
    pt = sz / 2.0

    brk = any(e.get(W + "type") == "page" for e in p.iter(W + "br"))

    # 이미지가 들어 있으면 그림 높이가 줄 높이를 대신한다
    img_pt = 0.0
    for ext in p.iter(WP + "extent"):
        img_pt = max(img_pt, int(ext.get("cy", 0)) / 914400.0 * 72.0)

    s = "".join(e.text or "" for e in p.iter(W + "t"))
    avail = COL_PT - ind / 20.0
    if img_pt:
        h = img_pt
    else:
        n_lines = max(1, math.ceil(text_width(s, pt) / avail)) if s else 1
        h = n_lines * pt * (line / 240.0) * LINEBOX
    return h + (before + after) / 20.0, brk, s

def table_height(tbl):
    h = 0.0
    for tr in tbl.findall(W + "tr"):
        row = 0.0
        cells = tr.findall(W + "tc")
        n = max(1, len(cells))
        for tc in cells:
            # 셀 폭을 모르면 균등 분할로 본다
            tcw = tc.find(W + "tcPr/" + W + "tcW")
            w_pt = int(tcw.get(W + "w")) / 20.0 if tcw is not None and tcw.get(W + "w") else COL_PT / n
            ch = 0.0
            for p in tc.findall(W + "p"):
                sizes = [int(e.get(W + "val")) for e in p.iter(W + "sz") if e.get(W + "val")]
                pt = (max(sizes) if sizes else 20) / 2.0
                s = "".join(e.text or "" for e in p.iter(W + "t"))
                nl = max(1, math.ceil(text_width(s, pt) / max(w_pt - 8, 20))) if s else 1
                ch += nl * pt * (280 / 240.0) * LINEBOX
            row = max(row, ch)
        h += row + 4          # 셀 여백
    return h

def main(path):
    z = zipfile.ZipFile(path)
    root = ET.fromstring(z.read("word/document.xml"))
    body = root.find(W + "body")
    pages, cur = 1, 0.0
    chap, used = "표지", {}
    CH = re.compile(r"^(연구 요약|[ⅠⅡⅢⅣⅤ]\.|참고 문헌|부 록|목    차)")
    for el in body:
        if el.tag == W + "p":
            h, brk, s = para_height(el)
            if CH.match(s.strip()) and len(s.strip()) < 30:
                chap = s.strip()
            if brk:
                used[chap] = used.get(chap, 0) + (1 - cur / BODY_PT)
                pages += 1; cur = 0.0
                continue
        elif el.tag == W + "tbl":
            h = table_height(el)
        else:
            continue
        if cur + h > BODY_PT:
            pages += 1; cur = h
        else:
            cur += h
        used[chap] = used.get(chap, 0) + h / BODY_PT
    print(f"{path}")
    print(f"  본문 폭 {COL_PT:.1f}pt · 본문 높이 {BODY_PT:.1f}pt · 줄상자 {LINEBOX}")
    print(f"  추정 쪽수: {pages-1+cur/BODY_PT:.2f}쪽 → {pages}쪽")
    print("  장별 분량(쪽):")
    for k, v in used.items():
        print(f"    {v:5.1f}  {k}")
    return pages

if __name__ == "__main__":
    for p in sys.argv[1:]:
        main(p)
