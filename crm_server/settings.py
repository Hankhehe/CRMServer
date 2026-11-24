"""
Django settings for CRMServer project.
"""

from pathlib import Path
import os
import json

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "django-insecure-change-me-in-production")

DEBUG = True

ALLOWED_HOSTS = ["*"]

# 支援外部域名與 IP 存取 CSRF 白名單
CSRF_TRUSTED_ORIGINS = [
    "http://127.0.0.1:8000",
    "http://localhost:8000",
]
_env_csrf = os.environ.get("CSRF_TRUSTED_ORIGINS")
if _env_csrf:
    CSRF_TRUSTED_ORIGINS.extend([origin.strip() for origin in _env_csrf.split(",") if origin.strip()])

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "apps.core",
    "apps.customers",
    "apps.hardware",
    "apps.licenses",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "apps.core.auto_migrate.AutoMigrateMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "apps.core.middleware.SessionIdleTimeoutMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "crm_server.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "apps.core.context_processors.permission_context",
            ],
        },
    },
]

WSGI_APPLICATION = "crm_server.wsgi.application"

# Database Configuration (Supporting SQLite and MSSQL)
DB_CONFIG_PATH = BASE_DIR / "db_config.json"
db_config = {}
if DB_CONFIG_PATH.exists():
    try:
        with open(DB_CONFIG_PATH, "r", encoding="utf-8") as f:
            db_config = json.load(f)
    except Exception:
        pass

current_db = db_config.get("current_db", "sqlite")

DATABASES = {
    "sqlite": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
    }
}

if current_db == "postgres" and "postgres" in db_config:
    cfg = db_config["postgres"]
    DATABASES["default"] = {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": cfg.get("name", "CRMServerDB"),
        "USER": cfg.get("user", "postgres"),
        "PASSWORD": cfg.get("password", ""),
        "HOST": cfg.get("host", "127.0.0.1"),
        "PORT": cfg.get("port", "5432"),
    }
    DATABASES["sqlite"] = {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
    }
elif current_db == "mssql" and "mssql" in db_config:
    cfg = db_config["mssql"]
    DATABASES["default"] = {
        "ENGINE": "mssql",
        "NAME": cfg.get("name", "CRMServerDB"),
        "USER": cfg.get("user", ""),
        "PASSWORD": cfg.get("password", ""),
        "HOST": cfg.get("host", ""),
        "PORT": cfg.get("port", "1433"),
        "OPTIONS": {
            "driver": cfg.get("driver", "ODBC Driver 18 for SQL Server"),
            "extra_params": "TrustServerCertificate=yes",
        },
    }
    DATABASES["sqlite"] = {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
    }
else:
    DATABASES["default"] = {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
    }
    if "postgres" in db_config:
        cfg = db_config["postgres"]
        DATABASES["postgres"] = {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": cfg.get("name", "CRMServerDB"),
            "USER": cfg.get("user", "postgres"),
            "PASSWORD": cfg.get("password", ""),
            "HOST": cfg.get("host", "127.0.0.1"),
            "PORT": cfg.get("port", "5432"),
        }
    if "mssql" in db_config:
        cfg = db_config["mssql"]
        DATABASES["mssql"] = {
            "ENGINE": "mssql",
            "NAME": cfg.get("name", "CRMServerDB"),
            "USER": cfg.get("user", ""),
            "PASSWORD": cfg.get("password", ""),
            "HOST": cfg.get("host", ""),
            "PORT": cfg.get("port", "1433"),
            "OPTIONS": {
                "driver": cfg.get("driver", "ODBC Driver 18 for SQL Server"),
                "extra_params": "TrustServerCertificate=yes",
            },
        }

AUTHENTICATION_BACKENDS = [
    "apps.core.backends.ActiveDirectoryBackend",
    "apps.core.backends.LocalModelBackend",
]

AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.CommonPasswordValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.NumericPasswordValidator",
    },
]

LANGUAGE_CODE = "zh-hant"
TIME_ZONE = "Asia/Taipei"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATICFILES_DIRS = [BASE_DIR / "static"]

MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

LOGIN_URL = "/login/"
LOGIN_REDIRECT_URL = "/"

# Fallback LDAP settings (prefer configuring via SystemSetting in DB or environment variables)
LDAP_SERVER_URI = os.environ.get("LDAP_SERVER_URI", "")
LDAP_BIND_DN = os.environ.get("LDAP_BIND_DN", "")
LDAP_BIND_PASSWORD = os.environ.get("LDAP_BIND_PASSWORD", "")
LDAP_BASE_DN = os.environ.get("LDAP_BASE_DN", "")
LDAP_DOMAIN = os.environ.get("LDAP_DOMAIN", "")
