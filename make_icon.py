"""生成 Pearl 守护器 V2.2.0 的安装和程序图标。"""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


def main() -> None:
    size = 512
    image = Image.new("RGBA", (size, size), "#0A66D8")
    draw = ImageDraw.Draw(image)
    for y in range(size):
        scale = y / (size - 1)
        color = (
            int(18 + 74 * scale),
            int(120 - 18 * scale),
            int(230 - 28 * scale),
            255,
        )
        draw.line((0, y, size, y), fill=color)
    draw.ellipse((62, 46, 450, 434), fill=(225, 244, 255, 185))
    draw.ellipse((100, 84, 412, 396), fill=(15, 108, 230, 255))
    try:
        font = ImageFont.truetype(r"C:\Windows\Fonts\segoeuib.ttf", 300)
    except OSError:
        font = ImageFont.load_default()
    box = draw.textbbox((0, 0), "P", font=font)
    x = (size - (box[2] - box[0])) / 2
    y = (size - (box[3] - box[1])) / 2 - 18
    draw.text((x, y), "P", font=font, fill="white")
    out = Path(__file__).resolve().parent / "assets" / "pearl.ico"
    out.parent.mkdir(parents=True, exist_ok=True)
    image.save(out, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    image.save(out.with_suffix(".png"))


if __name__ == "__main__":
    main()
