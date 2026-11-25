from django.db import migrations


def seed_lock(apps, schema_editor):
    apps.get_model("directory", "ReportingLock").objects.get_or_create(name="reporting")


class Migration(migrations.Migration):
    dependencies = [("directory", "0004_onboarding")]
    operations = [migrations.RunPython(seed_lock, migrations.RunPython.noop)]
