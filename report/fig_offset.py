# -*- coding: utf-8 -*-
"""그림11 — 전극 임피던스가 만드는 직류 오프셋.

막대 세 개 대신 관계식을 직선으로 그린다. V = I_os · Z_e 이므로 로그–로그에서
기울기 1의 직선이고, 허용 입력 한계와 만나는 점이 곧 '이 임피던스를 넘으면
오프셋만으로 포화한다'는 경계다. 그 경계값(49 MΩ)이 그림에서 바로 읽힌다.
"""
import sys, os, math
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from figlib import P, paper_svg, axes, esc

W, H = 760, 262
X0, X1 = 76, 560
Y0, Y1 = 20, 206

I_OS = 25e-12          # 입력 오프셋 전류 (A, typ) [1]
LIMIT_MV = 1.23        # 허용 입력 ±1.23 mV (이득 1100배, 3.3V 단전원)
ZE_LO, ZE_HI = 4.0, 10.0        # 10^4 ~ 10^10 Ω (사람 피부 50kΩ 도 들어오게)
MV_LO, MV_HI = -3.0, 2.4        # 10^-3 ~ 10^2.4 mV


def zx(z):
    return X0 + (math.log10(z) - ZE_LO) / (ZE_HI - ZE_LO) * (X1 - X0)


def vy(mv):
    return Y1 - (math.log10(mv) - MV_LO) / (MV_HI - MV_LO) * (Y1 - Y0)


def off_mv(z):
    return I_OS * z * 1000.0


Z_CROSS = LIMIT_MV / 1000.0 / I_OS      # 허용 한계를 넘는 임피던스

b = []

# 허용 범위 아래쪽 음영
b.append(f'<rect x="{X0}" y="{vy(LIMIT_MV):.1f}" width="{X1-X0}" '
         f'height="{Y1-vy(LIMIT_MV):.1f}" fill="{P["fill"]}"/>')

# 허용 한계 선
b.append(f'<line x1="{X0}" y1="{vy(LIMIT_MV):.1f}" x2="{X1}" y2="{vy(LIMIT_MV):.1f}" '
         f'stroke="{P["ink"]}" stroke-width="1" stroke-dasharray="4 3"/>')
b.append(f'<text x="{X0+6}" y="{vy(LIMIT_MV)-5:.1f}" font-size="9" fill="{P["ink"]}">'
         f'input limit ±1.23 mV</text>')

# V = I_os · Z 직선
b.append(f'<line x1="{zx(10**ZE_LO):.1f}" y1="{vy(off_mv(10**ZE_LO)):.1f}" '
         f'x2="{zx(10**ZE_HI):.1f}" y2="{vy(off_mv(10**ZE_HI)):.1f}" '
         f'stroke="{P["ink"]}" stroke-width="1.5"/>')

# 교점 — 이 그림의 결론
xc, yc = zx(Z_CROSS), vy(LIMIT_MV)
b.append(f'<line x1="{xc:.1f}" y1="{yc:.1f}" x2="{xc:.1f}" y2="{Y1}" '
         f'stroke="{P["accent"]}" stroke-width="0.9" stroke-dasharray="2 3"/>')
b.append(f'<circle cx="{xc:.1f}" cy="{yc:.1f}" r="3.6" fill="#fff" '
         f'stroke="{P["accent"]}" stroke-width="1.6"/>')
b.append(f'<text x="{xc+9:.1f}" y="{yc+14:.1f}" font-size="9.5" fill="{P["accent"]}" '
         f'font-weight="700">{esc(f"{Z_CROSS/1e6:.0f} MΩ")}</text>')

# 실제 전극이 놓이는 자리
for z, name in [(5e4, "human skin"), (1e9, "leaf surface")]:
    x, y = zx(z), vy(off_mv(z))
    b.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="3" fill="{P["accent2"]}"/>')
    dy = 14 if z < Z_CROSS else -8   # 아래쪽 점은 라벨을 선 밑으로
    b.append(f'<text x="{x:.1f}" y="{y+dy:.1f}" font-size="9" fill="{P["accent2"]}" '
             f'text-anchor="{"start" if z < Z_CROSS else "end"}">{esc(name)}</text>')

# 축
xt = [(e, zx(10.0 ** e)) for e in range(4, 11, 2)]
ZL = {4: "10 kΩ", 6: "1 MΩ", 8: "100 MΩ", 10: "10 GΩ"}
minor = [zx(m * 10.0 ** e) for e in range(4, 10) for m in range(2, 10)]
yt = [(e, vy(10.0 ** e)) for e in range(-3, 3)]
VL = {-3: "0.001", -2: "0.01", -1: "0.1", 0: "1", 1: "10", 2: "100"}
b.append(axes(X0, Y0, X1, Y1, xt, yt, "Electrode impedance (Ω)", "DC offset (mV)",
              xfmt=lambda e: ZL[e], yfmt=lambda e: VL[e], minor=minor))

lx0, ly0 = X0 + 8, Y0 + 14
# annotation
b.append(f'<text x="{lx0:.1f}" y="{ly0:.1f}" font-size="9">V = I_os · Zₑ,  I_os = 25 pA</text>')

open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "FIG_OFFSET.svg"),
     "w", encoding="utf-8").write(
    paper_svg(W, H, "".join(b), "전극 임피던스가 만드는 직류 오프셋"))
print("FIG_OFFSET.svg")
