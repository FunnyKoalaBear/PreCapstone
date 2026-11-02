from django.contrib.auth import views as auth_views
from django.urls import path

from . import feedback, styleguide, user_views, views

urlpatterns = [
    path('', views.home, name='home'),
    path('dashboard/', views.dashboard, name='dashboard'),
    path('users/', user_views.user_list, name='user_list'),
    path('users/add/', user_views.user_add, name='user_add'),
    path('users/<int:pk>/', user_views.user_edit, name='user_edit'),
    path('users/<int:pk>/reset/', user_views.user_reset, name='user_reset'),
    path('account/password/', user_views.PasswordChange.as_view(), name='password_change'),
    path('styleguide/', styleguide.styleguide, name='styleguide'),
    path('undo/<str:token>/', feedback.undo, name='undo'),
    path('login/', auth_views.LoginView.as_view(), name='login'),
    path('logout/', auth_views.LogoutView.as_view(), name='logout'),
]
