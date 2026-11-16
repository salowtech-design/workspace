from django import forms
from django.db.models import Q

from apps.directory.models import Team
from apps.posts.models import Post, PostComment


class PostForm(forms.ModelForm):
    class Meta:
        model = Post
        fields = ["title", "category", "team", "work_date", "content"]
        widgets = {
            "title": forms.TextInput(
                attrs={
                    "class": "form-control",
                    "placeholder": "What did you accomplish today?",
                    "required": True,
                }
            ),
            "category": forms.Select(attrs={"class": "form-control"}),
            "team": forms.Select(attrs={"class": "form-control"}),
            "work_date": forms.DateInput(attrs={"class": "form-control", "type": "date"}),
            "content": forms.Textarea(
                attrs={
                    "class": "form-control",
                    "rows": 6,
                    "placeholder": "Detail your key tasks, blockers, and next steps...",
                    "required": True,
                }
            ),
        }

    def __init__(self, *args, author=None, **kwargs):
        super().__init__(*args, **kwargs)
        if author:
            # Filter teams to user's teams unless admin (include led teams)
            if author.role not in ("super_admin", "admin"):
                self.fields["team"].queryset = Team.objects.filter(
                    Q(memberships__user=author) | Q(lead=author)
                ).distinct()
            else:
                self.fields["team"].queryset = Team.objects.all()


class PostReviewForm(forms.Form):
    status = forms.ChoiceField(
        choices=[
            (Post.Status.APPROVED, "Approve & Sign Off"),
            (Post.Status.FLAGGED, "Flag for Revision"),
        ],
        widget=forms.Select(attrs={"class": "form-control"}),
    )
    review_notes = forms.CharField(
        required=False,
        widget=forms.Textarea(
            attrs={
                "class": "form-control",
                "rows": 3,
                "placeholder": "Optional feedback or sign-off notes...",
            }
        ),
    )


class PostCommentForm(forms.ModelForm):
    class Meta:
        model = PostComment
        fields = ["content"]
        widgets = {
            "content": forms.Textarea(
                attrs={
                    "class": "form-control",
                    "rows": 2,
                    "placeholder": "Write a reply or constructive note...",
                    "required": True,
                }
            )
        }
