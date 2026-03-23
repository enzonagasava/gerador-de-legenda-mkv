from django.urls import path

from .views import JobDetailView, JobListCreateView

urlpatterns = [
    path("jobs/", JobListCreateView.as_view(), name="job-list-create"),
    path("jobs/<uuid:job_id>/", JobDetailView.as_view(), name="job-detail"),
]

