from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('orders', '0007_orderchange'),
    ]

    operations = [
        migrations.AddField(
            model_name='process',
            name='active',
            field=models.BooleanField(default=True, verbose_name='active'),
        ),
    ]
