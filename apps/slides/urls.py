from django.urls import path

from apps.slides import views

app_name = "slides"
urlpatterns = [path("", views.home, name="home")]
