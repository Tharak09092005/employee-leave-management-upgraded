from django.urls import path
from . import views

urlpatterns = [
    # Public
    path("", views.home, name="home"),
    path("register/", views.register, name="register"),
    path("login/", views.login_view, name="login"),
    path("logout/", views.logout_view, name="logout"),

    # Employee
    path("dashboard/", views.dashboard, name="dashboard"),
    path("apply-leave/", views.apply_leave, name="apply_leave"),
    path("leave-history/", views.leave_history, name="leave_history"),
    path("profile/", views.profile, name="profile"),
    path("profile/edit/", views.edit_profile, name="edit_profile"),
    path("profile/photo/", views.upload_photo, name="upload_photo"),
    path("profile/photo/remove/", views.remove_photo, name="remove_photo"),
    path("change-password/", views.change_password, name="change_password"),

    # Both roles (each user only sees their own data)
    path("notifications/", views.notifications, name="notifications"),
    path("notifications/read-all/", views.notification_mark_all_read, name="notification_mark_all_read"),
    path("notifications/<int:notification_id>/read/", views.notification_mark_read, name="notification_mark_read"),
    path("leave-calendar/", views.leave_calendar, name="leave_calendar"),
    path("leaves/<int:leave_id>/document/", views.leave_document, name="leave_document"),

    # Admin
    path("admin-dashboard/", views.admin_dashboard, name="admin_dashboard"),
    path("manage-leaves/", views.manage_leaves, name="manage_leaves"),
    path("employees/", views.employees_list, name="employees_list"),
    path("employees/<int:employee_pk>/", views.employee_detail, name="employee_detail"),
    path("approve-leave/<int:leave_id>/", views.approve_leave, name="approve_leave"),
    path("reject-leave/<int:leave_id>/", views.reject_leave, name="reject_leave"),
]
