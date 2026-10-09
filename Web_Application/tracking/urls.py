from django.urls import path

from . import views

urlpatterns = [
    path('simulate/', views.simulate, name='simulate'),
    path('tags/', views.tag_manage, name='tag_manage'),
]
