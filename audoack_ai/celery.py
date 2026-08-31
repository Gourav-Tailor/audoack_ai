import os

from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "audoack_ai.settings")

app = Celery("audoack_ai")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()
