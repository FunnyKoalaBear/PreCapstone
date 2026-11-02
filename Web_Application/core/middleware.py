from django.conf import settings
from django.shortcuts import redirect
from django.urls import reverse
from django.shortcuts import redirect
from django.urls import reverse


class DefaultJapaneseMiddleware:
    """First-time visitors (no language cookie) get Japanese whatever their browser sends."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if settings.LANGUAGE_COOKIE_NAME not in request.COOKIES:
            request.META['HTTP_ACCEPT_LANGUAGE'] = 'ja'
        return self.get_response(request)


class ForcePasswordChangeMiddleware:
    """While the session carries must_change_password, only login, logout and the password page open."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.user.is_authenticated and request.session.get('must_change_password'):
            allowed = {reverse(n) for n in ('login', 'logout', 'password_change')}
            if request.path not in allowed and not request.path.startswith(('/i18n/', settings.STATIC_URL)):
                return redirect('password_change')
        return self.get_response(request)
