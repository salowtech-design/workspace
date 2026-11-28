from django.urls import path

from apps.forms import views

app_name = "forms"
urlpatterns = [
    path("", views.home, name="home"),
    path("<uuid:pid>/", views.editor, name="editor"),
    path("<uuid:pid>/preview/", views.preview, name="preview"),
    path("<uuid:pid>/results/", views.results_view, name="results"),
    path("<uuid:pid>/results.csv", views.results_csv, name="csv"),
    path("<uuid:pid>/fill/", views.fill, name="fill"),
]
