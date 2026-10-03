from django import forms
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator

from learning_apps.persistence.models import UserProfile


class LoginForm(forms.Form):
    user_type = forms.ChoiceField(
        choices=UserProfile.ROLE_CHOICES,
        required=False,
        widget=forms.HiddenInput,
    )
    username = forms.CharField(label="Username", required=True)
    password = forms.CharField(label="Password", required=True, widget=forms.PasswordInput)


class RegistrationForm(forms.Form):
    user_type = forms.ChoiceField(
        choices=UserProfile.ROLE_CHOICES,
        required=False,
        widget=forms.HiddenInput,
    )
    username = forms.CharField(
        label="Username",
        min_length=4,
        max_length=25,
        required=True,
        validators=[RegexValidator(r"^\w+$", message="Username may only contain letters, numbers, and underscores.")],
    )
    email = forms.EmailField(label="Email", required=True)
    password = forms.CharField(
        label="Password",
        min_length=10,
        required=True,
        widget=forms.PasswordInput,
    )
    confirm_password = forms.CharField(
        label="Confirm Password",
        required=True,
        widget=forms.PasswordInput,
    )

    def __init__(self, *args, **kwargs):
        """Initialize the RegistrationForm instance."""
        super().__init__(*args, **kwargs)
        self._apply_auth_styles()

    def _apply_auth_styles(self) -> None:
        """Internal helper to handle apply auth styles."""
        base_attrs = {"class": "form-control auth-control"}
        self.fields["user_type"].widget.attrs.update({"class": "auth-role-input"})
        self.fields["username"].widget.attrs.update(base_attrs)
        self.fields["email"].widget.attrs.update(base_attrs)
        self.fields["password"].widget.attrs.update(base_attrs)
        self.fields["confirm_password"].widget.attrs.update(base_attrs)

    def clean_password(self):
        """Validate password using Django's configured password validators."""
        password = self.cleaned_data.get("password")
        if password:
            try:
                validate_password(password)
            except ValidationError as exc:
                raise forms.ValidationError(exc.messages) from exc
        return password

    def clean(self):
        """Handle clean."""
        cleaned_data = super().clean()
        password = cleaned_data.get("password")
        confirm = cleaned_data.get("confirm_password")
        if password and confirm and password != confirm:
            raise forms.ValidationError("Passwords must match.")
        return cleaned_data


class PreferencesSetupForm(forms.Form):
    age = forms.IntegerField(label="Age", min_value=1, max_value=120, required=True)
    academic_level = forms.CharField(label="Academic Level", max_length=50, required=True)

    def __init__(self, *args, **kwargs):
        """Initialize the PreferencesSetupForm instance."""
        super().__init__(*args, **kwargs)
        self._apply_style_attrs()

    def _apply_style_attrs(self) -> None:
        """Internal helper to handle apply style attrs."""
        text_attrs = {"class": "form-control prefs-control"}

        self.fields["age"].widget.attrs.update(text_attrs)
        self.fields["academic_level"].widget.attrs.update(text_attrs)
