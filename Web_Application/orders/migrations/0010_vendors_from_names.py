from django.db import migrations


def names_to_vendors(apps, schema_editor):
    Process, Vendor = apps.get_model('orders', 'Process'), apps.get_model('orders', 'Vendor')
    by_name = {}
    for p in Process.objects.exclude(vendor_name='').order_by('pk'):
        by_name.setdefault(p.vendor_name, []).append(p)
    for i, (name, procs) in enumerate(by_name.items()):
        leads = {p.vendor_lead_days for p in procs}
        # Vendor lead days only when every process of this name agrees; otherwise each keeps its own
        vendor = Vendor.objects.create(
            code=f'{100 + i:05d}', name=name, kind='VENDOR', lead_days=leads.pop() if len(leads) == 1 else None)
        Process.objects.filter(pk__in=[p.pk for p in procs]).update(default_vendor=vendor)


class Migration(migrations.Migration):
    dependencies = [('orders', '0009_vendor')]
    operations = [migrations.RunPython(names_to_vendors, migrations.RunPython.noop)]
