"""把竖向帧条拆成带帧号的拼图,便于人工识别"走路帧/举手帧"范围。

    python scripts/frame_montage.py guide          # 出 frame_montage_guide.png
    python scripts/frame_montage.py guide --cols 8

看图后把"走路是第几到第几帧、举手是第几到第几帧"告诉我,我写进 sprite-frames.json。
"""
import argparse
import json
from pathlib import Path

from PIL import Image, ImageDraw

SPRITES = Path("public/sprites")
META = json.loads((Path("src/game/sprite-frames.json")).read_text(encoding="utf-8"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("key", help="sprite_key,如 guide")
    ap.add_argument("--cols", type=int, default=8)
    ap.add_argument("--scale", type=int, default=3)
    args = ap.parse_args()

    m = META[args.key]
    fw, fh, n = m["frameWidth"], m["frameHeight"], m["frames"]
    sheet = Image.open(SPRITES / f"{args.key}.png").convert("RGBA")

    cell_w, cell_h = fw * args.scale, fh * args.scale + 16  # +16 放帧号
    cols = args.cols
    rows = (n + cols - 1) // cols
    canvas = Image.new("RGBA", (cols * cell_w, rows * cell_h), (40, 40, 48, 255))
    draw = ImageDraw.Draw(canvas)
    for i in range(n):
        frame = sheet.crop((0, i * fh, fw, (i + 1) * fh)).resize(
            (fw * args.scale, fh * args.scale), Image.NEAREST)
        cx, cy = (i % cols) * cell_w, (i // cols) * cell_h
        draw.text((cx + 2, cy + 2), str(i), fill=(255, 220, 80, 255))
        canvas.alpha_composite(frame, (cx, cy + 14))
    out = Path(f"frame_montage_{args.key}.png")
    canvas.save(out)
    print(f"{args.key}: {n} 帧({fw}x{fh})→ {out}  (cols={cols})")


if __name__ == "__main__":
    main()
