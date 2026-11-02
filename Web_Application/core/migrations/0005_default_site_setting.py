from django.db import migrations


def create_row(apps, schema_editor):
    apps.get_model('core', 'SiteSetting').objects.get_or_create(pk=1)


class Migration(migrations.Migration):
    dependencies = [('core', '0004_sitesetting')]
    operations = [migrations.RunPython(create_row, migrations.RunPython.noop)]
