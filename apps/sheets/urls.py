from django.urls import path

from apps.sheets import views

app_name = "sheets"
urlpatterns = [path("", views.home, name="home")]
