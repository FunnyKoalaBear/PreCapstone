from django.urls import path

from . import views

urlpatterns = [
    path('codes/', views.code_list, name='code_list'),
    path('customers/', views.customer_list, name='customer_list'),
    path('customers/<int:pk>/', views.customer_detail, name='customer_detail'),
    path('customers/<int:pk>/edit/', views.customer_edit, name='customer_edit'),
    path('orders/', views.order_list, name='order_list'),
    path('orders/new/', views.order_create, name='order_create'),
    path('orders/import/', views.order_import, name='order_import'),
    path('orders/route-copy/', views.route_copy, name='route_copy'),
    path('orders/<str:job_number>/edit/', views.order_edit, name='order_edit'),
    path('items/<int:pk>/drawing/', views.item_drawing, name='item_drawing'),
    path('orders/<str:job_number>/sheet/', views.order_sheet, name='order_sheet'),
    path('orders/<str:job_number>/', views.order_detail, name='order_detail'),
]
