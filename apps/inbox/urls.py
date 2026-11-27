from django.urls import path

from apps.inbox import views

app_name = "inbox"
urlpatterns = [
    path("", views.home, name="home"),
    path("open/<int:notice_id>/", views.open_notice, name="open"),
    path("read/", views.read_all, name="read"),
]
