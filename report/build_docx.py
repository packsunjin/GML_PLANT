# -*- coding: utf-8 -*-
"""HWP 서식2 규칙에 맞춘 결과보고서 DOCX 생성 (외부 라이브러리 없이 OOXML 직접 작성)"""
import json, zipfile, struct, os, re
from xml.sax.saxutils import escape

import sys
COMPACT = '--compact' in sys.argv          # 20쪽 판: 정보 손실이 적은 항목만 덜어낸다
BLK=json.load(open('blocks.json',encoding='utf-8'))
FONT='휴먼명조'
PLATES=['FIG_HW.png','FIG_PIPE.png','FIG_BAND.png','FIG_OFFSET.png']
EMU_W=5200000                      # 그림 폭 — 본문 폭의 약 85%.
                                   # 원본이 2700px 이라 이 폭에서도 약 470dpi 로, 서식이 요구하는 300dpi 를 넘는다.
TW=9360                            # 표 전체 폭(dxa)

def png_size(p):
    d=open(p,'rb').read(33); w,h=struct.unpack('>II', d[16:24]); return w,h

# ── 런/문단 ─────────────────────────────────────────
def rpr(sz=22, b=False, i=False, color=None):
    x=f'<w:rFonts w:ascii="{FONT}" w:hAnsi="{FONT}" w:eastAsia="{FONT}" w:cs="{FONT}"/>'
    if b: x+='<w:b/><w:bCs/>'
    if i: x+='<w:i/>'
    if color: x+=f'<w:color w:val="{color}"/>'
    return f'<w:rPr>{x}<w:sz w:val="{sz}"/><w:szCs w:val="{sz}"/></w:rPr>'

def run(t, **kw):
    return f'<w:r>{rpr(**kw)}<w:t xml:space="preserve">{escape(t)}</w:t></w:r>'

def para(t='', sz=22, align=None, b=False, ind=0, before=0, after=44, line=312, keep=False, color=None):
    pr='<w:pPr>'
    if keep: pr+='<w:keepNext/>'
    pr+=f'<w:spacing w:before="{before}" w:after="{after}" w:line="{line}" w:lineRule="auto"/>'
    if ind: pr+=f'<w:ind w:left="{ind}" w:hanging="{min(ind,220)}"/>'
    if align: pr+=f'<w:jc w:val="{align}"/>'
    pr+=rpr(sz,b,color=color).replace('<w:rPr>','<w:rPr>').replace('</w:rPr>','</w:rPr>')
    pr+='</w:pPr>'
    body=run(t, sz=sz, b=b, color=color) if t else ''
    return f'<w:p>{pr}{body}</w:p>'

def pagebreak():
    return '<w:p><w:r><w:br w:type="page"/></w:r></w:p>'

# ── 표 ─────────────────────────────────────────────
def table(head, body, widths=None):
    cols = max([sum(c['cs'] for c in r) for r in (head+body)] or [1])
    if not widths: widths=[TW//cols]*cols
    g=''.join(f'<w:gridCol w:w="{w}"/>' for w in widths)
    out=[f'<w:tbl><w:tblPr><w:tblW w:w="{TW}" w:type="dxa"/><w:jc w:val="center"/>'
         '<w:tblBorders>'+''.join(f'<w:{s} w:val="single" w:sz="6" w:space="0" w:color="666666"/>'
            for s in ('top','left','bottom','right','insideH','insideV'))+'</w:tblBorders>'
         '<w:tblCellMar><w:top w:w="60" w:type="dxa"/><w:left w:w="90" w:type="dxa"/>'
         '<w:bottom w:w="60" w:type="dxa"/><w:right w:w="90" w:type="dxa"/></w:tblCellMar>'
         f'</w:tblPr><w:tblGrid>{g}</w:tblGrid>']
    def row(cells, hdr):
        tr='<w:tr>'
        if hdr: tr='<w:tr><w:trPr><w:tblHeader/></w:trPr>'
        i=0
        for c in cells:
            w=sum(widths[i:i+c['cs']]); i+=c['cs']
            sp=f'<w:gridSpan w:val="{c["cs"]}"/>' if c['cs']>1 else ''
            sh='<w:shd w:val="clear" w:color="auto" w:fill="EFEFEF"/>' if hdr else ''
            al='center' if hdr else ('center' if len(c['t'])<=12 else 'left')
            tr+=(f'<w:tc><w:tcPr><w:tcW w:w="{w}" w:type="dxa"/>{sp}{sh}'
                 '<w:vAlign w:val="center"/></w:tcPr>'
                 +para(c['t'], sz=20, align=al, b=hdr, after=0, line=280)+'</w:tc>')
        return tr+'</w:tr>'
    for r in head: out.append(row(r, True))
    for r in body: out.append(row(r, False))
    out.append('</w:tbl>')
    return ''.join(out)+para('', after=80)

# ── 그림 ───────────────────────────────────────────
def image(idx, rid):
    w,h=png_size(PLATES[idx]); cy=int(EMU_W*h/w)
    # pPr 스키마 순서: keepNext -> spacing -> jc
    return ('<w:p><w:pPr><w:keepNext/><w:spacing w:before="120" w:after="60"/>'
            '<w:jc w:val="center"/></w:pPr><w:r><w:drawing>'
            f'<wp:inline distT="0" distB="0" distL="0" distR="0"><wp:extent cx="{EMU_W}" cy="{cy}"/>'
            '<wp:effectExtent l="0" t="0" r="0" b="0"/>'
            f'<wp:docPr id="{idx+1}" name="Picture {idx+1}"/><wp:cNvGraphicFramePr/>'
            '<a:graphic xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">'
            '<a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/picture">'
            '<pic:pic xmlns:pic="http://schemas.openxmlformats.org/drawingml/2006/picture">'
            f'<pic:nvPicPr><pic:cNvPr id="{idx+1}" name="p{idx}.png"/><pic:cNvPicPr/></pic:nvPicPr>'
            f'<pic:blipFill><a:blip r:embed="{rid}"/><a:stretch><a:fillRect/></a:stretch></pic:blipFill>'
            f'<pic:spPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="{EMU_W}" cy="{cy}"/></a:xfrm>'
            '<a:prstGeom prst="rect"><a:avLst/></a:prstGeom></pic:spPr></pic:pic>'
            '</a:graphicData></a:graphic></wp:inline></w:drawing></w:r></w:p>')

# ── 안내 상자(사진·데이터 삽입 예정) ─────────────────
def box(lines):
    inner=''.join(para(t, sz=18, after=40, color='555555') for t in lines)
    return ('<w:tbl><w:tblPr><w:tblW w:w="9360" w:type="dxa"/><w:jc w:val="center"/>'
            '<w:tblBorders>'+''.join(f'<w:{s} w:val="dashed" w:sz="6" w:space="0" w:color="999999"/>'
              for s in ('top','left','bottom','right'))+'</w:tblBorders>'
            '<w:tblCellMar><w:top w:w="120" w:type="dxa"/><w:left w:w="160" w:type="dxa"/>'
            '<w:bottom w:w="120" w:type="dxa"/><w:right w:w="160" w:type="dxa"/></w:tblCellMar></w:tblPr>'
            '<w:tblGrid><w:gridCol w:w="9360"/></w:tblGrid>'
            f'<w:tr><w:tc><w:tcPr><w:tcW w:w="9360" w:type="dxa"/></w:tcPr>{inner}</w:tc></w:tr></w:tbl>'
            +para('', after=80))
print("모듈 준비 완료")

# ══ 문서 조립 ══════════════════════════════════════
CIRC='①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮'
body=[]

# ── 표지 ───────────────────────────────────────────
body.append(para('제6회 학생 SW·AI 인재 양성 프로젝트 결과 보고서', sz=32, align='center', b=True, before=1200, after=1200))
body.append(table([[{'t':'연구 주제명','cs':1}]],
                  [[{'t':'머신러닝 모델 기반의 식물 전기 신호 시각화 및 패턴 분석을 활용한 실시간 상태 분류 시스템 구현','cs':1}]],
                  widths=[TW]))
body.append(para('', after=1600))
for t in ['영천고등학교 1학년 박선진','유도혁','이유찬','박소미']:
    body.append(para(t, sz=24, align='center', after=40))
body.append(para('', after=800))
body.append(para('자문위원: ㈜에이포랩  김민재', sz=24, align='center', after=60))
body.append(para('지도교사: 영천고등학교  이민정', sz=24, align='center', after=60))
body.append(pagebreak())

# ── 목차 ───────────────────────────────────────────
body.append(para('목    차', sz=28, align='center', b=True, after=400))
for t in ['연구 요약','Ⅰ. 서론','Ⅱ. 연구 배경','Ⅲ. 연구 방법 및 내용','Ⅳ. 연구 결과','Ⅴ. 결론 및 제언','참고 문헌','부 록']:
    body.append(para(t, sz=24, after=160, ind=720))
body.append(pagebreak())

# ── 본문 ───────────────────────────────────────────
i=0; plate_i=0; box_open=None; li_n=0; in_ref=False; skip_section=False; skip_box=False
while i < len(BLK):
    b=BLK[i]; k=b['k']; t=b.get('t','')
    if box_open is not None and k not in ('boxtitle','para','li','listend'):
        body.append(box(box_open)); box_open=None
    if k=='chap':
        if t=='목차':
            # 원본 HTML의 목차 블록은 통째로 건너뛴다(위에서 서식에 맞춰 새로 만들었다).
            i+=1
            while i < len(BLK) and BLK[i]['k']!='chap':
                i+=1
            continue
        in_ref = (t=='참고 문헌')
        # 20쪽 제한 때문에 장마다 쪽을 새로 시작하지 않고 이어서 흐르게 한다.
        # (표지·목차만 쪽을 나눈다. 이것만으로 약 2쪽이 절약된다.)
        if not COMPACT and t!='연구 요약': body.append(pagebreak())
        body.append(para(t.replace('Ⅰ.','Ⅰ. ').replace('Ⅱ.','Ⅱ. ').replace('Ⅲ.','Ⅲ. ')
                          .replace('Ⅳ.','Ⅳ. ').replace('Ⅴ.','Ⅴ. '),
                         sz=28, b=True, before=200, after=200, keep=True))
    elif k=='sec':
        # 20쪽 판에서는 '용어 정리'(일반 용어 해설)를 생략한다
        # 저장소 README 에 그대로 있는 항목은 20쪽 판에서 링크로 갈음한다
        skip_section = COMPACT and t.startswith('3. 용어 정리')   # 용어는 모두 본문에서 정의했다.
        # '2. 실험 재현 절차'는 짧고 연구보고서에서 값이 크므로 20쪽 판에도 남긴다
        if not skip_section:
            body.append(para(t, sz=24, b=True, before=200, after=120, keep=True))
    elif k=='item':  body.append(para(t, sz=22, b=True, before=160, after=100, keep=True))
    elif k=='boxtitle':
        box_open=[('■ '+t)]
        if COMPACT and skip_box:
            box_open=None
    elif k=='para':
        if COMPACT and '(선택)' in t:
            skip_box=True; i+=1; continue
        if box_open is not None: box_open.append(t)
        elif not (COMPACT and skip_section): body.append(para(t, sz=22, after=80))
    elif k=='li':
        if box_open is not None: box_open.append('· '+t); 
        elif in_ref:
            li_n+=1
            ref = t.split(' — ')[0] if COMPACT else t
            # 참고문헌은 학술 표기법대로 주석 없이, 9pt·줄간격 1.15로 촘촘하게 싣는다
            body.append(para(f'[{li_n}] {ref}', sz=18, ind=340, after=20, line=276))
        elif b.get('ord'):
            li_n+=1; body.append(para(f'{CIRC[(li_n-1)%15]} {t}', sz=22, ind=440, after=60))
        else:
            body.append(para('· '+t, sz=22, ind=440, after=60))
    elif k=='listend':
        if not in_ref: li_n=0
    elif k=='tblcap':
        # 양식: 표 제목은 표 상단 중앙, 휴먼명조 11pt
        body.append(para(t, sz=22, align='center', after=60, keep=True))
    elif k=='table':
        if not (COMPACT and skip_section): body.append(table(t['head'], t['body']))
    elif k=='plate':
        body.append(image(plate_i, f'rId{100+plate_i}')); plate_i+=1
    elif k=='figcap':
        parts=[x.strip() for x in t.split('\n') if x.strip()]
        body.append(para(parts[0], sz=22, align='center', after=40))
        for extra in parts[1:]:
            body.append(para(extra, sz=18, align='center', after=100, color='555555'))
    i+=1
if box_open is not None: body.append(box(box_open))
print("본문 요소", len(body))

# ══ 패키징 ════════════════════════════════════════
NS=('xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
    'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" '
    'xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing" '
    'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
    'xmlns:pic="http://schemas.openxmlformats.org/drawingml/2006/picture"')
# sectPr 스키마 순서: footerReference 가 pgSz/pgMar 보다 먼저 와야 한다
sect=('<w:sectPr><w:footerReference w:type="default" r:id="rId9"/>'
      '<w:pgSz w:w="11906" w:h="16838"/>'
      '<w:pgMar w:top="1134" w:right="1134" w:bottom="1134" w:left="1134" '
      'w:header="720" w:footer="720" w:gutter="0"/></w:sectPr>')
doc=f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<w:document {NS}><w:body>{"".join(body)}{sect}</w:body></w:document>'

styles=f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:styles {NS}><w:docDefaults><w:rPrDefault><w:rPr>
<w:rFonts w:ascii="{FONT}" w:hAnsi="{FONT}" w:eastAsia="{FONT}" w:cs="{FONT}"/>
<w:sz w:val="22"/><w:szCs w:val="22"/><w:lang w:val="ko-KR" w:eastAsia="ko-KR"/>
</w:rPr></w:rPrDefault><w:pPrDefault><w:pPr>
<w:spacing w:after="120" w:line="360" w:lineRule="auto"/></w:pPr></w:pPrDefault></w:docDefaults>
<w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/>
<w:qFormat/></w:style></w:styles>'''

footer=f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:ftr {NS}><w:p><w:pPr><w:jc w:val="center"/></w:pPr>
<w:r>{rpr(18)}<w:fldChar w:fldCharType="begin"/></w:r>
<w:r>{rpr(18)}<w:instrText xml:space="preserve"> PAGE </w:instrText></w:r>
<w:r>{rpr(18)}<w:fldChar w:fldCharType="end"/></w:r></w:p></w:ftr>'''

rels_doc=['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
 '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
 '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
 '<Relationship Id="rId9" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/footer" Target="footer1.xml"/>']
for n in range(len(PLATES)):
    rels_doc.append(f'<Relationship Id="rId{100+n}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" Target="media/p{n}.png"/>')
rels_doc.append('</Relationships>')

ct=('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
 '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
 '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
 '<Default Extension="xml" ContentType="application/xml"/>'
 '<Default Extension="png" ContentType="image/png"/>'
 '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
 '<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>'
 '<Override PartName="/word/footer1.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.footer+xml"/>'
 '</Types>')

rels=('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
 '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
 '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
 '</Relationships>')

OUT='GML_결과보고서_20쪽판.docx' if COMPACT else 'GML_결과보고서.docx'
with zipfile.ZipFile(OUT,'w',zipfile.ZIP_DEFLATED) as z:
    z.writestr('[Content_Types].xml', ct)
    z.writestr('_rels/.rels', rels)
    z.writestr('word/document.xml', doc)
    z.writestr('word/styles.xml', styles)
    z.writestr('word/footer1.xml', footer)
    z.writestr('word/_rels/document.xml.rels', ''.join(rels_doc))
    for n,p in enumerate(PLATES): z.write(p, f'word/media/p{n}.png')

import xml.etree.ElementTree as ET
ET.fromstring(doc); ET.fromstring(styles); ET.fromstring(footer)
print("XML 검증 통과 |", OUT, os.path.getsize(OUT), "bytes")
