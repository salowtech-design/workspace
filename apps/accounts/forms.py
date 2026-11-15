from django import forms
from django.contrib.auth import authenticate
from django.contrib.auth.hashers import check_password, make_password
from django.core.exceptions import ValidationError

# Dummy hash for constant-time comparison when user doesn't exist
DUMMY_PASSWORD_HASH = make_password("SalowWorkspaceDummyPasswordForTimingSafety!")


class LoginForm(forms.Form):
    """
    Employee ID + Password login form with enumeration-safe error handling.
    """

    employee_id = forms.CharField(
        max_length=32,
        label="Employee ID",
        widget=forms.TextInput(
            attrs={
                "class": "form-input",
                "placeholder": "e.g. EMP001",
                "autocomplete": "username",
                "autofocus": "autofocus",
            }
        ),
    )
    password = forms.CharField(
        label="Password",
        widget=forms.PasswordInput(
            attrs={
                "class": "form-input",
                "placeholder": "••••••••••••",
                "autocomplete": "current-password",
            }
        ),
    )

    def clean(self):
        cleaned_data = super().clean()
        raw_emp_id = cleaned_data.get("employee_id")
        password = cleaned_data.get("password")

        if raw_emp_id and password:
            employee_id = raw_emp_id.strip().upper()
            user = authenticate(username=employee_id, password=password)

            if user is None:
                # Perform dummy check to prevent user enumeration timing attacks
                check_password(password, DUMMY_PASSWORD_HASH)
                raise ValidationError("Invalid employee ID or password.")

            if not user.is_active:
                raise ValidationError("Invalid employee ID or password.")

            cleaned_data["user"] = user

        return cleaned_data
