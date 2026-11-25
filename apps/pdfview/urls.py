from django.urls import path

from apps.pdfview import views

app_name = "pdfview"
urlpatterns = [path("", views.home, name="home")]
