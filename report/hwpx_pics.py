# hwpx 의 "■ … 넣을 자리" 문단을 그림으로 바꾼다. 파이에서 실행.
#   python3 -I hwpx_pics.py 원본.hwpx 결과.hwpx "사진2 넣을 자리=a.jpg=24000" ...
# 값은 "자리 문구=그림 파일=가로(HWPUNIT, 본문 폭 48190)". 세로는 그림 비율로 정한다.
# 기존 그림 문단(<hp:pic>)을 본보기로 복사하고 binaryItemIDRef·크기만 바꾼다.
# BinData/imageN 을 새로 넣고 Contents/content.hpf 에 등록한다. 글자 서식은 건드리지 않는다.
import re, sys, zipfile
from xml.dom import minidom
from PIL import Image

src, dst, specs = sys.argv[1], sys.argv[2], sys.argv[3:]
zin = zipfile.ZipFile(src)
sec = zin.read("Contents/section0.xml").decode("utf-8")
hpf = zin.read("Contents/content.hpf").decode("utf-8")
tpl = re.search(r"<hp:pic .*?</hp:pic>", sec, re.S).group(0)
n_img = max(int(x) for x in re.findall(r'id="image(\d+)"', hpf))
new_bins = []

for spec in specs:
    key, path, width = spec.split("=")
    width = int(width)
    w_px, h_px = Image.open(path).size
    height = round(width * h_px / w_px)
    n_img += 1
    ext = "png" if path.lower().endswith(".png") else "jpg"
    name = f"image{n_img}"
    pic = tpl
    pic = re.sub(r'(<hp:orgSz|<hp:curSz) width="\d+" height="\d+"', rf'\1 width="{width}" height="{height}"', pic)
    pic = re.sub(r'<hp:sz width="\d+"(.*?)height="\d+"', rf'<hp:sz width="{width}"\1height="{height}"', pic)
    pic = re.sub(r'<hc:pt1 x="\d+" y="0"/>', f'<hc:pt1 x="{width}" y="0"/>', pic)
    pic = re.sub(r'<hc:pt2 x="\d+" y="\d+"/>', f'<hc:pt2 x="{width}" y="{height}"/>', pic)
    pic = re.sub(r'<hc:pt3 x="0" y="\d+"/>', f'<hc:pt3 x="0" y="{height}"/>', pic)
    pic = re.sub(r'<hp:imgClip [^>]*/>', f'<hp:imgClip left="0" right="{w_px * 75}" top="0" bottom="{h_px * 75}"/>', pic)
    pic = re.sub(r'binaryItemIDRef="\w+"', f'binaryItemIDRef="{name}"', pic)
    pic = re.sub(r"<hp:shapeComment>.*?</hp:shapeComment>",
                 f"<hp:shapeComment>그림입니다.\n원본 그림의 크기: 가로 {w_px}pixel, 세로 {h_px}pixel</hp:shapeComment>", pic, flags=re.S)
    hits = [m for m in re.finditer(r"<hp:t>([^<]*)</hp:t>", sec) if key in m.group(1)]
    if len(hits) != 1:
        sys.exit(f"[실패] '{key}' 자리 {len(hits)}곳")
    m = hits[0]
    p0 = sec.rfind("<hp:p ", 0, m.start())
    p1 = sec.find("</hp:p>", m.end())
    para = sec[p0:p1]
    para = para.replace(m.group(0), pic, 1)
    para = re.sub(r"<hp:linesegarray>.*?</hp:linesegarray>", "", para, flags=re.S)
    sec = sec[:p0] + para + sec[p1:]
    mt = "image/png" if ext == "png" else "image/jpeg"
    item = f'<opf:item id="{name}" href="BinData/{name}.{ext}" media-type="{mt}" isEmbeded="1"/>'
    last = list(re.finditer(r'<opf:item id="image\d+"[^>]*/>', hpf))[-1]
    hpf = hpf[:last.end()] + item + hpf[last.end():]
    new_bins.append((f"BinData/{name}.{ext}", path))
    print(f"[성공] {key} → {name}.{ext} ({w_px}×{h_px}px, {width}×{height})")

minidom.parseString(sec.encode("utf-8"))
minidom.parseString(hpf.encode("utf-8"))
infos = zin.infolist()
last_bin = max(i for i, x in enumerate(infos) if x.filename.startswith("BinData/"))
with zipfile.ZipFile(dst, "w") as zout:
    for i, info in enumerate(infos):
        data = (sec.encode("utf-8") if info.filename == "Contents/section0.xml"
                else hpf.encode("utf-8") if info.filename == "Contents/content.hpf"
                else zin.read(info.filename))
        zout.writestr(info, data)
        if i == last_bin:
            for arc, path in new_bins:
                zi = zipfile.ZipInfo(arc, date_time=info.date_time)
                zi.compress_type = info.compress_type
                zout.writestr(zi, open(path, "rb").read())
z = zipfile.ZipFile(dst)
print("testzip:", z.testzip(), "| 첫 항목:", z.infolist()[0].filename, z.infolist()[0].compress_type,
      "| hp:pic", z.read("Contents/section0.xml").decode().count("<hp:pic "),
      "| BinData", sum(1 for x in z.namelist() if x.startswith("BinData/")))
