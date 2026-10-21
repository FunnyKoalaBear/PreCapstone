from django.conf import settings
from django.contrib.auth.models import Group, User
from django.test import TestCase as DjangoTestCase
from django.utils import translation


class TestCase(DjangoTestCase):
    """Django TestCase that runs in English (cookie for the client, active language for plain calls)."""

    def _pre_setup(self):
        super()._pre_setup()
        self.client.cookies[settings.LANGUAGE_COOKIE_NAME] = 'en'
        translation.activate('en')

    def _post_teardown(self):
        translation.deactivate()
        super()._post_teardown()


def make_user(name='u', role=None):
    """Get or create a user in the given role group (None = no role)."""
    user = User.objects.filter(username=name).first() or User.objects.create_user(name, password='x')
    if role:
        user.groups.add(Group.objects.get(name=role))
    return user
