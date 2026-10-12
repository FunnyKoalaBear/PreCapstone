from django.conf import settings
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
