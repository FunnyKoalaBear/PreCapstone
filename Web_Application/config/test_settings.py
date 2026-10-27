from .settings import *  # noqa: F403

# Fast password hashing: the suite creates many users
PASSWORD_HASHERS = ['django.contrib.auth.hashers.MD5PasswordHasher']
