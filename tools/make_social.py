"""Draws assets/social-preview.png (1280x640), the image GitHub shows when the repo link is shared."""
from pathlib import Path
from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = Path(__file__).resolve().parent.parent
W, H = 1280, 640
BG, ORANGE, LCD, LCD_HI, TEXT, MUTED = (11, 11, 11), (255, 130, 0), (255, 140, 41), (255, 166, 79), (241, 237, 232), (150, 143, 136)

img = Image.new("RGB", (W, H), BG)

# soft orange glow, top-left and behind the screen
glow = Image.new("RGB", (W, H), BG)
gd = ImageDraw.Draw(glow)
gd.ellipse((-260, -320, 520, 300), fill=(70, 34, 0))
gd.ellipse((640, 40, 1360, 640), fill=(55, 27, 0))
img = Image.blend(img, glow.filter(ImageFilter.GaussianBlur(140)), 1.0)
d = ImageDraw.Draw(img)
for y in range(0, H, 16):             # faint dot grid, like the app background
    for x in range(0, W, 16):
        d.point((x, y), fill=(26, 26, 26))

# ---- the device: bezel + orange LCD with the soldering dolphin, 4x
SCALE = 4
lcd_w, lcd_h = 128 * SCALE, 64 * SCALE
dx, dy = W - lcd_w - 96, 150
d.rounded_rectangle((dx - 44, dy - 66, dx + lcd_w + 44, dy + lcd_h + 150), radius=44, fill=(26, 26, 26), outline=(48, 48, 48), width=2)
d.rounded_rectangle((dx - 20, dy - 20, dx + lcd_w + 20, dy + lcd_h + 20), radius=22, fill=(5, 5, 5))
lcd = Image.new("RGB", (lcd_w, lcd_h), LCD)
ld = ImageDraw.Draw(lcd)
for i in range(lcd_h):                # gentle backlight gradient
    t = i / lcd_h
    ld.line((0, i, lcd_w, i), fill=tuple(int(LCD_HI[k] * (1 - t) + LCD[k] * t) for k in range(3)))
sheet = Image.open(ROOT / "ui" / "vendor" / "dolphin" / "soldering.png").convert("RGBA")
frame = sheet.crop((0, 0, 128, 64)).resize((lcd_w, lcd_h), Image.NEAREST)
lcd.paste(frame, (0, 0), frame)
# speech bubble, pixel style
bub = Image.new("RGBA", (128, 64), (0, 0, 0, 0))
font8 = ImageFont.truetype(str(ROOT / "ui" / "vendor" / "fonts" / "Silkscreen-Regular.ttf"), 8)
bd = ImageDraw.Draw(bub)
text = "BUILDING..."
tw = int(bd.textlength(text, font=font8))
bx, by, bw, bh = 128 - tw - 10, 1, tw + 7, 12
bd.rectangle((bx, by, bx + bw, by + bh), fill=LCD + (255,), outline=(0, 0, 0, 255))
bd.text((bx + 4, by + 2), text, font=font8, fill=(0, 0, 0, 255))
bub = bub.resize((lcd_w, lcd_h), Image.NEAREST)
lcd.paste(bub, (0, 0), bub)
img.paste(lcd, (dx, dy))
d = ImageDraw.Draw(img)
# LED + d-pad hints under the screen
d.ellipse((dx - 22, dy - 48, dx - 10, dy - 36), fill=ORANGE)
cx, cy = dx + lcd_w // 2 - 60, dy + lcd_h + 88
d.ellipse((cx - 46, cy - 46, cx + 46, cy + 46), fill=(36, 36, 36), outline=(52, 52, 52), width=2)
d.ellipse((cx - 17, cy - 17, cx + 17, cy + 17), fill=(58, 58, 58))
d.ellipse((cx + 96, cy - 20, cx + 136, cy + 20), fill=(36, 36, 36), outline=(52, 52, 52), width=2)

# ---- text block
pix = ImageFont.truetype(str(ROOT / "ui" / "vendor" / "fonts" / "PixelifySans.ttf"), 92)
try:
    pix.set_variation_by_axes([700])
except Exception:  # noqa: BLE001
    pass
seg = "C:/Windows/Fonts/seguisb.ttf"
body = ImageFont.truetype(seg, 34)
small = ImageFont.truetype("C:/Windows/Fonts/segoeuib.ttf", 21)
icon = Image.open(ROOT / "assets" / "icon.png").convert("RGBA").resize((88, 88), Image.LANCZOS)
x0 = 84
img.paste(icon, (x0, 92), icon)
d.text((x0, 196), "Flipper", font=pix, fill=TEXT)
d.text((x0, 290), "Builder", font=pix, fill=ORANGE)
d.text((x0, 410), "GitHub link in, app on your", font=body, fill=MUTED)
d.text((x0, 452), "Flipper Zero out.", font=body, fill=MUTED)
# feature chips
x = x0
for label in ("OFFICIAL", "MOMENTUM", "UNLEASHED", "USB INSTALL"):
    w = int(d.textlength(label, font=small)) + 28
    d.rounded_rectangle((x, 528, x + w, 568), radius=9, fill=(43, 26, 10), outline=(150, 78, 10), width=2)
    d.text((x + 14, 535), label, font=small, fill=(255, 160, 64))
    x += w + 12

out = ROOT / "assets" / "social-preview.png"
img.save(out, optimize=True)
print("wrote", out, img.size)
