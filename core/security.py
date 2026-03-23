from __future__ import annotations

from pathlib import Path

from django.conf import settings


def mkv_path_is_allowed(mkv_path: str) -> bool:
    """
    Garante que o `mkv_path` está dentro de `MKV_ALLOWED_ROOTS`.
    Evita path traversal / leitura arbitrária do filesystem.
    """
    allowed_roots: list[str] = getattr(settings, "MKV_ALLOWED_ROOTS", []) or []
    if not allowed_roots:
        return False

    try:
        p = Path(mkv_path).expanduser().resolve(strict=False)
    except Exception:
        return False

    for root in allowed_roots:
        try:
            r = Path(root).expanduser().resolve(strict=False)
        except Exception:
            continue
        # "starts with" em path resolved
        try:
            if str(p).startswith(str(r) + str(Path("/"))):
                return True
            if str(p) == str(r):
                return True
        except Exception:
            continue
    return False

