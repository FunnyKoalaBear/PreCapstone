import tempfile
from datetime import date
from io import BytesIO

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from PIL import Image
from reportlab.pdfgen import canvas

from core.testing import TestCase
from orders.drawing_screen import rgb565
from orders.models import Customer, Order, OrderItem

from .models import ScannerDevice

WHITE, RED = b'\xff\xff', b'\xf8\x00'


def png(size, color):
    out = BytesIO()
    Image.new('RGB', size, color).save(out, 'PNG')
    return out.getvalue()


def pdf_with_a_thick_line():
    out = BytesIO()
    c = canvas.Canvas(out, pagesize=(400, 200))
    c.setLineWidth(20)
    c.line(0, 100, 400, 100)
    c.save()
    return out.getvalue()


def pixel(data, width, x, y):
    i = 2 * (y * width + x)
    return data[i:i + 2]


class Rgb565Tests(TestCase):
    def test_colours_pack_high_byte_first(self):
        colours = [(255, 255, 255), (0, 0, 0), (255, 0, 0), (0, 255, 0), (0, 0, 255), (8, 4, 8)]
        img = Image.new('RGB', (len(colours), 1))
        img.putdata(colours)
        self.assertEqual(rgb565(img), b'\xff\xff' b'\x00\x00' b'\xf8\x00' b'\x07\xe0' b'\x00\x1f' b'\x08\x21')


@override_settings(MEDIA_ROOT=tempfile.mkdtemp())
class DrawingPixelsApiTests(TestCase):
    def setUp(self):
        self.device = ScannerDevice.objects.create(name='Setter')
        order = Order.objects.create(customer=Customer.objects.create(name='C'), due_date=date(2026, 12, 1))
        self.item = OrderItem.objects.create(order=order, part_name='Shaft', quantity=1)
        self.key = {'HTTP_X_API_KEY': self.device.api_key}

    def get(self, pk=None, query='?w=40&h=20', **headers):
        return self.client.get(f'/api/items/{pk or self.item.pk}/drawing-pixels/{query}', **(headers or self.key))

    def attach(self, name, data):
        self.item.drawing_file.save(name, SimpleUploadedFile(name, data))

    def test_needs_an_active_key(self):
        self.assertEqual(self.get(HTTP_X_API_KEY='nope').status_code, 401)
        self.device.active = False
        self.device.save()
        self.assertEqual(self.get().status_code, 401)

    def test_size_must_be_in_range(self):
        for query in ('?w=0&h=20', '?w=40&h=801', '?w=abc'):
            r = self.get(query=query)
            self.assertEqual((r.status_code, r.json()['error']), (400, 'invalid'), query)

    def test_no_drawing_and_unknown_item_are_404(self):
        self.assertEqual(self.get().json()['error'], 'no_drawing')
        r = self.get(pk=99999)
        self.assertEqual((r.status_code, r.json()['error']), (404, 'no_item'))
        self.device.refresh_from_db()
        self.assertIsNotNone(self.device.last_seen)

    def test_image_is_fitted_and_centred_on_white(self):
        self.attach('a.png', png((40, 10), 'red'))  # 4:1 into 40x20: a 40x10 band in the middle
        r = self.get()
        self.assertEqual((r.status_code, r['Content-Type'], r['Content-Length']), (200, 'application/octet-stream', '1600'))
        data = r.content
        self.assertEqual(len(data), 40 * 20 * 2)
        self.assertEqual((pixel(data, 40, 0, 0), pixel(data, 40, 20, 10), pixel(data, 40, 39, 19)), (WHITE, RED, WHITE))

    def test_default_size_is_the_full_screen(self):
        self.attach('a.png', png((8, 8), 'red'))
        self.assertEqual(len(self.get(query='').content), 480 * 320 * 2)

    def test_pdf_first_page_is_rendered(self):
        self.attach('a.pdf', pdf_with_a_thick_line())
        data = self.get(query='?w=100&h=50').content
        self.assertEqual(len(data), 100 * 50 * 2)
        self.assertEqual(pixel(data, 100, 50, 5), WHITE)
        self.assertEqual(pixel(data, 100, 50, 25), b'\x00\x00')  # the line runs through the middle

    def test_broken_file_is_422_not_500(self):
        self.attach('a.png', b'not a png')
        r = self.get()
        self.assertEqual((r.status_code, r.json()['error']), (422, 'unreadable'))
        self.attach('b.pdf', b'%PDF-1.4 broken')
        self.assertEqual(self.get().status_code, 422)

    def test_only_get(self):
        r = self.client.post(f'/api/items/{self.item.pk}/drawing-pixels/', **self.key)
        self.assertEqual(r.status_code, 405)
