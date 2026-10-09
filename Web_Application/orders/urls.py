from django.urls import path

from . import views

urlpatterns = [
    path('orders/new/', views.order_create, name='order_create'),
]
