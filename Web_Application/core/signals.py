from django.contrib.auth import get_user_model
from django.contrib.auth.signals import user_logged_in
from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import Profile

SESSION_KEY = 'must_change_password'


@receiver(post_save, sender=get_user_model())
def create_profile(sender, instance, created, **kwargs):
    if created:
        Profile.objects.get_or_create(user=instance)


@receiver(user_logged_in)
def remember_flag(sender, request, user, **kwargs):
    # Kept in the session so the middleware needs no query per request
    request.session[SESSION_KEY] = Profile.objects.filter(user=user, must_change_password=True).exists()
