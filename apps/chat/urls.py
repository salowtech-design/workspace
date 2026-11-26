from django.urls import path

from apps.chat import views

app_name = "chat"

urlpatterns = [
    path("", views.home, name="home"),
    path("dm/", views.start_dm, name="dm"),
    path("group/", views.new_group, name="group"),
    path("space/<uuid:space_id>/", views.space_view, name="space"),
    path("space/<uuid:space_id>/send/", views.send, name="send"),
    path("space/<uuid:space_id>/poll/", views.poll, name="poll"),
    path("space/<uuid:space_id>/react/", views.react, name="react"),
    path("space/<uuid:space_id>/reactions/", views.reactions, name="reactions"),
]
