"""e2e 실험용 합성 샘플 사진 생성기.

실제 현장 사진이 없어도 파이프라인을 돌릴 수 있게 '복도 바닥 타일 파손'
장면을 그린다. 판독 품질을 보려면 진짜 사진을 --photo 로 넣을 것.
"""

from __future__ import annotations

import random
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

OUT = Path(__file__).resolve().parent / "sample.jpg"
W, H = 1024, 768


def main() -> None:
    random.seed(7)
    img = Image.new("RGB", (W, H), (176, 172, 165))
    d = ImageDraw.Draw(img)

    # 원근이 들어간 복도 바닥 타일 격자
    horizon = 200
    d.rectangle([0, 0, W, horizon], fill=(198, 196, 190))          # 벽
    d.rectangle([0, horizon - 6, W, horizon], fill=(120, 118, 112))  # 걸레받이
    for i in range(14):
        t = i / 13
        y = int(horizon + (H - horizon) * (t ** 1.9))
        d.line([(0, y), (W, y)], fill=(150, 146, 140), width=2)
    for i in range(-8, 9):
        x_top = W // 2 + i * 26
        x_bottom = W // 2 + i * 150
        d.line([(x_top, horizon), (x_bottom, H)], fill=(150, 146, 140), width=2)

    # 파손된 타일 두 장 — 균열과 결손부
    tile = [(360, 560), (700, 560), (790, 730), (300, 730)]
    d.polygon(tile, fill=(168, 163, 156))
    crack = [(400, 730), (470, 660), (520, 690), (560, 610), (600, 640), (640, 560)]
    d.line(crack, fill=(58, 54, 50), width=7, joint="curve")
    d.polygon([(470, 660), (520, 690), (560, 610), (505, 622)], fill=(72, 66, 60))  # 결손부
    # 돌출된 파편
    d.polygon([(560, 610), (640, 560), (668, 600), (585, 646)], fill=(198, 194, 188))
    d.line([(560, 610), (640, 560)], fill=(90, 86, 80), width=5)
    # 파편 조각
    for _ in range(18):
        x = random.randint(420, 700)
        y = random.randint(600, 720)
        r = random.randint(2, 6)
        d.ellipse([x, y, x + r, y + r], fill=(126, 121, 114))

    img = img.filter(ImageFilter.GaussianBlur(0.6))
    img.save(OUT, quality=88)
    print(f"샘플 사진 생성: {OUT}")


if __name__ == "__main__":
    main()
