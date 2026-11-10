"""An item's drawing as raw RGB565 pixels, so the ESP32 setter can stream it straight onto its TFT."""
from PIL import Image, ImageChops, ImageOps

MAX_SIDE = 800  # bigger than any setter screen; keeps one reply under 1.3 MB
MAX_SOURCE_PIXELS = 50_000_000  # guards against decompression bombs (same limit as defect photos)


class DrawingUnreadable(Exception):
    """The drawing file could not be opened or rendered."""


def rgb565(img):
    """RGB image -> 2 bytes per pixel, high byte first, row by row (the byte order the ST7796 panel wants)."""
    r, g, b = img.split()
    # RRRRRGGG GGGBBBBB: the bits of each pair never overlap, so add() works as a bitwise OR
    hi = ImageChops.add(r.point(lambda v: v & 0xF8), g.point(lambda v: v >> 5))
    lo = ImageChops.add(g.point(lambda v: (v << 3) & 0xE0), b.point(lambda v: v >> 3))
    return Image.merge('LA', (hi, lo)).tobytes()


def _flat_rgb(img):
    """Any Pillow image -> RGB, transparent areas white."""
    if img.mode in ('RGBA', 'LA', 'P'):
        img = img.convert('RGBA')
        flat = Image.new('RGB', img.size, 'white')
        flat.paste(img, mask=img.getchannel('A'))
        return flat
    return img.convert('RGB')


def _image(f):
    with Image.open(f) as img:
        if img.width * img.height > MAX_SOURCE_PIXELS:
            raise DrawingUnreadable('image too large')
        return _flat_rgb(ImageOps.exif_transpose(img))


def _pdf(f, width, height):
    """First page, rendered at the scale that fits width x height (pdfium draws the vectors sharp at that size)."""
    try:
        import pypdfium2 as pdfium
    except ImportError:
        raise DrawingUnreadable('pypdfium2 is not installed on the server')
    pdf = pdfium.PdfDocument(f.read())
    try:
        page = pdf[0]
        page_w, page_h = page.get_size()
        return page.render(scale=min(width / page_w, height / page_h)).to_pil().convert('RGB')
    finally:
        pdf.close()


def drawing_rgb565(item, width, height):
    """The item's drawing fitted inside width x height (aspect kept, centred on white) as RGB565 bytes."""
    try:
        with item.drawing_file.open('rb') as f:
            img = _image(f) if item.drawing_is_image else _pdf(f, width, height)
    except DrawingUnreadable:
        raise
    except Exception as e:  # Pillow, pypdfium2.PdfiumError or a missing file: a broken drawing must not give a 500
        raise DrawingUnreadable(f'{type(e).__name__}: {e}')
    img = ImageOps.contain(img, (width, height), Image.LANCZOS)
    screen = Image.new('RGB', (width, height), 'white')
    screen.paste(img, ((width - img.width) // 2, (height - img.height) // 2))
    return rgb565(screen)
