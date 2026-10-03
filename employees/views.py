import os
import re
from datetime import date

from django.contrib import messages
from django.contrib.auth import authenticate, login, logout, update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.core.validators import validate_email
from django.db import IntegrityError, transaction
from django.db.models import Q, Value
from django.db.models.functions import Concat
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from . import services
from .decorators import admin_required, employee_required
from .forms import (
    ApplyLeaveForm,
    EditProfileForm,
    ProfilePhotoForm,
    StyledPasswordChangeForm,
)
from .models import LEAVE_STATUSES, LEAVE_TYPES, Employee, Leave, Notification

PHONE_RE = re.compile(r"^\+?[0-9]{7,15}$")
EMPLOYEES_PER_PAGE = 10


# ---------------------------------------------------------------------------
# Public pages (unchanged behaviour)
# ---------------------------------------------------------------------------

def home(request):
    if request.user.is_authenticated:
        return redirect("admin_dashboard" if request.user.is_staff else "dashboard")
    return render(request, "home.html")


def register(request):
    if request.user.is_authenticated:
        return redirect("admin_dashboard" if request.user.is_staff else "dashboard")

    if request.method == "POST":
        employee_id = request.POST.get("employee_id", "").strip()
        first_name = request.POST.get("first_name", "").strip()
        last_name = request.POST.get("last_name", "").strip()
        email = request.POST.get("email", "").strip()
        phone = request.POST.get("phone", "").strip()
        password = request.POST.get("password", "")
        confirm_password = request.POST.get("confirm_password", "")

        form_data = {
            "employee_id": employee_id,
            "first_name": first_name,
            "last_name": last_name,
            "email": email,
            "phone": phone,
        }

        errors = []

        if not employee_id:
            errors.append("Employee ID is required.")
        if not first_name:
            errors.append("First name is required.")
        if not last_name:
            errors.append("Last name is required.")
        if not email:
            errors.append("Email is required.")
        if not phone:
            errors.append("Phone number is required.")

        if email:
            try:
                validate_email(email)
            except ValidationError:
                errors.append("Enter a valid email address.")

        if phone and not PHONE_RE.match(phone):
            errors.append("Enter a valid phone number (7-15 digits).")

        if not password or not confirm_password:
            errors.append("Password and Confirm Password are required.")
        elif password != confirm_password:
            errors.append("Password and Confirm Password do not match.")
        else:
            try:
                validate_password(password)
            except ValidationError as exc:
                errors.extend(exc.messages)

        if employee_id and User.objects.filter(username__iexact=employee_id).exists():
            errors.append("Employee ID already exists.")
        if email and Employee.objects.filter(email__iexact=email).exists():
            errors.append("Email is already registered.")

        if errors:
            for err in errors:
                messages.error(request, err)
            return render(request, "register.html", {"form_data": form_data})

        try:
            with transaction.atomic():
                user = User.objects.create_user(
                    username=employee_id,
                    email=email,
                    password=password,
                    first_name=first_name,
                    last_name=last_name,
                )
                Employee.objects.create(
                    user=user,
                    employee_id=employee_id,
                    first_name=first_name,
                    last_name=last_name,
                    email=email,
                    phone=phone,
                    department="Not Assigned",
                    designation="Employee",
                    joining_date=timezone.now().date(),
                )
        except IntegrityError:
            messages.error(
                request,
                "Registration failed because that Employee ID or email is already in use.",
            )
            return render(request, "register.html", {"form_data": form_data})

        messages.success(request, "Employee registered successfully! You can now log in.")
        return redirect("login")

    return render(request, "register.html")


def login_view(request):
    if request.user.is_authenticated:
        return redirect("admin_dashboard" if request.user.is_staff else "dashboard")

    if request.method == "POST":
        username = request.POST.get("username", "").strip()
        password = request.POST.get("password", "")

        user = authenticate(request, username=username, password=password)

        if user is not None:
            login(request, user)
            return redirect("admin_dashboard" if user.is_staff else "dashboard")

        messages.error(request, "Invalid Employee ID or password.")
        return render(request, "login.html", {"username": username})

    return render(request, "login.html")


@login_required(login_url="login")
def logout_view(request):
    logout(request)
    messages.success(request, "You have been logged out successfully.")
    return redirect("login")


# ---------------------------------------------------------------------------
# Employee pages
# ---------------------------------------------------------------------------

@employee_required
def dashboard(request):
    employee = request.employee
    leaves = Leave.objects.filter(employee=employee)

    context = {
        "employee": employee,
        "total": leaves.count(),
        "pending": leaves.filter(status="Pending").count(),
        "approved": leaves.filter(status="Approved").count(),
        "rejected": leaves.filter(status="Rejected").count(),
        "recent_leaves": leaves.order_by("-id")[:5],
        "balance": services.get_leave_balance(employee),
        "recent_notifications": Notification.objects.filter(recipient=request.user)[:3],
    }
    return render(request, "dashboard.html", context)


@employee_required
def apply_leave(request):
    employee = request.employee

    if request.method == "POST":
        form = ApplyLeaveForm(request.POST, request.FILES, employee=employee)
        if form.is_valid():
            data = form.cleaned_data
            leave = Leave.objects.create(
                employee=employee,
                leave_type=data["leave_type"],
                start_date=data["start_date"],
                end_date=data["end_date"],
                reason=data["reason"],
                document=data["document"] or "",
            )
            services.notify_leave_submitted(leave)
            messages.success(request, "Leave request submitted successfully.")
            return redirect("leave_history")

        for errors in form.errors.values():
            for error in errors:
                messages.error(request, error)
        return render(request, "apply_leave.html", {
            "leave_types": LEAVE_TYPES,
            "form_data": request.POST,
            "balance": services.get_leave_balance(employee),
        })

    return render(request, "apply_leave.html", {
        "leave_types": LEAVE_TYPES,
        "balance": services.get_leave_balance(employee),
    })


@employee_required
def leave_history(request):
    leaves = Leave.objects.filter(employee=request.employee).order_by("-id")
    leaves, filters, errors = services.apply_leave_filters(leaves, request.GET)
    for error in errors:
        messages.warning(request, error)

    return render(request, "leave_history.html", {
        "leaves": leaves,
        "filters": filters,
        "status_filter": filters["status"],
        "leave_types": LEAVE_TYPES,
        "statuses": LEAVE_STATUSES,
    })


@employee_required
def profile(request):
    return render(request, "profile.html", {
        "employee": request.employee,
        "balance": services.get_leave_balance(request.employee),
    })


@employee_required
def edit_profile(request):
    employee = request.employee

    if request.method == "POST":
        form = EditProfileForm(request.POST, instance=employee)
        if form.is_valid():
            try:
                with transaction.atomic():
                    form.save_with_user()
            except IntegrityError:
                messages.error(request, "Unable to update profile.")
            else:
                messages.success(request, "Profile updated successfully.")
                return redirect("profile")
        else:
            messages.error(request, "Unable to update profile.")
    else:
        form = EditProfileForm(instance=employee)

    return render(request, "edit_profile.html", {"form": form, "employee": employee})


@employee_required
@require_POST
def upload_photo(request):
    employee = request.employee
    form = ProfilePhotoForm(request.POST, request.FILES)
    if not form.is_valid():
        for errors in form.errors.values():
            for error in errors:
                messages.error(request, error)
        return redirect("profile")

    old_name = employee.profile_photo.name
    employee.profile_photo = form.cleaned_data["photo"]
    employee.save(update_fields=["profile_photo"])
    if old_name:                                    # remove the previous file
        employee.profile_photo.storage.delete(old_name)

    messages.success(request, "Profile photo updated successfully.")
    return redirect("profile")


@employee_required
@require_POST
def remove_photo(request):
    employee = request.employee
    if employee.profile_photo:
        employee.profile_photo.storage.delete(employee.profile_photo.name)
        employee.profile_photo = ""
        employee.save(update_fields=["profile_photo"])
        messages.success(request, "Profile photo removed.")
    return redirect("profile")


@employee_required
def change_password(request):
    if request.method == "POST":
        form = StyledPasswordChangeForm(request.user, request.POST)
        if form.is_valid():
            user = form.save()
            update_session_auth_hash(request, user)     # keep the user logged in
            messages.success(request, "Password changed successfully.")
            return redirect("profile")
        messages.error(request, "Unable to change password. Please fix the errors below.")
    else:
        form = StyledPasswordChangeForm(request.user)

    return render(request, "change_password.html", {"form": form})


# ---------------------------------------------------------------------------
# Notifications (employees and admins - each user only sees their own)
# ---------------------------------------------------------------------------

@login_required(login_url="login")
def notifications(request):
    items = Notification.objects.filter(recipient=request.user)[:100]
    return render(request, "notifications.html", {"notifications": items})


@login_required(login_url="login")
@require_POST
def notification_mark_read(request, notification_id):
    # Filtering by recipient means nobody can touch another user's notification.
    Notification.objects.filter(pk=notification_id, recipient=request.user).update(is_read=True)
    return redirect("notifications")


@login_required(login_url="login")
@require_POST
def notification_mark_all_read(request):
    Notification.objects.filter(recipient=request.user, is_read=False).update(is_read=True)
    messages.success(request, "All notifications marked as read.")
    return redirect("notifications")


# ---------------------------------------------------------------------------
# Calendar (employees see their own leaves, admins see everyone's)
# ---------------------------------------------------------------------------

@login_required(login_url="login")
def leave_calendar(request):
    year, month = services.parse_year_month(request.GET)
    first_day = date(year, month, 1)
    previous_month, next_month = services.month_neighbours(year, month)
    last_day = next_month.replace(day=1)       # first day of next month (exclusive)

    leaves = Leave.objects.select_related("employee").filter(
        start_date__lt=last_day, end_date__gte=first_day
    )
    if not request.user.is_staff:
        employee = Employee.objects.filter(user=request.user).first()
        leaves = leaves.filter(employee=employee) if employee else leaves.none()
    leaves = leaves.order_by("start_date", "employee__first_name")

    today = timezone.localdate()
    return render(request, "leave_calendar.html", {
        "weeks": services.build_calendar(year, month, leaves),
        "month_leaves": leaves,
        "month_start": first_day,
        "previous_month": previous_month,
        "next_month": next_month,
        "is_current_month": (year, month) == (today.year, today.month),
        "weekdays": ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"],
    })


# ---------------------------------------------------------------------------
# Leave documents (private: owner or admin only)
# ---------------------------------------------------------------------------

@login_required(login_url="login")
def leave_document(request, leave_id):
    leave = get_object_or_404(Leave.objects.select_related("employee"), pk=leave_id)

    owns_leave = leave.employee.user_id == request.user.id
    if not (request.user.is_staff or owns_leave) or not leave.document:
        raise Http404("Document not found")      # 404 so we don't reveal what exists

    extension = os.path.splitext(leave.document.name)[1]
    return FileResponse(
        leave.document.open("rb"),
        as_attachment=True,
        filename=f"leave-{leave.id}-document{extension}",
    )


# ---------------------------------------------------------------------------
# Admin-only pages
# ---------------------------------------------------------------------------

@admin_required
def admin_dashboard(request):
    return render(request, "admin_dashboard.html", services.get_admin_stats())


@admin_required
def manage_leaves(request):
    leaves = Leave.objects.select_related("employee").order_by("-id")

    status_filter = request.GET.get("status", "").strip()
    if status_filter in LEAVE_STATUSES:
        leaves = leaves.filter(status=status_filter)

    return render(request, "manage_leaves.html", {
        "leaves": leaves,
        "status_filter": status_filter,
    })


@admin_required
def employees_list(request):
    query = request.GET.get("q", "").strip()

    employees = Employee.objects.annotate(
        full_name_search=Concat("first_name", Value(" "), "last_name")
    ).order_by("first_name", "last_name")

    if query:
        employees = employees.filter(
            Q(employee_id__icontains=query)
            | Q(first_name__icontains=query)
            | Q(last_name__icontains=query)
            | Q(full_name_search__icontains=query)
            | Q(email__icontains=query)
            | Q(department__icontains=query)
        )

    page = Paginator(employees, EMPLOYEES_PER_PAGE).get_page(request.GET.get("page"))

    params = request.GET.copy()
    params.pop("page", None)

    return render(request, "employees_list.html", {
        "page": page,
        "query": query,
        "querystring": params.urlencode(),
    })


@admin_required
def employee_detail(request, employee_pk):
    employee = get_object_or_404(Employee, pk=employee_pk)
    leaves = Leave.objects.filter(employee=employee).order_by("-id")
    return render(request, "employee_detail.html", {
        "employee": employee,
        "balance": services.get_leave_balance(employee),
        "recent_leaves": leaves[:10],
        "leave_count": leaves.count(),
    })


def _review(request, leave_id, approve):
    result, leave, extra = services.review_leave(leave_id, approve)

    if result == services.NOT_FOUND:
        messages.error(request, "That leave request no longer exists.")
    elif result == services.ALREADY_PROCESSED:
        messages.warning(request, f"Leave request #{leave.id} was already {leave.status.lower()}. No changes made.")
    elif result == services.INSUFFICIENT:
        days, remaining, year = extra
        messages.error(
            request,
            f"Insufficient leave balance: {leave.employee.full_name} needs {days} day(s) of "
            f"{leave.leave_type} but only has {remaining} day(s) left for {year}.",
        )
    elif result == services.APPROVED:
        messages.success(request, "Leave request approved successfully.")
    else:
        messages.success(request, "Leave request rejected successfully.")
    return redirect("manage_leaves")


@admin_required
@require_POST
def approve_leave(request, leave_id):
    return _review(request, leave_id, approve=True)


@admin_required
@require_POST
def reject_leave(request, leave_id):
    return _review(request, leave_id, approve=False)
