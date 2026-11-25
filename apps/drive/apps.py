from django.apps import AppConfig


class DriveConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.drive"
    verbose_name = "Salow Drive"

    def ready(self):
        from apps.core.foundation.search import register_searcher
        from apps.drive import policy  # noqa: F401  registers the object policies
        from apps.drive.services import search_items

        register_searcher("drive", search_items)
