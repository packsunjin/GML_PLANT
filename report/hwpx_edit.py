# hwpx 본문 글자만 바꾼다. 사용: python3 -I hwpx_edit.py 원본.hwpx 결과.hwpx 편집.json
# - <hp:t> 안의 글자만 바꾸고, 바꾼 문단의 <hp:linesegarray> 를 지운다(한글이 다시 계산)
# - header.xml 은 건드리지 않는다
# - 원래 zip 항목 순서·압축 방식 유지, mimetype 은 첫 항목·무압축
import sys, json, zipfile, re
from xml.sax.saxutils import escape
import xml.dom.minidom

src, dst, spec = sys.argv[1], sys.argv[2], sys.argv[3]
# 그림 바꾸기: 뒤에 BinData/image5.png=새그림.png 처럼 준다(같은 가로세로비로 맞춰 둘 것)
bins = dict(a.split("=", 1) for a in sys.argv[4:])
edits = json.load(open(spec, encoding="utf-8"))
zin = zipfile.ZipFile(src)
xml_s = zin.read("Contents/section0.xml").decode("utf-8")

def esc(s):
    return escape(s, {'"': "&quot;", "'": "&apos;"})

report = []
for e in edits:
    old, new = esc(e["old"]), esc(e["new"])
    # <hp:t> 하나 안에 old 가 통째로 있어야 한다
    # "exact": true 면 <hp:t> 내용 전체가 old 와 같을 때만 바꾼다(표 칸처럼 짧은 글자용)
    hits = [m for m in re.finditer(r"<hp:t>([^<]*)</hp:t>", xml_s)
            if (m.group(1) == old if e.get("exact") else old in m.group(1))]
    want = e.get("count", 1)
    if len(hits) != want:
        report.append(f"[실패] {len(hits)}곳(기대 {want}): {e['old'][:40]}")
        continue
    for m in reversed(hits):
        a, b = m.span(1)
        seg = xml_s[a:b].replace(old, new)
        xml_s = xml_s[:a] + seg + xml_s[b:]
        # 이 문단의 linesegarray 지우기: 다음 </hp:p> 보다 먼저 나오는 것만
        endp = xml_s.find("</hp:p>", a)
        ls = xml_s.find("<hp:linesegarray", a)
        if 0 <= ls < endp:
            le = xml_s.find("</hp:linesegarray>", ls) + len("</hp:linesegarray>")
            xml_s = xml_s[:ls] + xml_s[le:]
    report.append(f"[성공] {len(hits)}곳: {e['old'][:40]}")

xml.dom.minidom.parseString(xml_s.encode("utf-8"))      # 파싱 확인
with zipfile.ZipFile(dst, "w") as zout:
    for info in zin.infolist():
        data = (xml_s.encode("utf-8") if info.filename == "Contents/section0.xml"
                else open(bins[info.filename], "rb").read() if info.filename in bins
                else zin.read(info.filename))
        ct = zipfile.ZIP_STORED if info.filename == "mimetype" else info.compress_type
        zi = zipfile.ZipInfo(info.filename, date_time=info.date_time)
        zi.compress_type = ct; zi.external_attr = info.external_attr
        zout.writestr(zi, data)
z = zipfile.ZipFile(dst)
print("\n".join(report))
print("testzip:", z.testzip(), "| 첫 항목:", z.infolist()[0].filename, z.infolist()[0].compress_type,
      "| hp:pic:", z.read("Contents/section0.xml").decode().count("<hp:pic"),
      "| BinData:", sum(1 for i in z.infolist() if i.filename.startswith("BinData/")))
