from django.urls import path

from apps.inbox import views

app_name = "inbox"
urlpatterns = [
    path("", views.home, name="home"),
    path("read/", views.read_all, name="read"),
]
