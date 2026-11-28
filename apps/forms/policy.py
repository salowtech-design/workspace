"""Object policy for a form. Default deny, and no administrator or superuser bypass.
PROPOSED defaults awaiting the owner's approval (load-bearing, not settled):
- the owner edits, previews, publishes, closes and sees every response with the respondent's name;
- a person on the audience of an OPEN form may see and fill it once, and sees only their own answers;
- the owner does not answer their own form (a self-answer is a future choice, not enabled).
While FORMS_COLLECTION_APPROVED is False nobody but the owner can see a form at all, whatever the audience list says, and a
person who has no response of their own gets no access."""

from django.conf import settings

from apps.core.foundation.permissions import register_policy


def collection_on():
    return bool(getattr(settings, "FORMS_COLLECTION_APPROVED", False))


def _form_policy(user, action, form):
    if form.owner_id == user.pk:
        return action in ("view", "edit", "preview", "publish", "close", "results")
    has_response = form.responses.filter(person_id=user.pk).exists()
    if action == "view_own":
        return has_response
    live = collection_on() and form.status == "open" and form.audience.filter(person_id=user.pk).exists()
    if action == "fill":
        return live and not has_response
    if action == "view":
        return live or has_response
    return False


register_policy("forms.form", _form_policy)
