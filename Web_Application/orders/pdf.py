import io
from xml.sax.saxutils import escape

from django.conf import settings
from django.utils.text import capfirst
from django.utils.translation import gettext as _
from pypdf import PdfReader, PdfWriter
from reportlab.graphics import renderPDF
from reportlab.graphics.barcode.qr import QrCodeWidget
from reportlab.graphics.shapes import Drawing
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from .models import OrderItem

FONT = 'NotoSansJP'
FONT_FILE = settings.BASE_DIR / 'static' / 'fonts' / 'NotoSansJP.ttf'
PAGE = landscape(A4)
MARGIN = 12 * mm
QR_SIZE = 24 * mm
ROW_HEIGHT = 9 * mm
_font_ready = False


def _register_font():
    global _font_ready
    if not _font_ready:
        pdfmetrics.registerFont(TTFont(FONT, str(FONT_FILE)))
        _font_ready = True


def _style(size, **kw):
    return ParagraphStyle('s', fontName=FONT, fontSize=size, leading=size * 1.35, wordWrap='CJK', **kw)


def _p(text, size=9, **kw):
    return Paragraph(escape(str(text)), _style(size, **kw))


def _draw_qr(c, value):
    widget = QrCodeWidget(value)
    x0, y0, x1, y1 = widget.getBounds()
    d = Drawing(QR_SIZE, QR_SIZE, transform=[QR_SIZE / (x1 - x0), 0, 0, QR_SIZE / (y1 - y0), 0, 0])
    d.add(widget)
    renderPDF.draw(d, c, PAGE[0] - MARGIN - QR_SIZE, PAGE[1] - MARGIN - QR_SIZE)


def _sheet(item):
    """The front sheet of one item as PDF bytes: details, route table with blank boxes to fill in, QR of the item code."""
    order, buf = item.order, io.BytesIO()
    info = [
        [_p(_('Job number'), 8, textColor=colors.grey), _p(item.item_code, 11), _p(_('Customer'), 8, textColor=colors.grey), _p(order.customer, 11)],
        [_p(_('Customer order number'), 8, textColor=colors.grey), _p(order.customer_order_number or '-', 11),
         _p(_('Part name'), 8, textColor=colors.grey), _p(item.part_name, 11)],
        [_p(capfirst(OrderItem._meta.get_field('material').verbose_name), 8, textColor=colors.grey), _p(item.material or '-', 11),
         _p(_('Due date'), 8, textColor=colors.grey), _p(f'{order.due_date:%Y-%m-%d}', 11)],
        [_p(_('Part number'), 8, textColor=colors.grey), _p(item.part_number or '-', 11),
         _p(_('Drawing number / Revision'), 8, textColor=colors.grey), _p(f"{item.drawing_number or '-'} / {item.revision or '-'}", 11)],
        [_p(_('Quantity'), 8, textColor=colors.grey), _p(item.quantity, 11), _p(_('Notes'), 8, textColor=colors.grey), _p(order.notes or '-', 9)],
    ]
    head = [_('Step'), _('Process'), _('Code / name'), _('Planned due'), _('Quantity'), _('Date'), _('Worker'), _('Notes'), _('Actual qty'), _('Defects')]
    rows = [[_p(h, 8, textColor=colors.grey) for h in head]]
    for s in item.steps.select_related('process', 'vendor', 'process__default_vendor'):
        rows.append([
            _p(s.sequence, 9), _p(s.process, 9), _p(s.vendor_label or '-', 8), _p(f'{s.due_date:%Y-%m-%d}' if s.due_date else '', 8),
            _p(item.quantity, 9), '', '', '', '', '',
        ])
    widths = [12, 40, 52, 24, 16, 24, 30, 45, 16, 16]
    scale = (PAGE[0] - 2 * MARGIN) / (sum(widths) * mm)
    table = Table(rows, colWidths=[w * mm * scale for w in widths], rowHeights=[None] + [ROW_HEIGHT] * (len(rows) - 1), repeatRows=1)
    table.setStyle(TableStyle([('GRID', (0, 0), (-1, -1), 0.5, colors.black), ('VALIGN', (0, 0), (-1, -1), 'MIDDLE')]))
    top = Table(info, colWidths=[38 * mm, 70 * mm, 28 * mm, 90 * mm], hAlign='LEFT')
    top.setStyle(TableStyle([('VALIGN', (0, 0), (-1, -1), 'TOP'), ('BOTTOMPADDING', (0, 0), (-1, -1), 2), ('TOPPADDING', (0, 0), (-1, -1), 2)]))
    doc = SimpleDocTemplate(buf, pagesize=PAGE, leftMargin=MARGIN, rightMargin=MARGIN, topMargin=MARGIN, bottomMargin=MARGIN)
    story = [_p(_('Work instruction sheet'), 16), Spacer(1, 3 * mm), top, Spacer(1, 6 * mm), table]
    doc.build(story, onFirstPage=lambda c, d: _draw_qr(c, item.item_code))
    return buf.getvalue()


def _blank():
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=PAGE)
    c.showPage()
    c.save()
    return PdfReader(io.BytesIO(buf.getvalue())).pages


def _image_page(f):
    """One landscape page with the image scaled to fit."""
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=PAGE)
    img = ImageReader(f)
    w, h = img.getSize()
    scale = min((PAGE[0] - 2 * MARGIN) / w, (PAGE[1] - 2 * MARGIN) / h)
    c.drawImage(img, (PAGE[0] - w * scale) / 2, (PAGE[1] - h * scale) / 2, w * scale, h * scale)
    c.showPage()
    c.save()
    return PdfReader(io.BytesIO(buf.getvalue())).pages


def _message_page(text):
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=PAGE)
    c.setFont(FONT, 12)
    c.drawString(MARGIN, PAGE[1] - MARGIN - 12, text)
    c.showPage()
    c.save()
    return PdfReader(io.BytesIO(buf.getvalue())).pages


def _drawing_pages(item):
    """Pages of the item's drawing: a PDF is appended as is, png/jpg scaled to fit; unreadable files give a note page."""
    try:
        with item.drawing_file.open('rb') as f:
            if item.drawing_is_image:
                return list(_image_page(f))
            reader = PdfReader(io.BytesIO(f.read()))
            if reader.is_encrypted and not reader.decrypt(''):
                raise ValueError('encrypted')
            return list(reader.pages)
    except Exception:
        return list(_message_page(_('The drawing could not be read.')))


def _item_pages(item, extra_image=None):
    """Pages of one item: its sheet, then (if it has a drawing) the drawing on the back, padded to an even count."""
    pages = list(PdfReader(io.BytesIO(_sheet(item))).pages)
    if item.drawing_file:
        if len(pages) % 2 == 0:
            pages += _blank()
        pages += _drawing_pages(item)
        if len(pages) % 2:
            pages += _blank()
    if extra_image is not None:
        pages += _image_page(extra_image)
    return pages


def _write(pages):
    writer = PdfWriter()
    for p in pages:
        writer.add_page(p)
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def order_pdf(order):
    """Work instruction sheets for every item of the order; each drawing follows its sheet so duplex printing puts it on the back."""
    _register_font()
    return _write([p for item in order.items.select_related('order__customer') for p in _item_pages(item)])


def item_pdf(item, extra_image=None):
    """The work instruction sheet, optional item drawing, and optional extra image as PDF bytes."""
    _register_font()
    return _write(_item_pages(item, extra_image))
