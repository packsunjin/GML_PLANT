"""
spectro_render.py
==================
Sxx_db(주파수 x 시간) 스펙트로그램 배열을 matplotlib Figure/Axes/Canvas 렌더링 없이
viridis 컬러맵 룩업 + PIL 리사이즈만으로 224x224 grayscale 이미지로 변환한다.

기존에는 preprocess.py/inference.py 각각이 pcolormesh -> Figure 렌더링 -> (PNG 저장 또는
캔버스 버퍼 읽기) 과정을 거쳤는데, 이 렌더링 자체가 스펙트로그램 1장당 수 ms~십수 ms가
걸리는 가장 무거운 단계였다. 여기서는 컬러맵을 256단계 룩업 테이블로 미리 구워두고
배열 인덱싱으로 바로 색을 입힌 뒤 리사이즈만 하므로 훨씬 빠르다.

preprocess.py(데이터셋 생성)와 inference.py(실시간 추론)가 반드시 동일한 함수를 사용해야
학습 때 특징과 추론 때 특징이 일치한다.
"""

import matplotlib
import numpy as np
from PIL import Image

IMG_SIZE = 224

_VIRIDIS_LUT = None


def _viridis_lut():
    """256단계 viridis RGB 룩업 테이블을 최초 1회만 생성해 재사용한다."""
    global _VIRIDIS_LUT
    if _VIRIDIS_LUT is None:
        cmap = matplotlib.colormaps["viridis"]
        _VIRIDIS_LUT = (cmap(np.linspace(0.0, 1.0, 256))[:, :3] * 255).astype(np.uint8)
    return _VIRIDIS_LUT


def _colorize(Sxx_db, img_size):
    vmin = float(Sxx_db.min())
    vmax = float(Sxx_db.max())
    if vmax > vmin:
        norm = (Sxx_db - vmin) / (vmax - vmin)
    else:
        norm = np.zeros_like(Sxx_db)
    idx = np.clip(norm * 255.0, 0, 255).astype(np.uint8)

    rgb = _viridis_lut()[idx]  # (n_freq, n_time, 3)
    # pcolormesh(t, f, Sxx_db)는 f(주파수)가 아래->위로 증가하는 축이지만,
    # 이미지 배열은 행(row) 0이 위쪽이므로 세로로 뒤집어 동일한 방향으로 맞춘다.
    rgb = rgb[::-1, :, :]

    img = Image.fromarray(rgb, mode="RGB")
    if img.size != (img_size, img_size):
        img = img.resize((img_size, img_size), resample=Image.BILINEAR)
    return img


def render_rgb_image(Sxx_db, img_size=IMG_SIZE):
    """Sxx_db 배열 -> viridis 컬러맵 적용된 img_size x img_size 컬러 PIL.Image 반환
    (데이터셋 PNG 저장 등 시각화용. 기존과 동일하게 컬러 스펙트로그램을 남긴다)."""
    return _colorize(Sxx_db, img_size=img_size)


def render_gray_image(Sxx_db, img_size=IMG_SIZE):
    """Sxx_db 배열 -> viridis 컬러맵 적용 -> img_size x img_size grayscale PIL.Image 반환."""
    return _colorize(Sxx_db, img_size=img_size).convert("L")


def render_gray_array(Sxx_db, img_size=IMG_SIZE):
    """render_gray_image()의 [0,1] 정규화된 float32 flatten 배열 버전 (모델 입력 특징용)."""
    img = render_gray_image(Sxx_db, img_size=img_size)
    return np.asarray(img, dtype=np.float32).flatten() / 255.0
