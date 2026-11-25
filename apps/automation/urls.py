from django.urls import path

from apps.automation import views

app_name = "automation"
urlpatterns = [
    path("", views.home, name="home"),
    path("new/", views.new, name="new"),
    path("<uuid:public_id>/", views.detail, name="detail"),
    path("<uuid:public_id>/edit/", views.edit, name="edit"),
    path("<uuid:public_id>/enable/", views.toggle, name="toggle"),
    path("<uuid:public_id>/run/", views.run_now, name="run"),
]
