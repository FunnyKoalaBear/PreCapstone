from django.contrib import admin
from django.utils.translation import gettext_lazy as _

from .models import Box, Tag, TagAssignment


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


@admin.register(Box)
class BoxAdmin(admin.ModelAdmin):
    list_display = ['label', 'active', 'created_at']
    list_filter = ['active']
    search_fields = ['label']


@admin.register(Tag)
class TagAdmin(admin.ModelAdmin):
    list_display = ['uid', 'active', 'note', 'created_at']
    list_filter = ['active']
    search_fields = ['uid']


@admin.register(TagAssignment)
class TagAssignmentAdmin(admin.ModelAdmin):
    list_display = ['tag', 'box', 'item', 'start_at', 'end_at']
    list_filter = [OpenFilter]
    actions = ['release_selected']

    @admin.action(description=_('Release selected'))
    def release_selected(self, request, queryset):
        for a in queryset:
            a.release()
