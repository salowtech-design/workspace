from django.urls import path

from apps.tasks import project_views, views

app_name = "tasks"

urlpatterns = [
    path("", views.home, name="home"),
    path("list/<uuid:list_id>/", views.list_view, name="list"),
    path("assigned/", views.assigned_view, name="assigned"),
    path("due/", views.due_soon_view, name="due_soon"),
    path("new-list/", views.new_list, name="new_list"),
    path("list/<uuid:list_id>/add/", views.add_task, name="add"),
    path("list/<uuid:list_id>/rename/", views.rename_list, name="rename_list"),
    path("list/<uuid:list_id>/delete/", views.delete_list, name="delete_list"),
    path("task/<uuid:task_id>/done/", views.toggle, name="toggle"),
    path("task/<uuid:task_id>/edit/", views.edit, name="edit"),
    path("task/<uuid:task_id>/assign/", views.assign_task, name="assign"),
    path("task/<uuid:task_id>/move/", views.move_task, name="move"),
    path("task/<uuid:task_id>/delete/", views.delete_task, name="delete"),
    path("trash/", views.trash_view, name="trash"),
    path("trash/task/<uuid:task_id>/restore/", views.restore_task, name="restore_task"),
    path("trash/list/<uuid:list_id>/restore/", views.restore_list, name="restore_list"),
    path("projects/", project_views.projects_home, name="projects"),
    path("projects/new/", project_views.new_project, name="new_project"),
    path("projects/<uuid:project_id>/", project_views.project_view, name="project"),
    path("projects/<uuid:project_id>/add/", project_views.add_project_task, name="project_add"),
    path(
        "projects/task/<uuid:task_id>/move/", project_views.move_project_task, name="project_move"
    ),
    path(
        "projects/task/<uuid:task_id>/edit/", project_views.edit_project_task, name="project_edit"
    ),
    path(
        "projects/task/<uuid:task_id>/delete/",
        project_views.delete_project_task,
        name="project_delete",
    ),
    path("projects/<uuid:project_id>/trash/", project_views.project_trash, name="project_trash"),
    path("projects/task/<uuid:task_id>/restore/", project_views.restore_project_task, name="project_restore"),
    path(
        "projects/<uuid:project_id>/milestones/new/",
        project_views.add_milestone,
        name="milestone_add",
    ),
    path(
        "projects/task/<uuid:task_id>/milestone/",
        project_views.task_milestone,
        name="project_milestone",
    ),
    path(
        "projects/task/<uuid:task_id>/comments/",
        project_views.task_comments,
        name="project_comments",
    ),
    path(
        "projects/task/<uuid:task_id>/assign/",
        project_views.assign_project_task,
        name="project_assign",
    ),
]
