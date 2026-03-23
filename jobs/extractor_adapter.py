from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any

from .models import Job, JobLog


class MKVExtractorAdapter:
    """
    Camada de adaptação para reutilizar `MKVExtractor` do extrair_legendas.py,
    mas persistindo status/log e coletando resultados para `Job.result_files`.
    """

    def __init__(self, job: Job):
        self.job = job

    def _log(self, message: str, level: str = "info") -> None:
        JobLog.objects.create(job=self.job, level=level, message=message)

    def _collect_results(self, mkv_path: str, start_ts: float) -> dict[str, Any]:
        mkv = Path(mkv_path)
        base_stem = mkv.stem
        directory = mkv.parent
        tol = 2.0

        result_files: dict[str, list[str]] = {
            "srt": [],
            "ass": [],
            "sup": [],
            "pt_srt": [],
            "pt_ass": [],
            "ocr_merged_srt": [],
        }

        try:
            for p in directory.iterdir():
                if not p.is_file():
                    continue
                if not p.name.startswith(base_stem):
                    continue
                try:
                    if p.stat().st_mtime < (start_ts - tol):
                        continue
                except Exception:
                    continue

                suffix = p.suffix.lower()
                name = p.name

                if suffix == ".sup":
                    result_files["sup"].append(str(p))
                elif suffix == ".srt":
                    result_files["srt"].append(str(p))
                    if name.endswith("_PT.srt"):
                        result_files["pt_srt"].append(str(p))
                    if "_OCR_MERGED.srt" in name:
                        result_files["ocr_merged_srt"].append(str(p))
                elif suffix in (".ass", ".ssa"):
                    result_files["ass"].append(str(p))
                    if name.endswith("_PT.ass"):
                        result_files["pt_ass"].append(str(p))
        except Exception:
            # Falhas aqui não devem apagar o job; ainda retornamos o que coletamos.
            pass

        # Remove listas vazias para reduzir ruído.
        return {k: v for k, v in result_files.items() if v}

    def run(self) -> dict[str, Any]:
        """
        Executa o processador e retorna o dicionário de arquivos resultantes.
        Lança exceção se falhar.
        """
        # Import atrasado para não instanciar nada no import do módulo.
        from extrair_legendas import MKVExtractor

        mkv_path = self.job.mkv_path
        mkv = Path(mkv_path)

        if not mkv.is_file():
            raise FileNotFoundError(f"MKV não encontrado: {mkv_path}")

        directory = mkv.parent
        self._log(f"Iniciando processamento do MKV: {mkv_path}", level="info")

        start_ts = time.time()

        # Override por job (idioma/back-end) via env para o extrair_legendas.py.
        old_idioma = os.environ.get("IDIOMA_DESTINO")
        old_backend = os.environ.get("TRADUCAO_BACKEND")
        os.environ["IDIOMA_DESTINO"] = str(self.job.idioma_destino)
        os.environ["TRADUCAO_BACKEND"] = str(self.job.translation_backend)

        try:
            extractor = MKVExtractor()
            ok = extractor.processar_mkv(
                mkv_path,
                numero_faixa=self.job.track_number,
                interativo=False,
            )
        finally:
            # Restaura env anterior para reduzir efeitos colaterais neste worker.
            if old_idioma is None:
                os.environ.pop("IDIOMA_DESTINO", None)
            else:
                os.environ["IDIOMA_DESTINO"] = old_idioma

            if old_backend is None:
                os.environ.pop("TRADUCAO_BACKEND", None)
            else:
                os.environ["TRADUCAO_BACKEND"] = old_backend

        if not ok:
            raise RuntimeError("Falha no processamento/extração do MKV.")

        # Coleta com base em start_ts (tolerante) e padrão de nomes.
        result_files = self._collect_results(mkv_path, start_ts=start_ts)
        self._log(f"Processamento concluido. Arquivos PT detectados: {len(result_files)} categorias", level="info")
        return result_files

