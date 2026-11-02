from django.contrib import admin
from django.utils.translation import gettext_lazy as _

from .models import DefectCause, Event, Machine, ScannerDevice, Tag, TagAssignment, Worker


class OpenFilter(admin.SimpleListFilter):
    title = _('status')
    parameter_name = 'open'

    def lookups(self, request, model_admin):
        return [('open', _('Open')), ('closed', _('Closed'))]

    def queryset(self, request, queryset):
        if self.value() == 'open':
            return queryset.filter(end_at__isnull=True)
        if self.value() == 'closed':
            return queryset.filter(end_at__isnull=False)


@admin.register(Tag)
class TagAdmin(admin.ModelAdmin):
    list_display = ['uid', 'active', 'note', 'created_at']
    list_filter = ['active']
    search_fields = ['uid']


@admin.register(TagAssignment)
class TagAssignmentAdmin(admin.ModelAdmin):
    list_display = ['tag', 'item', 'start_at', 'end_at']
    list_filter = [OpenFilter]
    actions = ['release_selected']

    @admin.action(description=_('Release selected'))
    def release_selected(self, request, queryset):
        for a in queryset:
            a.release()


@admin.register(ScannerDevice)
class ScannerDeviceAdmin(admin.ModelAdmin):
    list_display = ['name', 'location', 'active', 'last_seen']
    list_filter = ['active']
    readonly_fields = ['api_key']


@admin.register(Worker, Machine, DefectCause)
class NamedResourceAdmin(admin.ModelAdmin):
    list_display = ['name', 'name_ja', 'active']
    list_filter = ['active']
    search_fields = ['name', 'name_ja']


@admin.register(Event)
class EventAdmin(admin.ModelAdmin):
    list_display = ['occurred_at', 'event_type', 'tag_uid', 'device', 'process', 'worker', 'machine', 'defect_cause', 'quantity', 'defects', 'note', 'photo']
    list_filter = ['event_type', 'device', 'process']

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
