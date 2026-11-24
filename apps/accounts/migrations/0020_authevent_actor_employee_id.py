from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("accounts", "0019_iamsession_pending_summary")]

    operations = [
        migrations.AddField(
            model_name="authevent",
            name="actor_employee_id",
            field=models.CharField(blank=True, db_index=True, default="", max_length=32),
        ),
    ]
