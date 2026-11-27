from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('events', '0003_location_description'),
    ]

    operations = [
        migrations.AddField(
            model_name='event',
            name='all_day',
            field=models.BooleanField(default=False),
        ),
    ]
