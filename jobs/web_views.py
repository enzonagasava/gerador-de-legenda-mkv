from __future__ import annotations

import json

from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render

from jobs.forms import JobCreateForm
from jobs.models import Job
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
        form = JobCreateForm(request.POST)
        if form.is_valid():
            data = form.cleaned_data
            job = Job.objects.create(
                created_by=request.user,
                status=Job.Status.QUEUED,
                mkv_path=data["mkv_path"],
                track_number=data.get("track_number"),
                idioma_destino=data.get("idioma_destino") or "pt",
                translation_backend=data.get("translation_backend") or Job.TranslationBackend.LIBRETRANSLATE,
            )
            # Dispara background.
            run_job.delay(str(job.id))
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

