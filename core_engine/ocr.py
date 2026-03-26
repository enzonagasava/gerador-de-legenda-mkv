from __future__ import annotations

import os


def ocr_habilitado() -> bool:
    image = os.environ.get("SECONV_DOCKER_IMAGE", "").strip()
    return bool(image)
