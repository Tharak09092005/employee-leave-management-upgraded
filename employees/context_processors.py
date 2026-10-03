from .models import Employee, Notification


def navbar_context(request):
    """
    Values needed by the navbar on every page:
        unread_notifications - unread count for the bell icon
        nav_employee         - the employee (for the small avatar), employees only
    """
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated:
        return {}

    context = {
        "unread_notifications": Notification.objects.filter(recipient=user, is_read=False).count(),
    }
    if not user.is_staff:
        context["nav_employee"] = (
            Employee.objects.filter(user=user)
            .only("first_name", "last_name", "profile_photo")
            .first()
        )
    return context
