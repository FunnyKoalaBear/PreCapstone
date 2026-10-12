from django.conf import settings


class DefaultJapaneseMiddleware:
    """First-time visitors (no language cookie) get Japanese whatever their browser sends."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if settings.LANGUAGE_COOKIE_NAME not in request.COOKIES:
            request.META['HTTP_ACCEPT_LANGUAGE'] = 'ja'
        return self.get_response(request)
