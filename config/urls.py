from django.contrib import admin
from django.http import JsonResponse
from django.urls import include, path
from inbox.views import webhook

urlpatterns = [
    path("admin/", admin.site.urls),
    path("webhook/", webhook),
    path("webhook", webhook),  # tanpa slash agar POST dari Meta tidak di-redirect
    path("health/", lambda r: JsonResponse({"ok": True})),
    path("api/", include("inbox.urls")),
]
