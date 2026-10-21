from django.db import migrations

ROLES = ['Operator', 'Planner', 'Admin']


def create_groups(apps, schema_editor):
    Group = apps.get_model('auth', 'Group')
    for name in ROLES:
        Group.objects.get_or_create(name=name)


class Migration(migrations.Migration):
    dependencies = [('auth', '0001_initial')]
    operations = [migrations.RunPython(create_groups, migrations.RunPython.noop)]
