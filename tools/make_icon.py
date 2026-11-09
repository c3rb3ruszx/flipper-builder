"""Draws assets/icon.ico (and icon.png): orange tile, dark handheld with a screen and d-pad, build arrow."""
from pathlib import Path
from PIL import Image, ImageDraw

S = 1024
ORANGE, DARK, SCREEN, LIGHT = (255, 130, 0), (28, 31, 38), (255, 170, 70), (250, 250, 250)
img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
d = ImageDraw.Draw(img)

d.rounded_rectangle((40, 40, S - 40, S - 40), radius=210, fill=ORANGE)
# handheld body
d.rounded_rectangle((150, 300, 874, 760), radius=150, fill=DARK)
# screen
d.rounded_rectangle((235, 385, 570, 640), radius=34, fill=SCREEN)
# pixel-art down arrow on the screen (the "build" part)
px = 32
cx, top = 402, 418
for row, width in enumerate([2, 2, 2, 6, 4, 2]):
    for col in range(width):
        x = cx + int((col - width / 2) * px)
        y = top + row * px
        d.rectangle((x, y, x + px - 4, y + px - 4), fill=DARK)
# d-pad ring + center button
d.ellipse((620, 405, 820, 605), outline=LIGHT, width=34)
d.ellipse((690, 475, 750, 535), fill=LIGHT)

out = Path(__file__).resolve().parent.parent / "assets"
out.mkdir(exist_ok=True)
img.resize((256, 256), Image.LANCZOS).save(out / "icon.png")
img.save(out / "icon.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
print("wrote", out / "icon.ico")
