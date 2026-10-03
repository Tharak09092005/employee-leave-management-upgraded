from django.contrib import admin

from .models import Employee, Leave, LeaveBalance, Notification


@admin.register(Employee)
class EmployeeAdmin(admin.ModelAdmin):
    list_display = (
        "employee_id",
        "first_name",
        "email",
        "department",
    )
    search_fields = ("employee_id", "first_name", "last_name", "email", "department")


@admin.register(Leave)
class LeaveAdmin(admin.ModelAdmin):
    list_display = (
        "employee",
        "leave_type",
        "start_date",
        "end_date",
        "status",
    )

    list_filter = (
        "status",
        "leave_type",
    )

    search_fields = (
        "employee__first_name",
        "employee__employee_id",
    )


@admin.register(LeaveBalance)
class LeaveBalanceAdmin(admin.ModelAdmin):
    """Use this to give one employee a different yearly allowance."""
    list_display = ("employee", "leave_type", "year", "allocated")
    list_filter = ("year", "leave_type")
    search_fields = ("employee__employee_id", "employee__first_name")


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ("recipient", "title", "is_read", "created_at")
    list_filter = ("is_read", "kind")
