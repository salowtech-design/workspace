from django.urls import path

from apps.tasks import project_views, views

app_name = "tasks"

urlpatterns = [
    path("", views.home, name="home"),
    path("list/<uuid:list_id>/", views.list_view, name="list"),
    path("assigned/", views.assigned_view, name="assigned"),
    path("new-list/", views.new_list, name="new_list"),
    path("list/<uuid:list_id>/add/", views.add_task, name="add"),
    path("list/<uuid:list_id>/delete/", views.delete_list, name="delete_list"),
    path("task/<uuid:task_id>/done/", views.toggle, name="toggle"),
    path("task/<uuid:task_id>/edit/", views.edit, name="edit"),
    path("task/<uuid:task_id>/assign/", views.assign_task, name="assign"),
    path("task/<uuid:task_id>/delete/", views.delete_task, name="delete"),
    path("projects/", project_views.projects_home, name="projects"),
    path("projects/new/", project_views.new_project, name="new_project"),
    path("projects/<uuid:project_id>/", project_views.project_view, name="project"),
]
