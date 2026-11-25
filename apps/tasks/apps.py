from django.apps import AppConfig


class TasksConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.tasks"
    verbose_name = "Salow Tasks"

    def ready(self):
        from apps.tasks import policy  # noqa: F401  registers the object policies
