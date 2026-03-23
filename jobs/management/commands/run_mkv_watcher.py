from __future__ import annotations

import os
import threading
import time
from queue import Empty, Queue
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand

from jobs.models import Job
from core.security import mkv_path_is_allowed
from jobs.tasks import run_job


class Command(BaseCommand):
    help = "Monitora pastas e enfileira jobs Celery para MKVs (.mkv)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--paths",
            nargs="+",
            default=None,
            help="Pastas para monitorar (recursivo). Se omitido, usa MKV_ALLOWED_ROOTS.",
        )
        parser.add_argument(
            "--idioma-destino",
            default=os.environ.get("WATCHER_IDIOMA_DESTINO", "pt"),
            help="Idioma destino (IDIOMA_DESTINO) para tradução (ex.: pt).",
        )
        parser.add_argument(
            "--translation-backend",
            default=os.environ.get("WATCHER_TRANSLATION_BACKEND", Job.TranslationBackend.LIBRETRANSLATE),
            help="Backend de tradução (TRADUCAO_BACKEND).",
        )
        parser.add_argument(
            "--track-number",
            type=int,
            default=None,
            help="Se informado, extrai apenas esta faixa (track_number).",
        )
        parser.add_argument(
            "--stability-seconds",
            type=float,
            default=float(os.environ.get("WATCHER_ESTABILIDADE_SEGUNDOS", "5")),
            help="Tempo de espera antes de criar o job (evita arquivo em download).",
        )
        parser.add_argument(
            "--watcher-username",
            default=os.environ.get("WATCHER_USERNAME", "watcher"),
            help="Usuário do Django usado para atribuir created_by aos jobs gerados pelo watcher.",
        )

    def handle(self, *args, **options):
        try:
            from watchdog.events import FileSystemEventHandler
            from watchdog.observers import Observer
        except ImportError:
            raise SystemExit("watchdog não instalado. Rode: pip install -r requirements.txt")

        watch_paths: list[str] = options["paths"] or getattr(settings, "MKV_ALLOWED_ROOTS", None) or []
        watch_paths = [str(p) for p in watch_paths if p]

        if not watch_paths:
            raise SystemExit("Nenhuma pasta para monitorar. Defina MKV_ALLOWED_ROOTS ou passe --paths.")

        idioma_destino = str(options["idioma_destino"])
        translation_backend = str(options["translation_backend"]).strip()
        allowed_backends = {c.value for c in Job.TranslationBackend}
        if translation_backend not in allowed_backends:
            translation_backend = Job.TranslationBackend.LIBRETRANSLATE.value
        track_number = options["track_number"]
        stability_seconds = float(options["stability_seconds"])
        watcher_username = str(options["watcher_username"])

        # Garante um usuário determinístico para created_by.
        User = get_user_model()
        watcher_user, _ = User.objects.get_or_create(
            username=watcher_username,
            defaults={"is_staff": False, "is_superuser": False},
        )
        try:
            watcher_user.set_unusable_password()
            watcher_user.save(update_fields=["password"])
        except Exception:
            pass

        q: Queue[str] = Queue()
        enfileirado = set()
        enfileirado_lock = threading.Lock()
        processando = threading.Lock()

        def enqueue_if_needed(mkv_path: str) -> None:
            abs_path = str(Path(mkv_path).resolve(strict=False))
            if not abs_path.lower().endswith(".mkv"):
                return
            if not mkv_path_is_allowed(abs_path):
                return
            with enfileirado_lock:
                if abs_path in enfileirado:
                    return
                enfileirado.add(abs_path)
            q.put(abs_path)

        class Handler(FileSystemEventHandler):
            def on_created(self, event):
                if event.is_directory:
                    return
                enqueue_if_needed(event.src_path)

            def on_moved(self, event):
                if event.is_directory:
                    return
                enqueue_if_needed(event.dest_path)

        def worker() -> None:
            while True:
                try:
                    mkv_path = q.get(timeout=1.0)
                except Empty:
                    continue

                with processando:
                    try:
                        # Evita processar enquanto o arquivo ainda está copiando.
                        time.sleep(stability_seconds)
                        if not Path(mkv_path).is_file():
                            continue
                        # Evita duplicidade no banco por path.
                        exists = Job.objects.filter(mkv_path=mkv_path).exists()
                        if exists:
                            continue

                        job = Job.objects.create(
                            created_by=watcher_user,
                            status=Job.Status.QUEUED,
                            mkv_path=mkv_path,
                            track_number=track_number,
                            idioma_destino=idioma_destino,
                            translation_backend=translation_backend,
                        )
                        self.stdout.write(self.style.SUCCESS(f"Job criado/enfileirado: {job.id} ({Path(mkv_path).name})"))
                        run_job.delay(str(job.id))
                    except Exception as exc:
                        self.stdout.write(self.style.ERROR(f"Erro no watcher ao enfileirar {mkv_path}: {exc}"))

        t = threading.Thread(target=worker, daemon=True)
        t.start()

        observer = Observer()
        for p in watch_paths:
            path_obj = Path(p)
            if path_obj.is_dir():
                observer.schedule(Handler(), str(path_obj), recursive=True)
                self.stdout.write(f"Monitorando: {path_obj}")
            else:
                self.stdout.write(self.style.WARNING(f"Aviso: pasta não encontrada: {path_obj}"))

        observer.start()
        self.stdout.write("Watcher ativo. Ctrl+C para sair.")

        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            observer.stop()
        observer.join()

