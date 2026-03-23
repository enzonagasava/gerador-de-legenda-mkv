from __future__ import annotations

import os
from pathlib import Path

from rest_framework import serializers

from core.security import mkv_path_is_allowed

from .models import Job


class JobCreateSerializer(serializers.ModelSerializer):
    class Meta:
        model = Job
        fields = [
            "id",
            "status",
            "mkv_path",
            "track_number",
            "idioma_destino",
            "translation_backend",
            "result_files",
            "error_message",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "status",
            "result_files",
            "error_message",
            "created_at",
            "updated_at",
        ]

    def validate_mkv_path(self, value: str) -> str:
        p = Path(value).expanduser()

        if not p.is_file():
            raise serializers.ValidationError("`mkv_path` precisa apontar para um arquivo existente.")

        abs_path = str(p.resolve(strict=False))
        if not mkv_path_is_allowed(abs_path):
            raise serializers.ValidationError("`mkv_path` está fora das pastas permitidas (MKV_ALLOWED_ROOTS).")
        return abs_path

    def validate_track_number(self, value):
        if value is None:
            return value
        if value <= 0:
            raise serializers.ValidationError("`track_number` precisa ser >= 1.")
        return value

    def create(self, validated_data):
        request = self.context.get("request")
        if not request or not request.user:
            raise serializers.ValidationError("Usuário não autenticado.")
        return Job.objects.create(created_by=request.user, status=Job.Status.QUEUED, **validated_data)


class JobListSerializer(serializers.ModelSerializer):
    class Meta:
        model = Job
        fields = [
            "id",
            "status",
            "mkv_path",
            "track_number",
            "idioma_destino",
            "translation_backend",
            "result_files",
            "error_message",
            "created_at",
            "updated_at",
        ]


class JobDetailSerializer(serializers.ModelSerializer):
    logs = serializers.SerializerMethodField()

    class Meta:
        model = Job
        fields = [
            "id",
            "status",
            "mkv_path",
            "track_number",
            "idioma_destino",
            "translation_backend",
            "result_files",
            "error_message",
            "created_at",
            "updated_at",
            "logs",
        ]

    def get_logs(self, obj: Job):
        qs = obj.logs.order_by("created_at")
        return [{"level": l.level, "message": l.message, "created_at": l.created_at} for l in qs]

