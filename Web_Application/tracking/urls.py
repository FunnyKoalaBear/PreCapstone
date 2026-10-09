from django.urls import path

from . import views

urlpatterns = [
    path('simulate/', views.simulate, name='simulate'),
]
