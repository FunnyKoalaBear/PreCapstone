from django.conf import settings
from django.db import migrations


def create_profiles(apps, schema_editor):
    User, Profile = apps.get_model(*settings.AUTH_USER_MODEL.split('.')), apps.get_model('core', 'Profile')
    Profile.objects.bulk_create([Profile(user=u) for u in User.objects.filter(profile__isnull=True)])


class Migration(migrations.Migration):
    dependencies = [('core', '0002_profile')]
    operations = [migrations.RunPython(create_profiles, migrations.RunPython.noop)]
