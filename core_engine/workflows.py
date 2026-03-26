from __future__ import annotations

import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from extrair_legendas import MKVExtractor


@dataclass(slots=True)
class ProcessingOptions:
    mkv_path: str
    track_number: int | None = None
    idioma_destino: str = "pt"
    translation_backend: str = "libretranslate"
    interativo: bool = False


@dataclass(slots=True)
class ProcessingResult:
    ok: bool
    result_files: dict[str, list[str]]
    error_message: str = ""


class SubtitleProcessor:
    """
    Serviço reutilizável para processamento de MKV.
    Desacopla a execução da interface (CLI, Django ou GUI).
    """

    def run(self, options: ProcessingOptions) -> ProcessingResult:
        mkv_path = str(Path(options.mkv_path).expanduser().resolve(strict=False))
        if not Path(mkv_path).is_file():
            return ProcessingResult(ok=False, result_files={}, error_message=f"Arquivo não encontrado: {mkv_path}")

        start_ts = time.time()

        old_idioma = os.environ.get("IDIOMA_DESTINO")
        old_backend = os.environ.get("TRADUCAO_BACKEND")
        os.environ["IDIOMA_DESTINO"] = str(options.idioma_destino)
        os.environ["TRADUCAO_BACKEND"] = str(options.translation_backend)

        try:
            extractor = MKVExtractor()
            ok = extractor.processar_mkv(
                mkv_path,
                numero_faixa=options.track_number,
                interativo=options.interativo,
            )
        except Exception as exc:
            return ProcessingResult(ok=False, result_files={}, error_message=str(exc))
        finally:
            if old_idioma is None:
                os.environ.pop("IDIOMA_DESTINO", None)
            else:
                os.environ["IDIOMA_DESTINO"] = old_idioma

            if old_backend is None:
                os.environ.pop("TRADUCAO_BACKEND", None)
            else:
                os.environ["TRADUCAO_BACKEND"] = old_backend

        if not ok:
            return ProcessingResult(ok=False, result_files={}, error_message="Falha no processamento/extração do MKV.")

        return ProcessingResult(ok=True, result_files=self._collect_results(mkv_path, start_ts))

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
                if not p.is_file() or not p.name.startswith(base_stem):
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
            pass

        return {k: v for k, v in result_files.items() if v}
