from django.urls import path

from . import api, views

urlpatterns = [
    path('api/scan/', api.scan, name='api_scan'),
    path('events/<int:pk>/correct/', views.correct_event, name='correct_event'),
    path('simulate/', views.simulate, name='simulate'),
    path('priorities/', views.priorities, name='priorities'),
    path('boxes/', views.boxes, name='boxes'),
    path('matrix/', views.progress_matrix, name='matrix'),
    path('results/', views.results_summary, name='results'),
    path('items/<int:pk>/result/', views.record_result_view, name='record_result'),
    path('tags/', views.tag_manage, name='tag_manage'),
    path('tags/history/', views.tag_history, name='tag_history'),
]
