from django.contrib import admin

from .models import Customer, Order, OrderItem, Process, RouteStep


class OrderItemInline(admin.TabularInline):
    model = OrderItem
    extra = 1
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
    list_display = ['name', 'name_ja', 'kind', 'vendor_name']
    list_filter = ['kind']


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = ['job_number', 'customer', 'customer_order_number', 'due_date']
    readonly_fields = ['job_number']
    inlines = [OrderItemInline]


@admin.register(OrderItem)
class OrderItemAdmin(admin.ModelAdmin):
    list_display = ['item_code', 'part_name', 'quantity']
    readonly_fields = ['item_no']
    inlines = [RouteStepInline]


@admin.register(RouteStep)
class RouteStepAdmin(admin.ModelAdmin):
    list_display = ['item', 'sequence', 'process']
