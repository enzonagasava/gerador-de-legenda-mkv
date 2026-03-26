from __future__ import annotations

from extrair_legendas import MKVExtractor


def listar_conteudo_mkv(mkv_path: str) -> dict:
    return MKVExtractor().listar_conteudo_mkv(mkv_path)


def listar_faixas_legenda(mkv_path: str) -> list[dict]:
    return MKVExtractor().listar_faixas(mkv_path)
