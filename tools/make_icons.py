"""Генерирует иконки надстройки (16/32/80) и tray (64): знак «§» на синей плашке."""

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
TARGETS = {
    ROOT / "addin" / "src" / "taskpane" / "assets": (16, 32, 80),
    ROOT / "tray" / "assets": (64,),
}
BLUE = (16, 82, 150, 255)


def font(size: int) -> ImageFont.ImageFont:
    for name in ("seguisb.ttf", "segoeui.ttf", "arialbd.ttf", "DejaVuSans-Bold.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def icon(size: int) -> Image.Image:
    scale = 4  # рисуем крупно и уменьшаем — сглаженные края
    big = size * scale
    img = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle((0, 0, big - 1, big - 1), radius=big // 5, fill=BLUE)
    f = font(int(big * 0.78))
    box = draw.textbbox((0, 0), "§", font=f)
    x = (big - (box[2] - box[0])) / 2 - box[0]
    y = (big - (box[3] - box[1])) / 2 - box[1]
    draw.text((x, y), "§", font=f, fill=(255, 255, 255, 255))
    return img.resize((size, size), Image.LANCZOS)


if __name__ == "__main__":
    for folder, sizes in TARGETS.items():
        folder.mkdir(parents=True, exist_ok=True)
        for size in sizes:
            path = folder / f"icon-{size}.png"
            icon(size).save(path)
            print(path.relative_to(ROOT))
    ico = ROOT / "tray" / "assets" / "app.ico"  # иконка exe и строки в «Установленных программах»
    icon(256).save(ico, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    print(ico.relative_to(ROOT))
