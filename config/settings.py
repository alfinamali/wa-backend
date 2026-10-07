import os
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
try:
    from dotenv import load_dotenv
    load_dotenv(BASE_DIR / ".env")
except ImportError:
    pass

def csv(name, default=""):
    return [x.strip() for x in os.environ.get(name, default).split(",") if x.strip()]

DEBUG = os.environ.get("DEBUG", "0") == "1"
SECRET_KEY = os.environ.get("SECRET_KEY", "")
if not SECRET_KEY:
    if DEBUG or "test" in sys.argv:
        SECRET_KEY = "dev-only-insecure-key"
    else:
        raise RuntimeError("SECRET_KEY wajib diisi (atau set DEBUG=1 untuk pengembangan).")

ALLOWED_HOSTS = [
    "localhost",
    "127.0.0.1",
    "34da-110-139-53-44.ngrok-free.app",
    "wa-backend-production-96f0.up.railway.app",
]
CSRF_TRUSTED_ORIGINS = [
    "http://localhost:3000",
    "http://localhost:8080",
    "https://34da-110-139-53-44.ngrok-free.app",
    "https://wa-backend-production-96f0.up.railway.app",
]
CORS_ALLOWED_ORIGINS = [
    "http://localhost:3000",
    "http://localhost:8080",
    "https://34da-110-139-53-44.ngrok-free.app",
    "https://wa-backend-production-96f0.up.railway.app",
    "https://dashboard-multi-whatsapp-production.up.railway.app",
]
if os.environ.get("BEHIND_PROXY") == "1":
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

INSTALLED_APPS = [
    "django.contrib.admin", "django.contrib.auth", "django.contrib.contenttypes",
    "django.contrib.sessions", "django.contrib.messages", "django.contrib.staticfiles",
    "rest_framework", "rest_framework.authtoken", "corsheaders", "inbox",
]
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]
ROOT_URLCONF = "config.urls"
TEMPLATES = [{
    "BACKEND": "django.template.backends.django.DjangoTemplates", "DIRS": [], "APP_DIRS": True,
    "OPTIONS": {"context_processors": [
        "django.template.context_processors.request", "django.contrib.auth.context_processors.auth",
        "django.contrib.messages.context_processors.messages"]},
}]
WSGI_APPLICATION = "config.wsgi.application"

if os.environ.get("DB_HOST"):
    DATABASES = {"default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.environ.get("DB_NAME", "wa"), "USER": os.environ.get("DB_USER", "wa"),
        "PASSWORD": os.environ.get("DB_PASSWORD", ""), "HOST": os.environ["DB_HOST"],
        "PORT": os.environ.get("DB_PORT", "5432"),
    }}
else:  # pengembangan lokal tanpa Postgres
    DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": BASE_DIR / "db.sqlite3"}}

LANGUAGE_CODE = "id"
TIME_ZONE = "Asia/Jakarta"
USE_I18N = True
USE_TZ = True
STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": ["rest_framework.authentication.TokenAuthentication"],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticated"],
}

# WhatsApp Cloud API (Meta)
WA_TOKEN = os.environ.get("WA_TOKEN", "")                  # token System User
WA_APP_SECRET = os.environ.get("WA_APP_SECRET", "")        # App Settings > Basic
WA_VERIFY_TOKEN = os.environ.get("WA_VERIFY_TOKEN", "")    # string bebas buatan Anda
WA_WABA_ID = os.environ.get("WA_WABA_ID", "")              # untuk sinkron template
WA_GRAPH_VERSION = os.environ.get("WA_GRAPH_VERSION", "v23.0")  # cek versi terbaru di dokumentasi Meta
