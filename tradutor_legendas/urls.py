from django.contrib import admin
from django.urls import include, path

from jobs import web_views
from django.contrib.auth import views as auth_views


urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/", include("jobs.urls")),
    path("web/login/", auth_views.LoginView.as_view(template_name="registration/login.html"), name="web-login"),
    path("web/logout/", auth_views.LogoutView.as_view(), name="web-logout"),
    path("web/jobs/", web_views.job_list, name="web-job-list"),
    path("web/jobs/create/", web_views.job_create, name="web-job-create"),
    path("web/jobs/<uuid:job_id>/", web_views.job_detail, name="web-job-detail"),
]

