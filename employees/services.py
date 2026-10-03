"""
Business logic kept out of views.py so the views stay short and readable.

Sections:
    1. Leave balance
    2. Notifications
    3. Approving / rejecting leave
    4. Leave-history filters
    5. Admin dashboard statistics
    6. Calendar
"""

import calendar
from collections import defaultdict
from datetime import date, timedelta

from django.contrib.auth.models import User
from django.db import transaction
from django.db.models import Count
from django.db.models.functions import TruncMonth
from django.utils import timezone
from django.utils.dateparse import parse_date

from .models import (
    DEFAULT_ANNUAL_ALLOCATION,
    LEAVE_STATUSES,
    LEAVE_TYPES,
    STATUS_APPROVED,
    STATUS_PENDING,
    STATUS_REJECTED,
    Leave,
    LeaveBalance,
    Notification,
)


def pretty_date(value):
    """10 October 2026  (works on Windows too, unlike strftime('%-d'))."""
    return f"{value.day} {value:%B %Y}"


# ---------------------------------------------------------------------------
# 1. Leave balance
# ---------------------------------------------------------------------------

def get_leave_balance(employee, year=None):
    """
    Balance summary for one employee and one calendar year.

        allocated  = yearly allowance (LeaveBalance override or the default)
        used       = days of APPROVED leaves starting in that year
        remaining  = allocated - used
        pending    = days of requests still waiting (NOT deducted yet)
    """
    year = year or timezone.localdate().year

    overrides = {
        row.leave_type: row.allocated
        for row in LeaveBalance.objects.filter(employee=employee, year=year)
    }

    used = defaultdict(int)
    pending = defaultdict(int)
    for leave in Leave.objects.filter(employee=employee, start_date__year=year):
        if leave.status == STATUS_APPROVED:
            used[leave.leave_type] += leave.days
        elif leave.status == STATUS_PENDING:
            pending[leave.leave_type] += leave.days

    rows = []
    for leave_type in LEAVE_TYPES:
        allocated = overrides.get(leave_type, DEFAULT_ANNUAL_ALLOCATION[leave_type])
        remaining = allocated - used[leave_type]
        rows.append({
            "leave_type": leave_type,
            "allocated": allocated,
            "used": used[leave_type],
            "remaining": max(remaining, 0),
            "pending": pending[leave_type],
            "percent_used": min(round(used[leave_type] * 100 / allocated), 100) if allocated else 0,
        })

    return {
        "year": year,
        "rows": rows,
        "by_type": {r["leave_type"]: r for r in rows},
        "total_allocated": sum(r["allocated"] for r in rows),
        "total_used": sum(r["used"] for r in rows),
        "total_remaining": sum(r["remaining"] for r in rows),
        "total_pending": sum(r["pending"] for r in rows),
    }


def get_balance_shortfall(employee, leave_type, start_date, end_date):
    """
    Return None if the employee has enough balance for this request,
    otherwise (days_requested, days_remaining, year).
    """
    year = start_date.year
    days = (end_date - start_date).days + 1
    row = get_leave_balance(employee, year)["by_type"].get(leave_type)
    if row is None:                     # unknown/legacy leave type: no limit
        return None
    if days > row["remaining"]:
        return days, row["remaining"], year
    return None


# ---------------------------------------------------------------------------
# 2. Notifications
# ---------------------------------------------------------------------------

def notify(user, title, message, kind="info"):
    """Create one notification for one user (silently skipped if user is None)."""
    if user is None:
        return None
    return Notification.objects.create(recipient=user, title=title, message=message, kind=kind)


def notify_admins(title, message, kind="info"):
    """Create the same notification for every active admin/HR (staff) account."""
    admins = User.objects.filter(is_staff=True, is_active=True)
    Notification.objects.bulk_create([
        Notification(recipient=admin, title=title, message=message, kind=kind)
        for admin in admins
    ])


def notify_leave_submitted(leave):
    employee = leave.employee
    period = f"{pretty_date(leave.start_date)} to {pretty_date(leave.end_date)}"
    notify(
        employee.user,
        "Leave Request Submitted",
        f"Your {leave.leave_type} request from {period} has been submitted and is waiting for approval.",
        "info",
    )
    extra = " A supporting document is attached." if leave.document else ""
    notify_admins(
        "New Leave Request",
        f"{employee.full_name} ({employee.employee_id}) requested {leave.leave_type} "
        f"from {period} ({leave.days} day(s)).{extra}",
        "warning",
    )


# ---------------------------------------------------------------------------
# 3. Approving / rejecting leave
# ---------------------------------------------------------------------------

# Result codes returned by review_leave()
APPROVED = "approved"
REJECTED = "rejected"
ALREADY_PROCESSED = "already_processed"
INSUFFICIENT = "insufficient"
NOT_FOUND = "not_found"


def review_leave(leave_id, approve):
    """
    Approve or reject a leave request. Returns (result_code, leave, extra).

    Double-deduction protection:
      * Only a request that is still "Pending" can be changed. The status is
        flipped with a single UPDATE ... WHERE status = 'Pending', so if the
        admin double-clicks or opens the same request twice, the second call
        updates 0 rows and does nothing.
      * Balance is derived from Approved leaves, so there is no separate
        counter that could be decreased twice.
    """
    with transaction.atomic():
        try:
            leave = Leave.objects.select_related("employee", "employee__user").get(pk=leave_id)
        except Leave.DoesNotExist:
            return NOT_FOUND, None, None

        if leave.status != STATUS_PENDING:
            return ALREADY_PROCESSED, leave, None

        if approve:
            shortfall = get_balance_shortfall(
                leave.employee, leave.leave_type, leave.start_date, leave.end_date
            )
            if shortfall:
                return INSUFFICIENT, leave, shortfall

        new_status = STATUS_APPROVED if approve else STATUS_REJECTED
        changed = Leave.objects.filter(pk=leave.pk, status=STATUS_PENDING).update(status=new_status)
        if changed == 0:                # someone else processed it a moment ago
            return ALREADY_PROCESSED, leave, None

        leave.status = new_status
        period = f"{pretty_date(leave.start_date)} to {pretty_date(leave.end_date)}"
        if approve:
            notify(
                leave.employee.user,
                "Leave Request Approved",
                f"Your leave request from {period} has been approved.",
                "success",
            )
            return APPROVED, leave, None

        notify(
            leave.employee.user,
            "Leave Request Rejected",
            f"Your leave request from {period} has been rejected.",
            "danger",
        )
        return REJECTED, leave, None


# ---------------------------------------------------------------------------
# 4. Leave-history filters (real database queries)
# ---------------------------------------------------------------------------

def _parse_filter_date(raw):
    """Return (date_or_None, is_invalid). Empty input is valid and means 'no date'."""
    if not raw:
        return None, False
    try:
        value = parse_date(raw)         # None if badly formatted, ValueError if e.g. 2026-02-30
    except ValueError:
        value = None
    return value, value is None


def apply_leave_filters(queryset, params):
    """
    Filter a Leave queryset using GET parameters:
        status, leave_type, from_date, to_date

    Date range rule: a leave is shown if it OVERLAPS the chosen range.
    Returns (queryset, cleaned_filters, error_messages).
    """
    errors = []
    status = params.get("status", "").strip()
    leave_type = params.get("leave_type", "").strip()
    from_raw = params.get("from_date", "").strip()
    to_raw = params.get("to_date", "").strip()

    if status in LEAVE_STATUSES:
        queryset = queryset.filter(status=status)
    else:
        status = ""

    if leave_type in LEAVE_TYPES:
        queryset = queryset.filter(leave_type=leave_type)
    else:
        leave_type = ""

    from_date, from_bad = _parse_filter_date(from_raw)
    to_date, to_bad = _parse_filter_date(to_raw)
    if from_bad or to_bad:
        errors.append("Enter valid dates for the date filter.")
        from_date = to_date = None

    if from_date and to_date and from_date > to_date:
        errors.append("'From' date cannot be after 'To' date.")
        from_date = to_date = None

    if from_date:
        queryset = queryset.filter(end_date__gte=from_date)
    if to_date:
        queryset = queryset.filter(start_date__lte=to_date)

    cleaned = {
        "status": status,
        "leave_type": leave_type,
        "from_date": from_date.isoformat() if from_date else "",
        "to_date": to_date.isoformat() if to_date else "",
    }
    cleaned["active"] = any(cleaned.values())
    return queryset, cleaned, errors


# ---------------------------------------------------------------------------
# 5. Admin dashboard statistics (everything comes from the database)
# ---------------------------------------------------------------------------

STATUS_COLORS = {
    STATUS_PENDING: "#f5a524",
    STATUS_APPROVED: "#22a06b",
    STATUS_REJECTED: "#e23c46",
}


def _bars(pairs, color="#3557ff"):
    """[(label, count), ...] -> list of dicts with a bar width in percent."""
    biggest = max([count for _, count in pairs], default=0)
    bars = []
    for label, count in pairs:
        item_color = color(label) if callable(color) else color
        bars.append({
            "label": label,
            "count": count,
            "percent": round(count * 100 / biggest) if biggest else 0,
            "color": item_color,
        })
    return bars


def _last_months(count, today):
    """The last `count` (year, month) pairs, oldest first, ending this month."""
    months = []
    year, month = today.year, today.month
    for _ in range(count):
        months.append((year, month))
        month -= 1
        if month == 0:
            year, month = year - 1, 12
    return list(reversed(months))


def get_admin_stats():
    from .models import Employee  # local import avoids a circular import at start-up

    status_counts = dict(
        Leave.objects.values_list("status").annotate(n=Count("id")).order_by()
    )
    type_counts = dict(
        Leave.objects.values_list("leave_type").annotate(n=Count("id")).order_by()
    )
    dept_counts = list(
        Leave.objects.values_list("employee__department")
        .annotate(n=Count("id")).order_by("-n")[:8]
    )

    today = timezone.localdate()
    months = _last_months(6, today)
    first_month = date(months[0][0], months[0][1], 1)
    monthly_raw = {
        (row["month"].year, row["month"].month): row["n"]
        for row in Leave.objects.filter(start_date__gte=first_month)
        .annotate(month=TruncMonth("start_date"))
        .values("month").annotate(n=Count("id")).order_by()
    }
    monthly_pairs = [
        (f"{calendar.month_abbr[m]} {y}", monthly_raw.get((y, m), 0)) for y, m in months
    ]

    return {
        "total_employees": Employee.objects.count(),
        "total_leaves": sum(status_counts.values()),
        "pending": status_counts.get(STATUS_PENDING, 0),
        "approved": status_counts.get(STATUS_APPROVED, 0),
        "rejected": status_counts.get(STATUS_REJECTED, 0),
        "status_chart": _bars(
            [(s, status_counts.get(s, 0)) for s in LEAVE_STATUSES],
            color=lambda label: STATUS_COLORS[label],
        ),
        "monthly_chart": _bars(monthly_pairs, color="#3557ff"),
        "type_chart": _bars(
            [(t, type_counts.get(t, 0)) for t in LEAVE_TYPES], color="#5b5fc7"
        ),
        "department_chart": _bars(dept_counts, color="#0e9aa7"),
    }


# ---------------------------------------------------------------------------
# 6. Calendar
# ---------------------------------------------------------------------------

def parse_year_month(params):
    """Read ?year=&month= safely; fall back to the current month."""
    today = timezone.localdate()
    try:
        year = int(params.get("year", today.year))
        month = int(params.get("month", today.month))
        date(year, month, 1)            # raises ValueError if out of range
    except (TypeError, ValueError):
        return today.year, today.month
    return year, month


def month_neighbours(year, month):
    """First day of the previous and next month (for the navigation buttons)."""
    first = date(year, month, 1)
    previous_month = first - timedelta(days=1)
    next_month = (first + timedelta(days=32)).replace(day=1)
    return previous_month, next_month


def build_calendar(year, month, leaves):
    """
    Build a month grid (weeks of 7 days, Monday first).

    `leaves` must already be limited to the right employee(s) - this
    function only places them on the correct days.
    """
    weeks = calendar.Calendar(firstweekday=0).monthdatescalendar(year, month)
    grid_start, grid_end = weeks[0][0], weeks[-1][-1]

    by_day = defaultdict(list)
    for leave in leaves:
        day = max(leave.start_date, grid_start)
        last = min(leave.end_date, grid_end)
        while day <= last:
            by_day[day].append(leave)
            day += timedelta(days=1)

    today = timezone.localdate()
    return [
        [
            {
                "date": day,
                "in_month": day.month == month,
                "is_today": day == today,
                "leaves": by_day.get(day, []),
            }
            for day in week
        ]
        for week in weeks
    ]
