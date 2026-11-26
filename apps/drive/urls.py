from django.urls import path

from apps.drive import views

app_name = "drive"

urlpatterns = [
    path("", views.drive_home, name="home"),
    path("folder/<uuid:folder_id>/", views.folder_view, name="folder"),
    path("shared/", views.shared_view, name="shared"),
    path("trash/", views.trash_view, name="trash"),
    path("new-folder/", views.new_folder, name="new_folder"),
    path("<str:kind>/<uuid:item_id>/rename/", views.rename_item, name="rename"),
    path("<str:kind>/<uuid:item_id>/move/", views.move_item, name="move"),
    path("<str:kind>/<uuid:item_id>/move/find/", views.move_find, name="move_find"),
    path("<str:kind>/<uuid:item_id>/trash/", views.trash_item, name="trash_item"),
    path("<str:kind>/<uuid:item_id>/restore/", views.restore_item, name="restore_item"),
    path("<str:kind>/<uuid:item_id>/share/", views.share_item, name="share"),
    path("<str:kind>/<uuid:item_id>/unshare/", views.unshare_item, name="unshare"),
]
