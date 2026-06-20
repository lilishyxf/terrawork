"""M3.6 精灵预处理:白底原图 → 透明 + 裁切 + 补方 + 缩放 → public/sprites(或 portraits)。

用法:
    # 小精灵(默认 in=sprites_raw, out=public/sprites, size=64)
    python scripts/prep_sprites.py
    # 肖像(更大尺寸、单独目录)
    python scripts/prep_sprites.py --in portraits_raw --out public/portraits --size 256

流程(每张 PNG):
    1. 从四角 flood-fill 抠掉**连通的背景白**(thresh 容差)→ 透明;
       人物内部的浅色/白(地图、高光)不与边角连通,**不会被误伤**。
    2. 按非透明像素自动裁切到人物边界。
    3. 补成正方形(透明居中)。
    4. NEAREST 缩放到 size(像素风不糊)。
仅处理 *.png;跳过 README.md / manifest.json。原图建议白底方图(见提示词模板)。
"""
import argparse
from pathlib import Path

from PIL import Image, ImageDraw

_SENTINEL = (255, 0, 255)  # 抠图哨兵色(品红);原图勿用纯品红背景


def _strip_white_bg(img: Image.Image, thresh: int) -> Image.Image:
    """四角 flood-fill 连通背景白 → 透明。返回 RGBA。"""
    rgb = img.convert("RGB")
    w, h = rgb.size
    for corner in [(0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1)]:
        # 仅当该角确实接近白才填(避免误抠深色背景图)
        if sum(rgb.getpixel(corner)) >= 255 * 3 - thresh * 3:
            ImageDraw.floodfill(rgb, corner, _SENTINEL, thresh=thresh)
    rgba = rgb.convert("RGBA")
    px = rgba.load()
    w2, h2 = rgba.size
    for y in range(h2):
        for x in range(w2):
            r, g, b, _ = px[x, y]
            if (r, g, b) == _SENTINEL:
                px[x, y] = (0, 0, 0, 0)  # 背景哨兵 → 透明
    return rgba


def _square_pad(img: Image.Image) -> Image.Image:
    """裁到内容边界后补成正方形(透明居中)。"""
    bbox = img.getbbox()
    if bbox:
        img = img.crop(bbox)
    w, h = img.size
    side = max(w, h)
    canvas = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    canvas.paste(img, ((side - w) // 2, (side - h) // 2), img)
    return canvas


def prep_one(src: Path, dst: Path, size: int, thresh: int) -> None:
    img = Image.open(src)
    img = _strip_white_bg(img, thresh)
    img = _square_pad(img)
    img = img.resize((size, size), Image.NEAREST)
    dst.parent.mkdir(parents=True, exist_ok=True)
    img.save(dst)


def main() -> None:
    ap = argparse.ArgumentParser(description="白底原图 → 透明精灵/肖像")
    ap.add_argument("--in", dest="indir", default="sprites_raw", help="原图目录(白底 PNG)")
    ap.add_argument("--out", dest="outdir", default="public/sprites", help="输出目录")
    ap.add_argument("--size", type=int, default=64, help="输出方形边长(px)")
    ap.add_argument("--thresh", type=int, default=40, help="白底容差(越大抠得越狠)")
    args = ap.parse_args()

    indir, outdir = Path(args.indir), Path(args.outdir)
    if not indir.is_dir():
        raise SystemExit(f"原图目录不存在:{indir}(把白底 PNG 按 <key>.png 命名放进去)")
    skip = {"readme.md", "manifest.json"}
    pngs = [p for p in sorted(indir.glob("*.png")) if p.name.lower() not in skip]
    if not pngs:
        raise SystemExit(f"{indir} 里没有 *.png")
    for src in pngs:
        dst = outdir / src.name
        prep_one(src, dst, args.size, args.thresh)
        print(f"  ✓ {src.name} → {dst}  ({args.size}x{args.size})")
    print(f"\n完成 {len(pngs)} 张 → {outdir}")


if __name__ == "__main__":
    main()
