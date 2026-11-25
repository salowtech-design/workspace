from django.urls import path

from apps.finder import views

app_name = "finder"

urlpatterns = [path("", views.search_page, name="search")]
