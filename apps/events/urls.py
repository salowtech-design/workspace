from django.urls import path

from apps.events import views

app_name = "events"

urlpatterns = [
    path("", views.home, name="home"),
    path("month/", views.month_view, name="month"),
    path("week/", views.week_view, name="week"),
    path("day/", views.day_view, name="day"),
    path("new/", views.new_event, name="new"),
    path("busy/", views.busy_view, name="busy"),
    path("event/<uuid:event_id>/", views.detail, name="detail"),
    path("event/<uuid:event_id>/edit/", views.edit, name="edit"),
    path("event/<uuid:event_id>/respond/", views.respond_view, name="respond"),
    path("event/<uuid:event_id>/skip/", views.skip_view, name="skip"),
    path("event/<uuid:event_id>/cancel/", views.cancel_view, name="cancel"),
]
