"""Make the Setter's Japanese bitmap fonts (jp16.bin, jp24.bin) from Noto Sans JP.

Run again only when the character set or sizes change (needs Pillow, e.g. the Web_Application venv):
    Web_Application/.venv/Scripts/python Electronics/Setter/tools/make_jp_font.py
    Web_Application/.venv/Scripts/python Electronics/Setter/tools/make_jp_font.py --preview preview.png

File format (little-endian), read by components/setter_gui/canvas.cpp:
    uint32 count, uint32 size, uint16 codepoints[count] (sorted), then count bitmaps of
    size rows x ceil(size / 8) bytes, most significant bit = leftmost pixel.
"""
import argparse
import re
import struct
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

HERE = Path(__file__).resolve().parent
SETTER = HERE.parent
REPO = SETTER.parent.parent
FONT = REPO / 'Web_Application' / 'static' / 'fonts' / 'NotoSansJP.ttf'
OUT = SETTER / 'components' / 'setter_gui' / 'fonts'
SIZES = {16: 'Bold', 24: 'Medium'}   # thin strokes vanish at 1 bit per pixel, so heavier weights


def charset():
    """Every JIS X 0208 character (kana, kanji level 1 and 2, symbols), half-width katakana, and the UI's own strings."""
    chars = set()
    for hi in range(0xA1, 0xFF):
        for lo in range(0xA1, 0xFF):
            try:
                chars.add(bytes([hi, lo]).decode('euc_jp'))
            except UnicodeDecodeError:
                pass
    chars.update(chr(c) for c in range(0xFF61, 0xFFA0))
    for src in (SETTER / 'components' / 'setter_gui').glob('*.cpp'):
        chars.update(re.findall(r'[^\x00-\x7f]', src.read_text(encoding='utf-8')))
    return sorted(c for c in chars if 0x80 <= ord(c) <= 0xFFFF)


def render(ch, size, font):
    img = Image.new('1', (size, size), 0)
    ImageDraw.Draw(img).text((size / 2, round(size * 0.88)), ch, fill=1, font=font, anchor='ms')
    return img


def pack(img, size):
    row_bytes = (size + 7) // 8
    out = bytearray()
    for y in range(size):
        for b in range(row_bytes):
            byte = 0
            for bit in range(8):
                x = b * 8 + bit
                if x < size and img.getpixel((x, y)):
                    byte |= 0x80 >> bit
            out.append(byte)
    return bytes(out)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--preview', help='also save a PNG of some sample text')
    args = parser.parse_args()

    chars = charset()
    OUT.mkdir(parents=True, exist_ok=True)
    fonts = {}
    for size, weight in SIZES.items():
        font = ImageFont.truetype(str(FONT), size)
        font.set_variation_by_name(weight)
        fonts[size] = font
        data = bytearray(struct.pack('<II', len(chars), size))
        data += b''.join(struct.pack('<H', ord(c)) for c in chars)
        data += b''.join(pack(render(c, size, font), size) for c in chars)
        (OUT / f'jp{size}.bin').write_bytes(bytes(data))
        print(f'jp{size}.bin: {len(chars)} characters, {len(data) // 1024} KB')

    if args.preview:
        sample = 'カードをかざしてください 紐付け完了 品名 得意先 納期 工程 指示書を印刷中'
        width = max(s * len(sample) for s in SIZES)
        sheet = Image.new('1', (width, sum(s + 4 for s in SIZES)), 0)
        y = 0
        for s in SIZES:
            for i, c in enumerate(sample):
                sheet.paste(render(c, s, fonts[s]), (i * s, y))
            y += s + 4
        sheet.resize((width * 3, sheet.height * 3), Image.NEAREST).save(args.preview)
        print('preview saved to', args.preview)


if __name__ == '__main__':
    main()
