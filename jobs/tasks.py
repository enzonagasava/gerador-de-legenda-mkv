from __future__ import annotations

import traceback

from celery import shared_task
from django.db import transaction
from django.utils import timezone

from .extractor_adapter import MKVExtractorAdapter
from .models import Job, JobLog


@shared_task(bind=True, name="jobs.run_job")
def run_job(self, job_id: str) -> dict:
    """
    Executa um job de extração/tradução em background.
    """
    job = None
    try:
        job = Job.objects.get(pk=job_id)
    except Job.DoesNotExist:
        return {"job_id": str(job_id), "status": "failed", "error_message": "Job não encontrado."}

    try:
        with transaction.atomic():
            job.status = Job.Status.RUNNING
            job.error_message = ""
            job.save(update_fields=["status", "error_message", "updated_at"])
            JobLog.objects.create(
                job=job,
                level=JobLog.Level.INFO,
                message=f"Job iniciado ({timezone.now().isoformat()}).",
            )

        adapter = MKVExtractorAdapter(job)
        result_files = adapter.run()

        with transaction.atomic():
            job.refresh_from_db()
            job.status = Job.Status.SUCCEEDED
            job.result_files = result_files or {}
            job.error_message = ""
            job.save(update_fields=["status", "result_files", "error_message", "updated_at"])
            JobLog.objects.create(job=job, level=JobLog.Level.INFO, message="Job finalizado com sucesso.")

        return {"job_id": str(job.id), "status": job.status, "result_files": job.result_files}

    except Exception as exc:
        err = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        if job is not None:
            JobLog.objects.create(
                job=job,
                level=JobLog.Level.ERROR,
                message=f"Erro no job: {exc.__class__.__name__}: {exc}\n{err[:4000]}",
            )
            with transaction.atomic():
                job.status = Job.Status.FAILED
                job.error_message = str(exc)[:2000]
                job.save(update_fields=["status", "error_message", "updated_at"])

            return {"job_id": str(job.id), "status": job.status, "error_message": job.error_message}

        return {"job_id": str(job_id), "status": "failed", "error_message": str(exc)[:2000]}

