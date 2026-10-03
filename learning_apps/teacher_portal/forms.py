from __future__ import annotations

from django import forms

from .models import TeacherClassroom


class TeacherClassroomForm(forms.ModelForm):
    class Meta:
        model = TeacherClassroom
        fields = ["name", "subject", "term", "description"]
        widgets = {
            "name": forms.TextInput(attrs={"class": "form-control", "placeholder": "e.g. Algebra Studio A"}),
            "subject": forms.TextInput(attrs={"class": "form-control", "placeholder": "e.g. Mathematics"}),
            "term": forms.TextInput(attrs={"class": "form-control", "placeholder": "e.g. Fall 2026"}),
            "description": forms.Textarea(
                attrs={
                    "class": "form-control",
                    "rows": 4,
                    "placeholder": "Describe the class focus and what students should expect.",
                }
            ),
        }

    def clean_name(self):
        return str(self.cleaned_data["name"]).strip()

    def clean_subject(self):
        return str(self.cleaned_data.get("subject") or "").strip()

    def clean_term(self):
        return str(self.cleaned_data.get("term") or "").strip()

    def clean_description(self):
        return str(self.cleaned_data.get("description") or "").strip()


class ClassroomInvitationForm(forms.Form):
    username = forms.CharField(
        max_length=50,
        widget=forms.TextInput(
            attrs={"class": "form-control", "placeholder": "Existing student username", "autocomplete": "off"}
        ),
    )
    message = forms.CharField(
        required=False,
        widget=forms.Textarea(
            attrs={"class": "form-control", "rows": 3, "placeholder": "Add context for the student."}
        ),
    )

    def clean_username(self):
        return str(self.cleaned_data["username"]).strip()

    def clean_message(self):
        return str(self.cleaned_data.get("message") or "").strip()
