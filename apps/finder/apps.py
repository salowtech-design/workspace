from django.apps import AppConfig


class FinderConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.finder"
    verbose_name = "Salow Search"

    def ready(self):
        from apps.finder.searchers import register_all

        register_all()
