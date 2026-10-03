import os
import uuid

from django.contrib.auth.models import User
from django.db import models

# ---------------------------------------------------------------------------
# Constants shared by models, forms, views and services
# ---------------------------------------------------------------------------

LEAVE_TYPES = ["Casual Leave", "Sick Leave", "Earned Leave"]

# Days per calendar year given to every employee for each leave type.
# An individual employee's allowance can be changed from the Django admin
# (Leave balances) - see the LeaveBalance model below.
DEFAULT_ANNUAL_ALLOCATION = {
    "Casual Leave": 12,
    "Sick Leave": 10,
    "Earned Leave": 15,
}

STATUS_PENDING = "Pending"
STATUS_APPROVED = "Approved"
STATUS_REJECTED = "Rejected"
LEAVE_STATUSES = [STATUS_PENDING, STATUS_APPROVED, STATUS_REJECTED]


def _random_upload_name(folder, filename):
    """Store uploads under a random name so users can't guess or overwrite files."""
    extension = os.path.splitext(filename)[1].lower()
    return f"{folder}/{uuid.uuid4().hex}{extension}"


def profile_photo_path(instance, filename):
    return _random_upload_name("profile_photos", filename)


def leave_document_path(instance, filename):
    return _random_upload_name("leave_documents", filename)


# ---------------------------------------------------------------------------
# Employee / Leave (original models - existing fields are unchanged)
# ---------------------------------------------------------------------------

class Employee(models.Model):

    user = models.OneToOneField(User, on_delete=models.CASCADE, null=True, blank=True)

    employee_id = models.CharField(max_length=20, unique=True)
    first_name = models.CharField(max_length=50)
    last_name = models.CharField(max_length=50)
    email = models.EmailField(unique=True)
    phone = models.CharField(max_length=15)
    department = models.CharField(max_length=50)
    designation = models.CharField(max_length=50)
    joining_date = models.DateField()

    # --- added fields ---
    address = models.TextField(blank=True, default="")
    profile_photo = models.FileField(upload_to=profile_photo_path, blank=True, default="")

    def __str__(self):
        return f"{self.employee_id} - {self.first_name} {self.last_name}"

    @property
    def full_name(self):
        return f"{self.first_name} {self.last_name}".strip()

    @property
    def initials(self):
        return f"{self.first_name[:1]}{self.last_name[:1]}".upper()


class Leave(models.Model):
    employee = models.ForeignKey(Employee, on_delete=models.CASCADE)
    leave_type = models.CharField(max_length=30)
    start_date = models.DateField()
    end_date = models.DateField()
    reason = models.TextField()
    status = models.CharField(max_length=20, default="Pending")

    # --- added field: optional supporting document (e.g. medical certificate) ---
    document = models.FileField(upload_to=leave_document_path, blank=True, default="")

    def __str__(self):
        return f"{self.employee.first_name} - {self.leave_type}"

    @property
    def days(self):
        """Number of calendar days requested (start and end date both included)."""
        return (self.end_date - self.start_date).days + 1


# ---------------------------------------------------------------------------
# Leave balance
# ---------------------------------------------------------------------------

class LeaveBalance(models.Model):
    """
    Optional per-employee override of the yearly allowance for one leave type.

    Only the ALLOWANCE is stored. Days already used are always calculated
    from the employee's *Approved* leaves, so the balance can never get out
    of sync and an approval can never be deducted twice.

    If no row exists, DEFAULT_ANNUAL_ALLOCATION is used.
    """

    employee = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name="balances")
    leave_type = models.CharField(max_length=30)
    year = models.PositiveSmallIntegerField()
    allocated = models.PositiveSmallIntegerField(help_text="Days allowed for this year")

    class Meta:
        unique_together = ("employee", "leave_type", "year")
        ordering = ["employee", "year", "leave_type"]

    def __str__(self):
        return f"{self.employee.employee_id} {self.leave_type} {self.year}: {self.allocated}"


# ---------------------------------------------------------------------------
# Notifications
# ---------------------------------------------------------------------------

class Notification(models.Model):
    KIND_CHOICES = [
        ("info", "Info"),
        ("success", "Success"),
        ("warning", "Warning"),
        ("danger", "Danger"),
    ]

    recipient = models.ForeignKey(User, on_delete=models.CASCADE, related_name="notifications")
    title = models.CharField(max_length=120)
    message = models.TextField()
    kind = models.CharField(max_length=10, choices=KIND_CHOICES, default="info")
    is_read = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [models.Index(fields=["recipient", "is_read"])]

    def __str__(self):
        return f"{self.recipient.username}: {self.title}"
