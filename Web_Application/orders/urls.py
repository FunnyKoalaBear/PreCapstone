from django.urls import path

from . import views

urlpatterns = [
    path('customers/', views.customer_list, name='customer_list'),
    path('orders/', views.order_list, name='order_list'),
    path('orders/new/', views.order_create, name='order_create'),
    path('orders/<str:job_number>/edit/', views.order_edit, name='order_edit'),
    path('orders/<str:job_number>/', views.order_detail, name='order_detail'),
]
