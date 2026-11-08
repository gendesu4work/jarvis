"""Draw the Jarvis app icon (glowing hex core) -> jarvis.ico + hud/icon.png."""
import math, os
from PIL import Image, ImageDraw, ImageFilter

HERE = os.path.dirname(os.path.abspath(__file__))
S = 1024
C = (88, 214, 255)

def hexagon(cx, cy, r, rot=-90):
    return [(cx + r * math.cos(math.radians(rot + 60 * i)), cy + r * math.sin(math.radians(rot + 60 * i))) for i in range(6)]

img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
# dark rounded tile
tile = Image.new("RGBA", (S, S), (0, 0, 0, 0))
ImageDraw.Draw(tile).rounded_rectangle([40, 40, S - 40, S - 40], radius=210, fill=(4, 12, 28, 255),
                                       outline=(*C, 90), width=10)
img.alpha_composite(tile)

glow = Image.new("RGBA", (S, S), (0, 0, 0, 0))
g = ImageDraw.Draw(glow)
g.ellipse([S/2 - 300, S/2 - 300, S/2 + 300, S/2 + 300], fill=(*C, 70))
g.ellipse([S/2 - 150, S/2 - 150, S/2 + 150, S/2 + 150], fill=(*C, 160))
img.alpha_composite(glow.filter(ImageFilter.GaussianBlur(90)))

lines = Image.new("RGBA", (S, S), (0, 0, 0, 0))
d = ImageDraw.Draw(lines)
d.polygon(hexagon(S/2, S/2, 360), outline=(*C, 255), width=26)
d.polygon(hexagon(S/2, S/2, 220), outline=(*C, 170), width=16)
for i in range(6):                       # spokes
    a = math.radians(-90 + 60 * i)
    d.line([(S/2 + 230 * math.cos(a), S/2 + 230 * math.sin(a)), (S/2 + 350 * math.cos(a), S/2 + 350 * math.sin(a))],
           fill=(*C, 200), width=12)
img.alpha_composite(lines.filter(ImageFilter.GaussianBlur(14)))
img.alpha_composite(lines)

core = Image.new("RGBA", (S, S), (0, 0, 0, 0))
ImageDraw.Draw(core).ellipse([S/2 - 80, S/2 - 80, S/2 + 80, S/2 + 80], fill=(235, 250, 255, 255))
img.alpha_composite(core.filter(ImageFilter.GaussianBlur(30)))
img.alpha_composite(core.filter(ImageFilter.GaussianBlur(6)))

img.resize((256, 256), Image.LANCZOS).save(os.path.join(HERE, "hud", "icon.png"))
img.save(os.path.join(HERE, "jarvis.ico"), sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
print("icon ok")
