from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('tasks', '0005_task_comments'),
    ]

    operations = [
        migrations.AddField(model_name='tasklist', name='delete_batch', field=models.UUIDField(blank=True, null=True)),
        migrations.AddField(model_name='task', name='delete_batch', field=models.UUIDField(blank=True, null=True)),
    ]
