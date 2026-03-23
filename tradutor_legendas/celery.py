import os

from celery import Celery


os.environ.setdefault("DJANGO_SETTINGS_MODULE", "tradutor_legendas.settings")

app = Celery("tradutor_legendas")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()

