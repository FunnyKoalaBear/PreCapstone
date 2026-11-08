import random

from django.conf import settings
from PIL import Image, ImageDraw

DEMO_DIR = 'defects/demo'  # under MEDIA_ROOT; seed_demo --reset removes this folder


def demo_photo(n, label):
    """Write a small deterministic JPEG of a metal part with a defect mark and return its name under MEDIA_ROOT."""
    rng = random.Random(n)
    img = Image.new('RGB', (640, 480), (70 + n * 6, 74 + n * 4, 80))
    draw = ImageDraw.Draw(img)
    x, y = 120 + rng.randrange(40), 100 + rng.randrange(40)
    draw.rounded_rectangle((x, y, x + 360, y + 240), 18, fill=(176, 182, 190), outline=(230, 232, 236), width=4)
    draw.ellipse((x + 150, y + 90, x + 210, y + 150), fill=(70, 74, 80))
    sx, sy = x + 40 + rng.randrange(120), y + 30 + rng.randrange(60)
    draw.line((sx, sy, sx + 120 + rng.randrange(60), sy + 40 + rng.randrange(40)), fill=(120, 60, 50), width=5)
    draw.text((16, 16), f'DEMO {n:02d} {label}', fill=(240, 240, 240))
    folder = settings.MEDIA_ROOT / DEMO_DIR
    folder.mkdir(parents=True, exist_ok=True)
    name = f'{DEMO_DIR}/demo-{n:02d}.jpg'
    img.save(settings.MEDIA_ROOT / name, 'JPEG', quality=85)
    return name


def demo_drawing(label):
    """Write a one-page demo drawing PDF and return its name under MEDIA_ROOT."""
    from reportlab.pdfgen import canvas
    folder = settings.MEDIA_ROOT / DEMO_DIR
    folder.mkdir(parents=True, exist_ok=True)
    name = f'{DEMO_DIR}/drawing-{label}.pdf'
    c = canvas.Canvas(str(settings.MEDIA_ROOT / name), invariant=True)
    c.rect(100, 300, 300, 200)
    c.circle(250, 400, 40)
    c.drawString(100, 520, f'DEMO DRAWING {label}')
    c.save()
    return name
