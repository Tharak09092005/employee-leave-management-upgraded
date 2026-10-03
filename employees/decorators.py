"""
Role-based access decorators.

The project has two kinds of authenticated users:
    - Employees  -> request.user.is_staff == False
    - Admins/HR  -> request.user.is_staff == True

Plain @login_required is not enough because it does not know about roles,
and Django's @staff_member_required sends unauthenticated users to the
*Django admin* login page instead of our own login page.
"""

from functools import wraps

from django.contrib import messages
from django.contrib.auth import logout
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect

from .models import Employee


def employee_required(view_func):
    """
    Allow access only to logged-in, non-staff (employee) users.

    The employee's own Employee record is attached as `request.employee`,
    so views never need an employee ID from the URL (which an attacker
    could change to see somebody else's data).
    """

    @wraps(view_func)
    @login_required(login_url="login")
    def _wrapped(request, *args, **kwargs):
        if request.user.is_staff:
            # Admins don't have employee dashboards - send them home.
            return redirect("admin_dashboard")

        employee = Employee.objects.filter(user=request.user).first()
        if employee is None:
            logout(request)
            messages.error(request, "No employee profile is linked to this account. Please contact HR.")
            return redirect("login")

        request.employee = employee
        return view_func(request, *args, **kwargs)

    return _wrapped


def admin_required(view_func):
    """Allow access only to logged-in, staff (admin/HR) users."""

    @wraps(view_func)
    @login_required(login_url="login")
    def _wrapped(request, *args, **kwargs):
        if not request.user.is_staff:
            messages.error(
                request,
                "You do not have permission to access the admin area.",
            )
            return redirect("dashboard")
        return view_func(request, *args, **kwargs)

    return _wrapped
