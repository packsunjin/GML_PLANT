# -*- coding: utf-8 -*-
"""tpl.html 의 표·그림 번호를 문서 등장 순서대로 다시 매긴다.
번호가 정의되는 자리는 두 곳이다.
  · 실제 캡션      <span class="cap">표3.</span>
  · 안내상자 예약   <p class="tag">■ … · 표5</p>   (측정 후 채울 자리)
본문의 모든 '표N'·'그림N' 참조도 같은 표로 함께 고친다."""
import re, sys, collections

s = open("tpl.html", encoding="utf-8").read()

SLOT = re.compile(r'class="cap">(표|그림)(\d+)\.|· (표|그림)(\d+)(?:–(\d+))?')

order = {"표": [], "그림": []}
for m in SLOT.finditer(s):
    kind = m.group(1) or m.group(3)
    if m.group(2):                       # 실제 캡션 — 한 번호
        nums = [int(m.group(2))]
    else:                                # 안내상자 — '그림5–7' 처럼 범위일 수 있다
        a = int(m.group(4)); b = int(m.group(5)) if m.group(5) else a
        nums = list(range(a, b + 1))
    for n in nums:
        if n not in order[kind]:
            order[kind].append(n)

mapping = {k: {old: i + 1 for i, old in enumerate(v)} for k, v in order.items()}
for k in ("표", "그림"):
    changed = {o: n for o, n in mapping[k].items() if o != n}
    print(f"  {k}: {len(order[k])}개 · 바뀌는 번호 {changed if changed else '없음'}")

def sub(m):
    kind, num = m.group(1), int(m.group(2))
    return f"{kind}{mapping[kind].get(num, num)}"

out = re.sub(r'(표|그림)(\d+)', sub, s)
if "--write" in sys.argv:
    open("tpl.html", "w", encoding="utf-8").write(out)
    print("  → tpl.html 갱신")
else:
    print("  (미리보기만 — 적용하려면 --write)")
