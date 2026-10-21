from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator, RegexValidator
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


class Vendor(models.Model):
    """Internal department, supplier or vendor, identified by a 5-digit code."""
    class Kind(models.TextChoices):
        INTERNAL = 'INTERNAL', _('Internal')
        SUPPLIER = 'SUPPLIER', _('Supplier')
        VENDOR = 'VENDOR', _('Vendor')

    # Allowed code range (inclusive) per kind
    RANGES = {Kind.INTERNAL: (0, 99), Kind.SUPPLIER: (100, 429), Kind.VENDOR: (100, 429)}

    code = models.CharField(
        _('code'), max_length=5, unique=True,
        validators=[RegexValidator(r'^\d{5}$', _('The code must be exactly 5 digits.'))],
    )
    name = models.CharField(_('name'), max_length=200)
    name_ja = models.CharField(_('Japanese name'), max_length=200, blank=True)
    kind = models.CharField(_('kind'), max_length=20, choices=Kind.choices)
    lead_days = models.PositiveIntegerField(_('lead days'), null=True, blank=True)

    class Meta:
        verbose_name = _('vendor')
        verbose_name_plural = _('vendors')
        ordering = ['code']

    @property
    def display_name(self):
        return self.name_ja if self.name_ja and get_language().startswith('ja') else self.name

    def __str__(self):
        return f'{self.code} {self.display_name}'

    def clean(self):
        super().clean()
        if self.kind in self.RANGES and self.code.isdigit():
            low, high = self.RANGES[self.kind]
            if not low <= int(self.code) <= high:
                raise ValidationError({'code': _('Code %(code)s is outside the range %(low)05d-%(high)05d for this kind.') % {
                    'code': self.code, 'low': low, 'high': high}})


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
    default_vendor = models.ForeignKey(
        Vendor, on_delete=models.SET_NULL, null=True, blank=True, related_name='+', verbose_name=_('default vendor'),
    )
    stagnation_hours = models.PositiveIntegerField(
        _('stagnation hours'), null=True, blank=True, help_text=_('Blank = use the default.'),
    )
    active = models.BooleanField(_('active'), default=True)

    class Meta:
        verbose_name = _('process')
        verbose_name_plural = _('processes')

    def __str__(self):
        return self.name_ja if self.name_ja and get_language().startswith('ja') else self.name

    @property
    def vendor_label(self):
        """Default vendor as "code name", else the old free-text vendor name."""
        return str(self.default_vendor) if self.default_vendor else self.vendor_name


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
    material = models.CharField(_('material'), max_length=100, blank=True)
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
    def drawing_is_image(self):
        return self.drawing_file.name.lower().endswith(('.png', '.jpg', '.jpeg'))

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
    vendor = models.ForeignKey(Vendor, on_delete=models.PROTECT, null=True, blank=True, related_name='steps', verbose_name=_('vendor'))
    due_date = models.DateField(_('planned due date'), null=True, blank=True)
    slip_number = models.CharField(_('slip number'), max_length=50, blank=True)

    class Meta:
        verbose_name = _('route step')
        verbose_name_plural = _('route steps')
        ordering = ['sequence']
        constraints = [
            models.UniqueConstraint(fields=['item', 'sequence'], name='unique_item_sequence'),
        ]

    def __str__(self):
        return f'{self.item.item_code} #{self.sequence} {self.process}'

    @property
    def effective_vendor(self):
        return self.vendor or self.process.default_vendor

    @property
    def vendor_label(self):
        """"code name" of the step's vendor, falling back to the process default."""
        return str(self.effective_vendor) if self.effective_vendor else self.process.vendor_name

    @property
    def lead_days(self):
        """Vendor lead days, else the process's."""
        v = self.effective_vendor
        return v.lead_days if v and v.lead_days is not None else self.process.vendor_lead_days


class OrderChange(models.Model):
    """Append-only log of edits to an order or its items."""
    order = models.ForeignKey(Order, on_delete=models.PROTECT, related_name='changes', verbose_name=_('order'))
    item = models.ForeignKey(OrderItem, on_delete=models.SET_NULL, null=True, blank=True, related_name='changes', verbose_name=_('item'))
    item_code = models.CharField(_('item code'), max_length=40, blank=True)
    field = models.CharField(_('field'), max_length=40)
    old_value = models.TextField(_('before'), blank=True)
    new_value = models.TextField(_('after'), blank=True)
    changed_at = models.DateTimeField(_('changed at'), default=timezone.now)
    changed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, verbose_name=_('changed by'),
    )

    class Meta:
        verbose_name = _('order change')
        verbose_name_plural = _('order changes')
        ordering = ['-changed_at', '-pk']

    # Field name -> label shown in the change history
    LABELS = {'route': _('Route'), 'item_added': _('Item added'), 'item_deleted': _('Item deleted')}

    @property
    def field_label(self):
        if self.field in self.LABELS:
            return self.LABELS[self.field]
        model = OrderItem if self.item_code else Order
        return model._meta.get_field(self.field).verbose_name

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValidationError(_('The change history is append-only and cannot be changed.'))
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError(_('The change history is append-only and cannot be deleted.'))
