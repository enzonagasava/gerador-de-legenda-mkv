from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

from django.contrib.auth.decorators import login_required
from django.conf import settings
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from kombu.exceptions import OperationalError

from jobs.forms import JobCreateForm
from jobs.models import Job, JobLog
from jobs.tasks import run_job


def _can_view_job(request, job: Job) -> bool:
    if request.user.is_staff:
        return True
    return job.created_by_id == request.user.id


@login_required(login_url="/web/login/")
def job_list(request: HttpRequest) -> HttpResponse:
    qs = Job.objects.all().order_by("-created_at")
    if not request.user.is_staff:
        qs = qs.filter(created_by=request.user)
    jobs = qs[:200]
    return render(request, "jobs/job_list.html", {"jobs": jobs})


@login_required(login_url="/web/login/")
def job_create(request: HttpRequest) -> HttpResponse:
    if request.method == "POST":
        form = JobCreateForm(request.POST, request.FILES)
        if form.is_valid():
            data = form.cleaned_data
            uploaded = data["mkv_file"]
            upload_root = Path(getattr(settings, "MKV_UPLOAD_DIR", settings.BASE_DIR / "uploaded_mkvs"))
            upload_root.mkdir(parents=True, exist_ok=True)
            target_path = upload_root / f"{uuid4().hex}.mkv"
            with target_path.open("wb+") as destination:
                for chunk in uploaded.chunks():
                    destination.write(chunk)

            job = Job.objects.create(
                created_by=request.user,
                status=Job.Status.QUEUED,
                mkv_path=str(target_path.resolve(strict=False)),
                track_number=data.get("track_number"),
                idioma_destino=data.get("idioma_destino") or "pt",
                translation_backend=data.get("translation_backend") or Job.TranslationBackend.LIBRETRANSLATE,
            )
            try:
                run_job.delay(str(job.id))
            except OperationalError:
                job.status = Job.Status.FAILED
                job.error_message = "Fila Celery/Redis indisponivel. Inicie o Redis e tente novamente."
                job.save(update_fields=["status", "error_message", "updated_at"])
                JobLog.objects.create(
                    job=job,
                    level=JobLog.Level.ERROR,
                    message="Falha ao enfileirar no Celery (broker indisponivel).",
                )
                return redirect("web-job-detail", job_id=job.id)
            return redirect("web-job-detail", job_id=job.id)
    else:
        form = JobCreateForm()
    return render(request, "jobs/job_create.html", {"form": form})


@login_required(login_url="/web/login/")
def job_detail(request: HttpRequest, job_id) -> HttpResponse:
    job = get_object_or_404(Job, pk=job_id)
    if not _can_view_job(request, job):
        raise Http404()

    logs = job.logs.all().order_by("created_at")
    result_files_pretty = json.dumps(job.result_files or {}, ensure_ascii=False, indent=2)

    return render(
        request,
        "jobs/job_detail.html",
        {
            "job": job,
            "logs": logs,
            "result_files_pretty": result_files_pretty,
        },
    )
