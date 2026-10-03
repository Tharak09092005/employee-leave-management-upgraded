from django.conf import settings
from django.contrib import admin
from django.urls import include, path, re_path
from django.views.static import serve

urlpatterns = [
    path("admin/", admin.site.urls),
    path("", include("employees.urls")),
]

if settings.DEBUG:
    # Development only: serve profile photos from MEDIA_ROOT.
    # Leave documents are deliberately NOT served here - they are private and
    # are only available through the permission-checked "leave_document" view.
    urlpatterns += [
        re_path(
            rf"^{settings.MEDIA_URL.lstrip('/')}(?P<path>profile_photos/.*)$",
            serve,
            {"document_root": settings.MEDIA_ROOT},
        ),
    ]
