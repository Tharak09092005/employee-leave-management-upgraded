import re

from django import forms
from django.contrib.auth.forms import PasswordChangeForm

from .models import LEAVE_TYPES, Employee
from .services import get_balance_shortfall
from .validators import validate_leave_document, validate_profile_photo

PHONE_RE = re.compile(r"^\+?[0-9]{7,15}$")


class EditProfileForm(forms.ModelForm):
    """
    Lets an employee edit their own contact details.
    Employee ID, department and designation are deliberately NOT fields here,
    so they cannot be changed even if someone tampers with the POST data.
    """

    class Meta:
        model = Employee
        fields = ["first_name", "last_name", "email", "phone", "address"]
        widgets = {
            "address": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs["class"] = "form-control"
        self.fields["address"].required = False

    def clean_email(self):
        email = self.cleaned_data["email"].strip()
        duplicate = Employee.objects.filter(email__iexact=email).exclude(pk=self.instance.pk)
        if duplicate.exists():
            raise forms.ValidationError("This email is already used by another employee.")
        return email

    def clean_phone(self):
        phone = self.cleaned_data["phone"].strip()
        if not PHONE_RE.match(phone):
            raise forms.ValidationError("Enter a valid phone number (7-15 digits).")
        return phone

    def save_with_user(self):
        """Save the Employee and keep the linked Django User in sync."""
        employee = self.save()
        user = employee.user
        if user is not None:
            user.first_name = employee.first_name
            user.last_name = employee.last_name
            user.email = employee.email
            user.save(update_fields=["first_name", "last_name", "email"])
        return employee


class ProfilePhotoForm(forms.Form):
    photo = forms.FileField(
        validators=[validate_profile_photo],
        error_messages={"required": "Please choose a photo to upload."},
    )


class StyledPasswordChangeForm(PasswordChangeForm):
    """Django's PasswordChangeForm (secure hashing + validators) with Bootstrap styling."""

    error_messages = {
        **PasswordChangeForm.error_messages,
        "password_incorrect": "Invalid current password.",
    }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs["class"] = "form-control"
        self.fields["old_password"].widget.attrs["autofocus"] = True

    def clean_new_password1(self):
        new_password = self.cleaned_data.get("new_password1")
        old_password = self.cleaned_data.get("old_password")
        if new_password and old_password and new_password == old_password:
            raise forms.ValidationError("The new password must be different from the current one.")
        return new_password


class ApplyLeaveForm(forms.Form):
    leave_type = forms.ChoiceField(
        choices=[(t, t) for t in LEAVE_TYPES],
        error_messages={
            "required": "Please select a valid leave type.",
            "invalid_choice": "Please select a valid leave type.",
        },
    )
    start_date = forms.DateField(
        error_messages={"required": "Start date and end date are required.", "invalid": "Enter valid dates."}
    )
    end_date = forms.DateField(
        error_messages={"required": "Start date and end date are required.", "invalid": "Enter valid dates."}
    )
    reason = forms.CharField(error_messages={"required": "Reason cannot be empty."})
    document = forms.FileField(required=False, validators=[validate_leave_document])

    def __init__(self, *args, employee, **kwargs):
        super().__init__(*args, **kwargs)
        self.employee = employee

    def clean(self):
        cleaned = super().clean()
        start, end = cleaned.get("start_date"), cleaned.get("end_date")
        leave_type = cleaned.get("leave_type")
        if not (start and end and leave_type):
            return cleaned

        if start > end:
            raise forms.ValidationError("Start date cannot be after end date.")

        if start.year != end.year:
            raise forms.ValidationError(
                "A leave request cannot span two calendar years. "
                "Please submit a separate request for each year."
            )

        shortfall = get_balance_shortfall(self.employee, leave_type, start, end)
        if shortfall:
            days, remaining, year = shortfall
            raise forms.ValidationError(
                f"Insufficient leave balance. You asked for {days} day(s) of {leave_type}, "
                f"but only {remaining} day(s) are available for {year}."
            )
        return cleaned
