from django import forms
from django.utils import timezone

from apps.accounts.models import CustomUser
from apps.certificates.models import Certificate, CertificateTemplate


class CertificateIssueForm(forms.ModelForm):
    class Meta:
        model = Certificate
        fields = [
            "recipient",
            "template",
            "title",
            "recipient_role",
            "description",
            "issue_date",
            "expiry_date",
        ]
        widgets = {
            "recipient": forms.Select(attrs={"class": "form-control"}),
            "template": forms.Select(attrs={"class": "form-control"}),
            "title": forms.TextInput(
                attrs={
                    "class": "form-control",
                    "placeholder": "e.g. Certificate of Internship Excellence",
                    "required": True,
                }
            ),
            "recipient_role": forms.TextInput(
                attrs={
                    "class": "form-control",
                    "placeholder": "e.g. Software Engineering Intern",
                }
            ),
            "description": forms.Textarea(
                attrs={
                    "class": "form-control",
                    "rows": 4,
                    "placeholder": "Enter official recognition citation...",
                    "required": True,
                }
            ),
            "issue_date": forms.DateInput(attrs={"class": "form-control", "type": "date"}),
            "expiry_date": forms.DateInput(attrs={"class": "form-control", "type": "date"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["recipient"].queryset = CustomUser.objects.filter(is_active=True).order_by(
            "employee_id"
        )
        self.fields["template"].queryset = CertificateTemplate.objects.filter(is_active=True)
        # The model default is timezone.now (a UTC datetime), which this date box would print as the UTC
        # date: a day behind India after 00:00 until 05:30. Use the local calendar date, as the service does.
        self.fields["issue_date"].initial = timezone.localdate


class CertificateRevokeForm(forms.Form):
    reason = forms.CharField(
        widget=forms.Textarea(
            attrs={
                "class": "form-control",
                "rows": 3,
                "placeholder": "Provide the reason for revoking this certificate (e.g. administrative error, credential invalidated)...",
                "required": True,
            }
        )
    )
