from django.urls import path
from rest_framework.authtoken.views import obtain_auth_token
from rest_framework.routers import DefaultRouter

from . import views

router = DefaultRouter()
router.register("outlets", views.OutletViewSet, basename="outlet")
router.register("conversations", views.ConversationViewSet, basename="conversation")
router.register("contacts", views.ContactViewSet, basename="contact")
router.register("messages", views.MessageViewSet, basename="message")
router.register("templates", views.TemplateViewSet, basename="template")

urlpatterns = [
    path("auth/login/", obtain_auth_token),
    path("auth/logout/", views.logout),
    path("me/", views.me),
    path("me/password/", views.change_password),
    path("stats/", views.stats),
    *router.urls,
]
