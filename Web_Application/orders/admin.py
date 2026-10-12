from django.contrib import admin
from django.utils.translation import gettext_lazy as _

from tracking.status import item_status, order_status

from .models import Customer, Order, OrderChange, OrderItem, Process, RouteStep


class OrderItemInline(admin.TabularInline):
    model = OrderItem
    extra = 1
    fields = ['part_name', 'quantity', 'box_count', 'drawing_file', 'item_no']
    readonly_fields = ['item_no']


class RouteStepInline(admin.TabularInline):
    model = RouteStep
    extra = 1


@admin.register(Customer)
class CustomerAdmin(admin.ModelAdmin):
    list_display = ['name', 'contact', 'created_at']
    search_fields = ['name']


@admin.register(Process)
class ProcessAdmin(admin.ModelAdmin):
    list_display = ['name', 'name_ja', 'kind', 'active', 'vendor_name', 'vendor_lead_days', 'stagnation_hours']
    list_filter = ['kind', 'active']
    actions = ['mark_inactive', 'mark_active']

    @admin.action(description=_('Mark selected processes as inactive'))
    def mark_inactive(self, request, queryset):
        queryset.update(active=False)

    @admin.action(description=_('Reactivate selected processes'))
    def mark_active(self, request, queryset):
        queryset.update(active=True)


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = ['job_number', 'customer', 'customer_order_number', 'due_date', 'status']
    readonly_fields = ['job_number']
    inlines = [OrderItemInline]

    @admin.display(description=_('status'))
    def status(self, obj):
        return order_status(obj)


@admin.register(OrderItem)
class OrderItemAdmin(admin.ModelAdmin):
    list_display = ['item_code', 'part_name', 'quantity', 'status']
    fields = ['order', 'part_name', 'quantity', 'box_count', 'drawing_file', 'item_no']
    readonly_fields = ['item_no']
    inlines = [RouteStepInline]

    @admin.display(description=_('status'))
    def status(self, obj):
        return item_status(obj).label


@admin.register(RouteStep)
class RouteStepAdmin(admin.ModelAdmin):
    list_display = ['item', 'sequence', 'process']


@admin.register(OrderChange)
class OrderChangeAdmin(admin.ModelAdmin):
    list_display = ['changed_at', 'order', 'item_code', 'field', 'old_value', 'new_value', 'changed_by']
    list_filter = ['field']

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
