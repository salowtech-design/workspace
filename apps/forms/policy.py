"""Object policy for a form. Default deny. Working defaults awaiting the owner's confirmation (load-bearing, not settled):
the owner edits, previews, publishes, closes and sees every response; a person on the audience of an OPEN form may fill it once
and sees only their own answers; everyone else is refused."""

from apps.core.foundation.permissions import register_policy


def _form_policy(user, action, form):
    if form.owner_id == user.pk:
        return action in ("view", "edit", "preview", "publish", "close", "results")
    if action in ("view", "fill", "view_own"):
        return form.status == "open" and form.audience.filter(person_id=user.pk).exists() or (
            action in ("view", "view_own") and form.responses.filter(person_id=user.pk).exists()
        )
    return False


register_policy("forms.form", _form_policy)
