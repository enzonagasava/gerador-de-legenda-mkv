from __future__ import annotations

from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView


class JobCreateView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        from .serializers import JobCreateSerializer

        serializer = JobCreateSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        job = serializer.save()
        return Response(JobCreateSerializer(job, context={"request": request}).data, status=201)


class JobListCreateView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        from .serializers import JobListSerializer
        from .models import Job

        qs = Job.objects.all().order_by("-created_at")
        serializer = JobListSerializer(qs, many=True)
        return Response(serializer.data)

    def post(self, request):
        from .serializers import JobCreateSerializer

        serializer = JobCreateSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        job = serializer.save()
        return Response(JobCreateSerializer(job, context={"request": request}).data, status=201)


class JobListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        from .serializers import JobListSerializer
        from .models import Job

        qs = Job.objects.all().order_by("-created_at")
        serializer = JobListSerializer(qs, many=True)
        return Response(serializer.data)


class JobDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, job_id):
        from .models import Job
        from .serializers import JobDetailSerializer

        job = Job.objects.get(pk=job_id)
        return Response(JobDetailSerializer(job).data)

