from __future__ import annotations

from django.conf import settings
from django.db import models
from uuid import uuid4


class Job(models.Model):
    class Status(models.TextChoices):
        QUEUED = "queued"
        RUNNING = "running"
        SUCCEEDED = "succeeded"
        FAILED = "failed"

    class TranslationBackend(models.TextChoices):
        LIBRETRANSLATE = "libretranslate"
        NONE = "none"

    id = models.UUIDField(primary_key=True, editable=False, default=uuid4)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="jobs")
    status = models.CharField(max_length=32, choices=Status.choices, default=Status.QUEUED)

    mkv_path = models.TextField()
    track_number = models.PositiveIntegerField(null=True, blank=True)

    idioma_destino = models.CharField(max_length=16, default="pt")
    translation_backend = models.CharField(
        max_length=32,
        choices=TranslationBackend.choices,
        default=TranslationBackend.LIBRETRANSLATE,
    )

    # Paths de arquivos gerados (ex.: extraídos, PTs, OCR merged etc.)
    # Ex.: {"srt":[...], "ass":[...], "pt_srt":[...], "pt_ass":[...], "ocr":[...]}
    result_files = models.JSONField(default=dict, blank=True)

    error_message = models.TextField(blank=True, default="")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        return f"Job({self.id}) {self.status}"


class JobLog(models.Model):
    class Level(models.TextChoices):
        INFO = "info"
        ERROR = "error"

    job = models.ForeignKey(Job, on_delete=models.CASCADE, related_name="logs")
    level = models.CharField(max_length=8, choices=Level.choices, default=Level.INFO)
    message = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self) -> str:
        return f"JobLog({self.job_id}) {self.level}"

