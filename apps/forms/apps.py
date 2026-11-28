from django.apps import AppConfig


class FormsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.forms"
    verbose_name = "Salow Forms"

    def ready(self):
        from apps.forms import policy  # noqa: F401  registers the object policy
