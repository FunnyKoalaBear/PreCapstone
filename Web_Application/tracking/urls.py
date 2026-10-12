from django.urls import path

from . import views

urlpatterns = [
    path('simulate/', views.simulate, name='simulate'),
    path('priorities/', views.priorities, name='priorities'),
    path('boxes/', views.boxes, name='boxes'),
    path('tags/', views.tag_manage, name='tag_manage'),
    path('tags/history/', views.tag_history, name='tag_history'),
]
