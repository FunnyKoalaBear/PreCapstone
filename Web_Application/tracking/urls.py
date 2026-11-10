from django.urls import path
from django.views.generic import RedirectView

from . import api, devices, settings_page, views

urlpatterns = [
    path('api/scan/', api.scan, name='api_scan'),
    path('api/scan-preview/', api.scan_preview, name='api_scan_preview'),
    path('api/assignable-items/', api.assignable_items_list, name='api_assignable_items'),
    path('api/assign/', api.assign, name='api_assign'),
    path('api/items/<int:pk>/drawing-pixels/', api.item_drawing_pixels, name='api_item_drawing_pixels'),
    path('events/<int:pk>/correct/', views.correct_event, name='correct_event'),
    path('simulate/', views.simulate, name='simulate'),
    path('priorities/', views.priorities, name='priorities'),
    path('boxes/', views.boxes, name='boxes'),
    path('matrix/', RedirectView.as_view(pattern_name='home', query_string=True), name='matrix'),
    path('analysis/', views.analysis, name='analysis'),
    path('results/', views.results_summary, name='results'),
    path('completed/', views.completed, name='completed'),
    path('shipping/', views.shipping, name='shipping'),
    path('plan-vs-actual/', views.plan_summary, name='plan_summary'),
    path('results/<int:pk>/', views.result_detail, name='result_detail'),
    path('results/<int:pk>/photo/', views.result_photo, name='result_photo'),
    path('items/<int:pk>/result/', views.record_result_view, name='record_result'),
    path('items/<int:pk>/action/', views.item_action, name='item_action'),
    path('settings/', settings_page.settings_page, name='settings'),
    path('tags/', views.tag_manage, name='tag_manage'),
    path('tags/devices/', devices.device_list, name='device_list'),
    path('tags/devices/add/', devices.device_add, name='device_add'),
    path('tags/devices/<int:pk>/', devices.device_edit, name='device_edit'),
    path('tags/devices/<int:pk>/key/', devices.device_regenerate, name='device_regenerate'),
    path('tags/history/', views.tag_history, name='tag_history'),
]
