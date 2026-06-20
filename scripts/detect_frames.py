"""M3.6 帧布局探测:扫 public/sprites/*.png(竖向帧条)→ 推断每个精灵的帧宽/帧高/帧数,
写 src/game/sprite-frames.json 供 town.ts 正确切片(Phaser spritesheet)。

原理:帧条由"内容块 + 透明分隔行"组成。块起始间距(pitch)≈ 帧高;再取最接近 pitch 的
图高整除值作精确帧高(保证 总高 = 帧数 × 帧高)。单帧正方形图不写入(静态处理)。

改了精灵后重跑:python scripts/detect_frames.py
"""
import json
from pathlib import Path
from statistics import median

from PIL import Image

SPRITES = Path("public/sprites")
OUT = Path("src/game/sprite-frames.json")


def _block_starts(im: Image.Image) -> list[int]:
    w, h = im.size
    px = im.load()
    content = [any(px[x, y][3] != 0 for x in range(w)) for y in range(h)]
    starts = [y for y in range(h) if content[y] and (y == 0 or not content[y - 1])]
    return starts


def detect(path: Path):
    im = Image.open(path).convert("RGBA")
    w, h = im.size
    if h <= w:
        return None  # 单帧(正方形/横图)→ 静态,不写
    starts = _block_starts(im)
    if len(starts) < 2:
        return None
    spacings = [b - a for a, b in zip(starts, starts[1:])]
    pitch = median(spacings)
    # 取最接近 pitch 的"图高整除帧高"(范围 24..96)
    divs = [fh for fh in range(24, 97) if h % fh == 0]
    if not divs:
        return None
    frame_h = min(divs, key=lambda fh: abs(fh - pitch))
    return {"frameWidth": w, "frameHeight": frame_h, "frames": h // frame_h}


def main():
    meta = {}
    for p in sorted(SPRITES.glob("*.png")):
        d = detect(p)
        if d:
            meta[p.stem] = d
            print(f"  {p.stem:<14} {d['frameWidth']}x{d['frameHeight']} × {d['frames']} 帧")
    OUT.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"\n写入 {OUT}({len(meta)} 个帧条)")


if __name__ == "__main__":
    main()
