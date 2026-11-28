from django.urls import path

from apps.addons import views

app_name = "addons"
urlpatterns = [
    path("", views.home, name="home"),
    path("<uuid:pid>/", views.draft, name="draft"),
    path("<uuid:pid>/v/<str:version>/", views.review, name="review"),
]
