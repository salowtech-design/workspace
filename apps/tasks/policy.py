"""Who may do what. The list owner manages everything; an assignee may only see the task and
mark it done or open again. Deleted lists and tasks are visible to nobody."""

from apps.core.foundation.permissions import register_policy


def _task_policy(user, action, task):
    if task.deleted_at is not None or task.task_list.deleted_at is not None:
        return False
    owner = task.task_list.owner_id == user.pk
    assignee = task.assignee_id == user.pk
    if action == "view":
        return owner or assignee
    if action == "complete":
        return owner or assignee
    if action in ("edit", "assign", "delete"):
        return owner
    return False


def _list_policy(user, action, task_list):
    if task_list.deleted_at is not None:
        return False
    return task_list.owner_id == user.pk and action in ("view", "edit", "delete", "add")


register_policy("task", _task_policy)
register_policy("task_list", _list_policy)
