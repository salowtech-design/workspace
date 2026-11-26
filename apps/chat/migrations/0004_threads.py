from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("chat", "0003_reactions"),
    ]

    operations = [
        migrations.AddField(
            model_name="message",
            name="thread_root_seq",
            field=models.BigIntegerField(blank=True, null=True),
        ),
        migrations.AddIndex(
            model_name="message",
            index=models.Index(fields=["space", "thread_root_seq"], name="chat_msg_thread_idx"),
        ),
    ]
