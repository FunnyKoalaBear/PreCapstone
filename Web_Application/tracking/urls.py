from django.urls import path
from django.views.generic import RedirectView

from . import api, settings_page, views

urlpatterns = [
    path('api/scan/', api.scan, name='api_scan'),
    path('events/<int:pk>/correct/', views.correct_event, name='correct_event'),
    path('simulate/', views.simulate, name='simulate'),
    path('priorities/', views.priorities, name='priorities'),
    path('boxes/', views.boxes, name='boxes'),
    path('matrix/', RedirectView.as_view(pattern_name='home', query_string=True), name='matrix'),
    path('analysis/', views.analysis, name='analysis'),
    path('results/', views.results_summary, name='results'),
    path('shipping/', views.shipping, name='shipping'),
    path('plan-vs-actual/', views.plan_summary, name='plan_summary'),
    path('results/<int:pk>/', views.result_detail, name='result_detail'),
    path('results/<int:pk>/photo/', views.result_photo, name='result_photo'),
    path('items/<int:pk>/result/', views.record_result_view, name='record_result'),
    path('items/<int:pk>/action/', views.item_action, name='item_action'),
    path('settings/', settings_page.settings_page, name='settings'),
    path('tags/', views.tag_manage, name='tag_manage'),
    path('tags/history/', views.tag_history, name='tag_history'),
]
