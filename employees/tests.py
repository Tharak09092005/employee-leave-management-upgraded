import shutil
import tempfile
from datetime import date, timedelta

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from . import services
from .models import Employee, Leave, LeaveBalance, Notification

TMP_MEDIA = tempfile.mkdtemp()
PDF = b"%PDF-1.4\n%fake but valid header\n"
PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 50
YEAR = timezone.localdate().year


def make_employee(emp_id, email, dept="IT", password="Str0ng!Pass#99"):
    user = User.objects.create_user(emp_id, email, password, first_name=emp_id, last_name="Test")
    return Employee.objects.create(
        user=user, employee_id=emp_id, first_name=emp_id, last_name="Test", email=email,
        phone="9876543210", department=dept, designation="Dev", joining_date=date(2024, 1, 1),
    )


def make_leave(emp, days=2, leave_type="Sick Leave", status="Pending", start=None, **kw):
    start = start or date(YEAR, 3, 10)
    return Leave.objects.create(
        employee=emp, leave_type=leave_type, start_date=start,
        end_date=start + timedelta(days=days - 1), reason="r", status=status, **kw)


@override_settings(MEDIA_ROOT=TMP_MEDIA)
class BaseCase(TestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(TMP_MEDIA, ignore_errors=True)

    def setUp(self):
        self.alice = make_employee("EMP1", "alice@x.com")
        self.bob = make_employee("EMP2", "bob@x.com", dept="HR")
        self.admin = User.objects.create_user("boss", "boss@x.com", "Adm1n!Pass#99", is_staff=True)

    def login(self, username, password="Str0ng!Pass#99"):
        self.client.logout()
        self.assertTrue(self.client.login(username=username, password=password))

    def login_admin(self):
        self.login("boss", "Adm1n!Pass#99")

    def apply(self, **over):
        data = {"leave_type": "Sick Leave", "start_date": f"{YEAR}-05-04",
                "end_date": f"{YEAR}-05-05", "reason": "Fever"}
        data.update(over)
        return self.client.post(reverse("apply_leave"), data, follow=True)


class BalanceTests(BaseCase):
    def test_submit_does_not_reduce_balance_approve_does_reject_does_not(self):
        self.login("EMP1")
        self.apply()
        leave = Leave.objects.get()
        b = services.get_leave_balance(self.alice)["by_type"]["Sick Leave"]
        self.assertEqual((b["used"], b["remaining"], b["pending"]), (0, 10, 2))

        self.login_admin()
        self.client.post(reverse("approve_leave", args=[leave.id]))
        b = services.get_leave_balance(self.alice)["by_type"]["Sick Leave"]
        self.assertEqual((b["used"], b["remaining"]), (2, 8))

        other = make_leave(self.alice, days=3, start=date(YEAR, 6, 1))
        self.client.post(reverse("reject_leave", args=[other.id]))
        b = services.get_leave_balance(self.alice)["by_type"]["Sick Leave"]
        self.assertEqual(b["used"], 2)                       # rejection deducted nothing

    def test_no_double_deduction_when_processed_twice(self):
        leave = make_leave(self.alice, days=4)
        self.login_admin()
        for _ in range(3):
            self.client.post(reverse("approve_leave", args=[leave.id]))
        self.assertEqual(services.get_leave_balance(self.alice)["by_type"]["Sick Leave"]["used"], 4)
        # approving then rejecting the same request must not change anything either
        self.client.post(reverse("reject_leave", args=[leave.id]))
        leave.refresh_from_db()
        self.assertEqual(leave.status, "Approved")
        self.assertEqual(services.get_leave_balance(self.alice)["by_type"]["Sick Leave"]["used"], 4)
        self.assertEqual(Notification.objects.filter(recipient=self.alice.user, title="Leave Request Approved").count(), 1)

    def test_cannot_request_more_than_balance(self):
        self.login("EMP1")
        resp = self.apply(start_date=f"{YEAR}-05-01", end_date=f"{YEAR}-05-20")   # 20 days > 10
        self.assertContains(resp, "Insufficient leave balance")
        self.assertEqual(Leave.objects.count(), 0)

    def test_approval_rechecks_balance_for_competing_pending_requests(self):
        a = make_leave(self.alice, days=8, start=date(YEAR, 2, 1))
        b = make_leave(self.alice, days=8, start=date(YEAR, 4, 1))
        self.login_admin()
        self.client.post(reverse("approve_leave", args=[a.id]))
        resp = self.client.post(reverse("approve_leave", args=[b.id]), follow=True)
        self.assertContains(resp, "Insufficient leave balance")
        b.refresh_from_db()
        self.assertEqual(b.status, "Pending")

    def test_custom_allocation_override(self):
        LeaveBalance.objects.create(employee=self.alice, leave_type="Sick Leave", year=YEAR, allocated=3)
        self.login("EMP1")
        resp = self.apply(start_date=f"{YEAR}-05-01", end_date=f"{YEAR}-05-05")
        self.assertContains(resp, "only 3 day(s)")

    def test_leave_cannot_span_two_years(self):
        self.login("EMP1")
        resp = self.apply(start_date=f"{YEAR}-12-30", end_date=f"{YEAR + 1}-01-02")
        self.assertContains(resp, "two calendar years")

    def test_bad_dates_and_empty_reason(self):
        self.login("EMP1")
        self.assertContains(self.apply(start_date=f"{YEAR}-05-09", end_date=f"{YEAR}-05-01"), "Start date cannot be after end date")
        self.assertContains(self.apply(reason=""), "Reason cannot be empty")
        self.assertContains(self.apply(leave_type="Bogus"), "valid leave type")


class NotificationTests(BaseCase):
    def test_full_notification_flow(self):
        self.login("EMP1")
        self.apply()
        self.assertEqual(Notification.objects.filter(recipient=self.alice.user).count(), 1)
        self.assertEqual(Notification.objects.filter(recipient=self.admin).count(), 1)
        self.assertEqual(Notification.objects.filter(recipient=self.bob.user).count(), 0)

        leave = Leave.objects.get()
        self.login_admin()
        self.client.post(reverse("approve_leave", args=[leave.id]))
        self.login("EMP1")
        resp = self.client.get(reverse("dashboard"))
        self.assertEqual(resp.context["unread_notifications"], 2)
        n = Notification.objects.get(title="Leave Request Approved")
        self.assertIn("has been approved", n.message)
        self.assertIn(f"4 May {YEAR} to 5 May {YEAR}", n.message)

        self.client.post(reverse("notification_mark_read", args=[n.id]))
        self.assertEqual(self.client.get(reverse("notifications")).context["unread_notifications"], 1)
        self.client.post(reverse("notification_mark_all_read"))
        self.assertEqual(self.client.get(reverse("notifications")).context["unread_notifications"], 0)

    def test_reject_notification(self):
        leave = make_leave(self.alice)
        self.login_admin()
        self.client.post(reverse("reject_leave", args=[leave.id]))
        self.assertTrue(Notification.objects.filter(title="Leave Request Rejected", kind="danger").exists())

    def test_cannot_mark_someone_elses_notification(self):
        n = Notification.objects.create(recipient=self.bob.user, title="t", message="m")
        self.login("EMP1")
        self.client.post(reverse("notification_mark_read", args=[n.id]))
        n.refresh_from_db()
        self.assertFalse(n.is_read)

    def test_mark_read_requires_post(self):
        n = Notification.objects.create(recipient=self.alice.user, title="t", message="m")
        self.login("EMP1")
        self.assertEqual(self.client.get(reverse("notification_mark_read", args=[n.id])).status_code, 405)


class ProfileTests(BaseCase):
    def test_edit_profile_updates_employee_and_user(self):
        self.login("EMP1")
        resp = self.client.post(reverse("edit_profile"), {
            "first_name": "Alicia", "last_name": "New", "email": "alicia@x.com",
            "phone": "+919876500000", "address": "12 Main Rd"}, follow=True)
        self.assertContains(resp, "Profile updated successfully.")
        self.assertRedirects(resp, reverse("profile"))
        self.alice.refresh_from_db(); self.alice.user.refresh_from_db()
        self.assertEqual((self.alice.first_name, self.alice.address), ("Alicia", "12 Main Rd"))
        self.assertEqual((self.alice.user.first_name, self.alice.user.email), ("Alicia", "alicia@x.com"))

    def test_duplicate_email_rejected_case_insensitive(self):
        self.login("EMP1")
        resp = self.client.post(reverse("edit_profile"), {
            "first_name": "A", "last_name": "B", "email": "BOB@x.com", "phone": "9876543210"})
        self.assertContains(resp, "already used by another employee")
        self.assertContains(resp, "Unable to update profile.")
        self.alice.refresh_from_db()
        self.assertEqual(self.alice.email, "alice@x.com")

    def test_invalid_email_and_phone(self):
        self.login("EMP1")
        resp = self.client.post(reverse("edit_profile"), {
            "first_name": "A", "last_name": "B", "email": "nope", "phone": "abc"})
        self.assertContains(resp, "valid email")
        self.assertContains(resp, "valid phone")

    def test_cannot_change_protected_fields_by_tampering(self):
        self.login("EMP1")
        self.client.post(reverse("edit_profile"), {
            "first_name": "A", "last_name": "B", "email": "alice@x.com", "phone": "9876543210",
            "employee_id": "HACK", "department": "Board", "designation": "CEO"})
        self.alice.refresh_from_db()
        self.assertEqual((self.alice.employee_id, self.alice.department, self.alice.designation), ("EMP1", "IT", "Dev"))

    def test_edit_only_affects_logged_in_user(self):
        self.login("EMP1")
        self.client.post(reverse("edit_profile") + f"?id={self.bob.id}", {
            "first_name": "X", "last_name": "Y", "email": "alice@x.com", "phone": "9876543210", "id": self.bob.id})
        self.bob.refresh_from_db()
        self.assertEqual(self.bob.first_name, "EMP2")

    def test_photo_upload_replace_remove_and_validation(self):
        self.login("EMP1")
        url = reverse("upload_photo")
        self.client.post(url, {"photo": SimpleUploadedFile("me.png", PNG)})
        self.alice.refresh_from_db()
        first = self.alice.profile_photo.name
        self.assertTrue(first.startswith("profile_photos/") and first.endswith(".png"))
        self.assertTrue(self.client.get(reverse("profile")).content.decode().count(first) >= 1)

        self.client.post(url, {"photo": SimpleUploadedFile("me2.png", PNG)})
        self.alice.refresh_from_db()
        self.assertNotEqual(self.alice.profile_photo.name, first)
        self.assertFalse(self.alice.profile_photo.storage.exists(first))     # old file cleaned up

        keep = self.alice.profile_photo.name
        for bad in (SimpleUploadedFile("x.gif", b"GIF89a"), SimpleUploadedFile("x.png", b"<html>not png"),
                    SimpleUploadedFile("big.png", PNG + b"0" * (2 * 1024 * 1024))):
            resp = self.client.post(url, {"photo": bad}, follow=True)
            self.assertContains(resp, "alert-danger")
        self.alice.refresh_from_db()
        self.assertEqual(self.alice.profile_photo.name, keep)

        self.client.post(reverse("remove_photo"))
        self.alice.refresh_from_db()
        self.assertFalse(self.alice.profile_photo)

    def test_change_password(self):
        self.login("EMP1")
        url = reverse("change_password")
        self.assertContains(self.client.post(url, {"old_password": "wrong", "new_password1": "N3w!Passw0rd#", "new_password2": "N3w!Passw0rd#"}), "Invalid current password")
        self.assertContains(self.client.post(url, {"old_password": "Str0ng!Pass#99", "new_password1": "N3w!Passw0rd#", "new_password2": "Different!1"}), "didn")
        self.assertContains(self.client.post(url, {"old_password": "Str0ng!Pass#99", "new_password1": "12345678", "new_password2": "12345678"}), "error")
        resp = self.client.post(url, {"old_password": "Str0ng!Pass#99", "new_password1": "N3w!Passw0rd#", "new_password2": "N3w!Passw0rd#"}, follow=True)
        self.assertContains(resp, "Password changed successfully.")
        self.assertRedirects(resp, reverse("profile"))                      # still logged in
        self.alice.user.refresh_from_db()
        self.assertTrue(self.alice.user.check_password("N3w!Passw0rd#"))
        self.assertTrue(self.alice.user.password.startswith(("pbkdf2_", "argon2", "bcrypt", "scrypt")))
        self.login("EMP1", "N3w!Passw0rd#")


class DocumentTests(BaseCase):
    def test_upload_view_and_permissions(self):
        self.login("EMP1")
        self.apply(document=SimpleUploadedFile("cert.pdf", PDF))
        leave = Leave.objects.get()
        self.assertTrue(leave.document.name.startswith("leave_documents/"))
        self.assertContains(self.client.get(reverse("leave_history")), reverse("leave_document", args=[leave.id]))
        url = reverse("leave_document", args=[leave.id])
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(b"".join(resp.streaming_content), PDF)
        self.assertIn("attachment", resp["Content-Disposition"])

        self.login("EMP2")
        self.assertEqual(self.client.get(url).status_code, 404)             # another employee
        self.client.logout()
        self.assertEqual(self.client.get(url).status_code, 302)             # anonymous -> login
        self.login_admin()
        self.assertEqual(self.client.get(url).status_code, 200)             # admin ok
        self.assertContains(self.client.get(reverse("manage_leaves")), url)

    def test_document_is_optional_and_validated(self):
        self.login("EMP1")
        self.apply()
        self.assertEqual(Leave.objects.count(), 1)
        for bad in (SimpleUploadedFile("a.exe", b"MZ"), SimpleUploadedFile("a.pdf", b"not a pdf"),
                    SimpleUploadedFile("a.pdf", PDF + b"0" * (5 * 1024 * 1024))):
            self.apply(document=bad)
        self.assertEqual(Leave.objects.count(), 1)

    def test_documents_not_served_through_media_url(self):
        self.login("EMP1")
        self.apply(document=SimpleUploadedFile("cert.pdf", PDF))
        name = Leave.objects.get().document.name
        self.assertEqual(self.client.get("/media/" + name).status_code, 404)


class FilterTests(BaseCase):
    def setUp(self):
        super().setUp()
        make_leave(self.alice, 2, "Sick Leave", "Approved", date(YEAR, 1, 10))
        make_leave(self.alice, 2, "Casual Leave", "Pending", date(YEAR, 2, 10))
        make_leave(self.alice, 2, "Earned Leave", "Rejected", date(YEAR, 3, 10))
        make_leave(self.bob, 2, "Sick Leave", "Approved", date(YEAR, 1, 10))
        self.login("EMP1")

    def count(self, **params):
        return len(self.client.get(reverse("leave_history"), params).context["leaves"])

    def test_filters(self):
        self.assertEqual(self.count(), 3)                                    # own leaves only
        self.assertEqual(self.count(status="Approved"), 1)
        self.assertEqual(self.count(leave_type="Casual Leave"), 1)
        self.assertEqual(self.count(status="Pending", leave_type="Sick Leave"), 0)
        self.assertEqual(self.count(from_date=f"{YEAR}-02-01", to_date=f"{YEAR}-02-28"), 1)
        self.assertEqual(self.count(from_date=f"{YEAR}-02-01"), 2)
        self.assertEqual(self.count(to_date=f"{YEAR}-01-31"), 1)
        self.assertEqual(self.count(from_date=f"{YEAR}-01-11", to_date=f"{YEAR}-01-11"), 1)   # overlap rule

    def test_bad_filter_input_is_safe(self):
        self.assertEqual(self.count(status="'; DROP", from_date="garbage"), 3)
        self.assertEqual(self.count(from_date=f"{YEAR}-02-30"), 3)
        self.assertEqual(self.count(from_date=f"{YEAR}-05-01", to_date=f"{YEAR}-01-01"), 3)


class AdminTests(BaseCase):
    def test_stats_are_real_data(self):
        self.login_admin()
        ctx = self.client.get(reverse("admin_dashboard")).context
        self.assertEqual((ctx["total_employees"], ctx["total_leaves"], ctx["pending"]), (2, 0, 0))
        make_leave(self.alice, status="Approved"); make_leave(self.alice, status="Pending", start=date(YEAR, 4, 1))
        make_leave(self.bob, status="Rejected", leave_type="Casual Leave")
        ctx = self.client.get(reverse("admin_dashboard")).context
        self.assertEqual((ctx["total_leaves"], ctx["pending"], ctx["approved"], ctx["rejected"]), (3, 1, 1, 1))
        depts = {b["label"]: b["count"] for b in ctx["department_chart"]}
        self.assertEqual(depts, {"IT": 2, "HR": 1})
        types = {b["label"]: b["count"] for b in ctx["type_chart"]}
        self.assertEqual(types["Sick Leave"], 2)
        self.assertEqual(sum(b["count"] for b in ctx["status_chart"]), 3)

    def test_employee_search_and_pagination(self):
        for i in range(25):
            make_employee(f"E{i:03d}", f"e{i}@corp.com", dept="Sales")
        self.login_admin()
        url = reverse("employees_list")
        resp = self.client.get(url)
        self.assertEqual(len(resp.context["page"]), 10)
        self.assertEqual(resp.context["page"].paginator.count, 27)
        self.assertEqual(len(self.client.get(url, {"page": 3}).context["page"]), 7)
        self.assertEqual(self.client.get(url, {"page": 999}).status_code, 200)
        for term, expected in [("E007", 1), ("alice", 1), ("bob@x", 1), ("sales", 25), ("hr", 1),
                               ("EMP1 Test", 1), ("nothing-here", 0)]:
            self.assertEqual(self.client.get(url, {"q": term}).context["page"].paginator.count, expected, term)
        resp = self.client.get(url, {"q": "sales", "page": 2})
        self.assertContains(resp, "q=sales&page=3")                           # search kept in pagination

    def test_employee_detail_page(self):
        self.login_admin()
        self.assertContains(self.client.get(reverse("employee_detail", args=[self.alice.id])), "EMP1")
        self.assertEqual(self.client.get(reverse("employee_detail", args=[9999])).status_code, 404)


class CalendarTests(BaseCase):
    def setUp(self):
        super().setUp()
        make_leave(self.alice, 3, status="Approved", start=date(2026, 3, 30))
        make_leave(self.bob, 1, status="Pending", start=date(2026, 3, 12))

    def test_employee_sees_only_own(self):
        self.login("EMP1")
        resp = self.client.get(reverse("leave_calendar"), {"year": 2026, "month": 3})
        self.assertEqual([l.employee_id for l in resp.context["month_leaves"]], [self.alice.id])
        self.assertNotContains(resp, "EMP2 Test")

    def test_admin_sees_all_and_leave_spills_into_next_month(self):
        self.login_admin()
        resp = self.client.get(reverse("leave_calendar"), {"year": 2026, "month": 3})
        self.assertEqual(len(resp.context["month_leaves"]), 2)
        april = self.client.get(reverse("leave_calendar"), {"year": 2026, "month": 4})
        self.assertEqual(len(april.context["month_leaves"]), 1)               # 30 Mar - 1 Apr
        placed = [c for w in april.context["weeks"] for c in w if c["leaves"] and c["in_month"]]
        self.assertEqual([c["date"] for c in placed], [date(2026, 4, 1)])

    def test_navigation_and_bad_params(self):
        self.login("EMP1")
        dec = self.client.get(reverse("leave_calendar"), {"year": 2026, "month": 12})
        self.assertEqual((dec.context["next_month"].year, dec.context["next_month"].month), (2027, 1))
        jan = self.client.get(reverse("leave_calendar"), {"year": 2026, "month": 1})
        self.assertEqual((jan.context["previous_month"].year, jan.context["previous_month"].month), (2025, 12))
        for q in ({"year": "abc"}, {"month": 13}, {"year": 99999, "month": 1}, {"month": 0}):
            self.assertEqual(self.client.get(reverse("leave_calendar"), q).status_code, 200)


class SecurityTests(BaseCase):
    ADMIN_URLS = ["admin_dashboard", "manage_leaves", "employees_list"]
    EMPLOYEE_URLS = ["dashboard", "apply_leave", "leave_history", "profile", "edit_profile", "change_password"]
    LOGIN_URLS = ["notifications", "leave_calendar"]

    def test_anonymous_redirected_to_login(self):
        for name in self.ADMIN_URLS + self.EMPLOYEE_URLS + self.LOGIN_URLS:
            resp = self.client.get(reverse(name))
            self.assertEqual(resp.status_code, 302, name)
            self.assertTrue(resp["Location"].startswith(reverse("login")), name)
        for url in (reverse("approve_leave", args=[1]), reverse("upload_photo"), reverse("employee_detail", args=[1])):
            self.assertEqual(self.client.post(url).status_code, 302)

    def test_employee_blocked_from_admin_pages_and_actions(self):
        leave = make_leave(self.bob)
        self.login("EMP1")
        for name in self.ADMIN_URLS:
            self.assertEqual(self.client.get(reverse(name)).status_code, 302, name)
        self.assertEqual(self.client.get(reverse("employee_detail", args=[self.bob.id])).status_code, 302)
        self.client.post(reverse("approve_leave", args=[leave.id]))
        self.client.post(reverse("reject_leave", args=[leave.id]))
        leave.refresh_from_db()
        self.assertEqual(leave.status, "Pending")

    def test_admin_redirected_away_from_employee_pages(self):
        self.login_admin()
        for name in self.EMPLOYEE_URLS:
            self.assertRedirects(self.client.get(reverse(name)), reverse("admin_dashboard"), fetch_redirect_response=False)

    def test_approve_requires_post(self):
        leave = make_leave(self.alice)
        self.login_admin()
        self.assertEqual(self.client.get(reverse("approve_leave", args=[leave.id])).status_code, 405)

    def test_csrf_enforced(self):
        from django.test import Client
        c = Client(enforce_csrf_checks=True)
        c.login(username="EMP1", password="Str0ng!Pass#99")
        self.assertEqual(c.post(reverse("edit_profile"), {"first_name": "x"}).status_code, 403)
        self.assertEqual(c.post(reverse("change_password"), {}).status_code, 403)
        self.assertEqual(c.post(reverse("apply_leave"), {}).status_code, 403)

    def test_missing_leave_id(self):
        self.login_admin()
        resp = self.client.post(reverse("approve_leave", args=[424242]), follow=True)
        self.assertContains(resp, "no longer exists")


class SmokeTests(BaseCase):
    """Every page renders for the right role and the navbar/messages work."""

    def test_all_pages_render(self):
        make_leave(self.alice, status="Approved", document=SimpleUploadedFile("a.pdf", PDF))
        self.login("EMP1")
        for name in ["dashboard", "apply_leave", "leave_history", "profile", "edit_profile",
                     "change_password", "notifications", "leave_calendar"]:
            self.assertEqual(self.client.get(reverse(name)).status_code, 200, name)
        self.login_admin()
        for name in ["admin_dashboard", "manage_leaves", "employees_list", "notifications", "leave_calendar"]:
            self.assertEqual(self.client.get(reverse(name)).status_code, 200, name)
        self.client.logout()
        for name in ["home", "login", "register"]:
            self.assertEqual(self.client.get(reverse(name)).status_code, 200, name)

    def test_register_login_logout_still_work(self):
        resp = self.client.post(reverse("register"), {
            "employee_id": "NEW1", "first_name": "N", "last_name": "E", "email": "n@e.com",
            "phone": "9876543210", "password": "Str0ng!Pass#99", "confirm_password": "Str0ng!Pass#99"}, follow=True)
        self.assertContains(resp, "registered successfully")
        resp = self.client.post(reverse("login"), {"username": "NEW1", "password": "Str0ng!Pass#99"}, follow=True)
        self.assertRedirects(resp, reverse("dashboard"))
        self.assertContains(resp, "Leave Balance")
        resp = self.client.get(reverse("logout"), follow=True)
        self.assertContains(resp, "logged out successfully")
        resp = self.client.post(reverse("login"), {"username": "NEW1", "password": "bad"})
        self.assertContains(resp, "Invalid Employee ID or password")
