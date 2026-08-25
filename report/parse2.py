import re, json
from html.parser import HTMLParser
s=open('tpl.html',encoding='utf-8').read()
s=s[s.index('<h2>목차'):]

class P(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.b=[]; self.st=[]; self.buf=[]; self.cell=None
        self.tbl=None; self.row=None; self.inhead=False; self.lst=[]
    def handle_data(self,d):
        (self.cell if self.cell is not None else self.buf).append(d)
    def handle_starttag(self,tag,attrs):
        a=dict(attrs); c=a.get('class','')
        if tag in ('h2','h3','h4','h6','p','li','figcaption','caption'):
            self.buf=[]; self.st.append((tag,c))
        elif tag in ('ul','ol'): self.lst.append(tag)
        elif tag=='table': self.tbl={'head':[],'body':[]}
        elif tag=='tr': self.row=[]; self.inhead=False
        elif tag in ('th','td'):
            self.cell=[]; self.inhead=self.inhead or tag=='th'
            self.cs=int(a.get('colspan',1))
        elif tag=='div' and 'photo' in c: self.b.append({'k':'box','t':''})
        elif tag=='div' and 'plate' in c: self.b.append({'k':'plate','t':''})
    def handle_endtag(self,tag):
        if tag in ('h2','h3','h4','h6','p','li','figcaption','caption'):
            t,c=self.st.pop() if self.st else (tag,'')
            txt=re.sub(r'[ \t]+',' ',''.join(self.buf)).strip(); self.buf=[]
            if txt:
                k={'h2':'chap','h3':'sec','h4':'item','h6':'boxtitle','p':'para','li':'li',
                   'figcaption':'figcap','caption':'tblcap'}[t]
                e={'k':k,'t':txt,'c':c}
                if k=='li': e['ord'] = (self.lst[-1]=='ol') if self.lst else False
                self.b.append(e)
        elif tag in ('ul','ol'):
            if self.lst: self.lst.pop()
            self.b.append({'k':'listend','t':''})
        elif tag in ('th','td'):
            self.row.append({'t':re.sub(r'\s+',' ',''.join(self.cell)).strip(),'cs':self.cs}); self.cell=None
        elif tag=='tr':
            if self.row: (self.tbl['head'] if self.inhead else self.tbl['body']).append(self.row)
            self.row=None
        elif tag=='table':
            self.b.append({'k':'table','t':self.tbl}); self.tbl=None
p=P(); p.feed(s)
json.dump(p.b, open('blocks.json','w',encoding='utf-8'), ensure_ascii=False)
import collections; print(len(p.b), collections.Counter(x['k'] for x in p.b))
print("ol li:", sum(1 for x in p.b if x['k']=='li' and x.get('ord')))
