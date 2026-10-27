"""Pengganti `{% get_solo 'mainapps.TemplateConfig' %}` dari backend-pusat.

Di backend-pusat nama situs & favicon disimpan di model singleton (django-solo). Di sini cukup dari settings:
    MATERIALIZE_ADMIN = {"site_name": "Multi Outlet", "logo": "images/logo.png"}
"""
from types import SimpleNamespace

from django import template
from django.conf import settings
from django.templatetags.static import static

register = template.Library()


@register.simple_tag
def get_site_config():
    cfg = getattr(settings, "MATERIALIZE_ADMIN", {}) or {}
    logo = cfg.get("logo") or "images/logo.png"
    return SimpleNamespace(
        site_name=cfg.get("site_name", "Admin"),
        logo_url=static(logo),
        favicon=None,          # template lama: site_config.favicon.url
        vapidKey="",           # notifikasi Firebase backend-pusat: tidak dipakai
        firebase_json={},
    )
