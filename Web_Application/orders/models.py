from django.core.validators import MinValueValidator
from django.db import models, transaction
from django.db.models import Max, Q
from django.utils import timezone
from django.utils.translation import get_language
from django.utils.translation import gettext_lazy as _

from .validators import drawing_upload_to, validate_drawing_extension, validate_drawing_size


class Customer(models.Model):
    name = models.CharField(_('name'), max_length=200, unique=True)
    contact = models.CharField(_('contact'), max_length=200, blank=True)
    created_at = models.DateTimeField(_('created at'), auto_now_add=True)

    class Meta:
        verbose_name = _('customer')
        verbose_name_plural = _('customers')

    def __str__(self):
        return self.name


class Process(models.Model):
    class Kind(models.TextChoices):
        MATERIAL = 'MATERIAL', _('Material')
        MACHINING = 'MACHINING', _('Machining')
        OUTSOURCED = 'OUTSOURCED', _('Outsourced')

    name = models.CharField(_('name'), max_length=100, unique=True)
    name_ja = models.CharField(_('Japanese name'), max_length=100, blank=True)
    kind = models.CharField(_('kind'), max_length=20, choices=Kind.choices)
    vendor_name = models.CharField(_('vendor name'), max_length=200, blank=True)
    vendor_lead_days = models.PositiveIntegerField(_('vendor lead days'), null=True, blank=True)
    stagnation_hours = models.PositiveIntegerField(
        _('stagnation hours'), null=True, blank=True, help_text=_('Blank = use the default.'),
    )

    class Meta:
        verbose_name = _('process')
        verbose_name_plural = _('processes')

    def __str__(self):
        return self.name_ja if self.name_ja and get_language().startswith('ja') else self.name


class Order(models.Model):
    job_number = models.CharField(_('job number'), max_length=20, unique=True, editable=False)
    customer = models.ForeignKey(Customer, on_delete=models.PROTECT, verbose_name=_('customer'))
    customer_order_number = models.CharField(_('customer order number'), max_length=100, blank=True)
    due_date = models.DateField(_('due date'))
    notes = models.TextField(_('notes'), blank=True)
    created_at = models.DateTimeField(_('created at'), auto_now_add=True)

    class Meta:
        verbose_name = _('order')
        verbose_name_plural = _('orders')
        constraints = [
            models.UniqueConstraint(
                fields=['customer', 'customer_order_number'],
                condition=~Q(customer_order_number=''),
                name='unique_customer_order_number',
            ),
        ]

    def __str__(self):
        return self.job_number

    def save(self, *args, **kwargs):
        if not self.job_number:
            # YYMM-NNN in JST; sequence restarts each month
            prefix = timezone.localtime().strftime('%y%m')
            with transaction.atomic():
                last = Order.objects.select_for_update().filter(
                    job_number__startswith=prefix + '-'
                ).aggregate(m=Max('job_number'))['m']
                seq = int(last.split('-')[1]) + 1 if last else 1
                self.job_number = f'{prefix}-{seq:03d}'
                return super().save(*args, **kwargs)
        return super().save(*args, **kwargs)


class OrderItem(models.Model):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name='items', verbose_name=_('order'))
    item_no = models.PositiveIntegerField(_('item no.'), editable=False)
    part_name = models.CharField(_('part name'), max_length=200)
    quantity = models.PositiveIntegerField(_('quantity'))
    box_count = models.PositiveIntegerField(_('box count'), default=1, validators=[MinValueValidator(1)])
    drawing_file = models.FileField(
        _('drawing file'), upload_to=drawing_upload_to, blank=True,
        validators=[validate_drawing_extension, validate_drawing_size],
    )

    class Meta:
        verbose_name = _('order item')
        verbose_name_plural = _('order items')
        ordering = ['order', 'item_no']
        constraints = [
            models.UniqueConstraint(fields=['order', 'item_no'], name='unique_order_item_no'),
        ]

    def __str__(self):
        return f'{self.item_code} {self.part_name}'

    @property
    def item_code(self):
        return f'{self.order.job_number}-{self.item_no:02d}'

    def save(self, *args, **kwargs):
        if not self.item_no:
            with transaction.atomic():
                last = OrderItem.objects.select_for_update().filter(
                    order=self.order
                ).aggregate(m=Max('item_no'))['m']
                self.item_no = (last or 0) + 1
                return super().save(*args, **kwargs)
        return super().save(*args, **kwargs)


class RouteStep(models.Model):
    item = models.ForeignKey(OrderItem, on_delete=models.CASCADE, related_name='steps', verbose_name=_('item'))
    sequence = models.PositiveIntegerField(_('sequence'))
    process = models.ForeignKey(Process, on_delete=models.PROTECT, verbose_name=_('process'))

    class Meta:
        verbose_name = _('route step')
        verbose_name_plural = _('route steps')
        ordering = ['sequence']
        constraints = [
            models.UniqueConstraint(fields=['item', 'sequence'], name='unique_item_sequence'),
        ]

    def __str__(self):
        return f'{self.item.item_code} #{self.sequence} {self.process}'
