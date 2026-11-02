from django.db import migrations, models
import django.db.models.deletion


def link_replacements(apps, schema_editor):
    """Chain the assignments of each (item, box) in time order, so a box keeps its tag history without Box."""
    TagAssignment = apps.get_model('tracking', 'TagAssignment')
    last = {}
    for a in TagAssignment.objects.order_by('start_at', 'pk').iterator():
        key = (a.item_id, a.box_id)
        if key in last:
            TagAssignment.objects.filter(pk=a.pk).update(replaces_id=last[key])
        last[key] = a.pk


class Migration(migrations.Migration):

    dependencies = [
        ('orders', '0014_vendor_notes_active'),
        ('tracking', '0007_event_tag_time_index'),
    ]

    operations = [
        migrations.AddField(
            model_name='tagassignment',
            name='replaces',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='replaced_by', to='tracking.tagassignment', verbose_name='replaces'),
        ),
        migrations.RunPython(link_replacements, migrations.RunPython.noop),
        migrations.RemoveConstraint(model_name='tagassignment', name='one_open_assignment_per_box'),
        migrations.RemoveField(model_name='tagassignment', name='box'),
        migrations.DeleteModel(name='Box'),
    ]
