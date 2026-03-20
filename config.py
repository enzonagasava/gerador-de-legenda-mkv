"""
Configuração do extrator e tradutor de legendas MKV.
"""

# Pastas para varrer em modo lote e watcher (busca recursiva)
PASTAS = [
    "/media/enzo/backup-sdb2/torrents",
    "/media/enzo/backup-sdb2/series",
]

# Idioma de destino para tradução (pt, en, es, etc.)
IDIOMA_DESTINO = "pt"

# Segundos para aguardar arquivo estável no watcher (evitar processar durante download)
WATCHER_ESTABILIDADE_SEGUNDOS = 5

# --- Tradução em Python (após extrair a legenda) ---
# "google_v1" = Google Translate sem API key (como SubtitleEdit V1; requer deep-translator)
# "google" = Google Translate API v2 (GOOGLE_TRANSLATE_API_KEY no ambiente)
# "libretranslate" = API gratuita (sem chave) | "none" = não traduzir
TRADUCAO_BACKEND = "google_v1"

# Chave para Google Translate API v2 (recomendado: definir no ambiente GOOGLE_TRANSLATE_API_KEY)

# URL da API LibreTranslate (pública)
LIBRETRANSLATE_URL = "https://libretranslate.de/translate"

# --- OCR via SubtitleEdit (para PGS/SUP) ---
# O SubtitleEdit (GUI) faz OCR por ferramentas como nOCR/Tesseract/etc.
# Para automatizar via watcher no Linux, este script tenta chamar o SubtitleEdit
# via command line conversion (/convert) usando Mono.
#
# 1) Informe o caminho do SubtitleEdit.exe (ex.: "/home/enzo/SubtitleEdit/SubtitleEdit.exe")
# 2) O script tentará converter o .sup para .srt (subrip) e depois traduzir.
SUBTITLEEDIT_EXE_PATH = "/home/enzo/Documentos/programas/SubtitleEdit-Linux-x64/SubtitleEdit"
SUBTITLEEDIT_MONO_CMD = "mono"

# Formato de saída do SubtitleEdit (ver help da opção /convert)
# Exemplo usado pela documentação: "subrip" -> srt
SUBTITLEEDIT_OCR_OUTPUT_FORMAT = "subrip"

# --- OCR via subtitleedit-cli (seconv) ---
# Use este caminho quando você quer OCR automatizado (PGS/SUP -> SRT) via CLI.
# Segundo a documentação, rodar OCR em BDSup/SUP é suportado no subtitleedit-cli.
#
# Opções:
# - SECONV_MODE = "docker" (rodar seconv dentro de Docker) ou "local" (rodar seconv local)
SECONV_MODE = "docker"

# Imagem Docker que contém o binário `seconv`.
# Ex.: o README do projeto sugere construir uma imagem local como `seconv:1.0`.
# Se o OCR ainda ficar incompleto, tente reconstruir a imagem com uma versão mais
# nova do `subtitleedit-cli` e/ou troque `SECONV_DOCKER_IMAGE`.
SECONV_DOCKER_IMAGE = "seconv:1.0"

# Opções extras para `docker run` (string). Ex.: `--network=host`.
SECONV_DOCKER_RUN_OPTS = ""

# Formato de saída do OCR.
SECONV_OCR_OUTPUT_FORMAT = "subrip"

# Banco OCR usado pelo seconv para BDSup/PGS (ex.: "Latin" ou "Japan")
SECONV_OCR_DB = "Latin"

# Ao invés de traduzir apenas o "melhor" SRT, o script consolida TODOS os SRT
# OCRados gerados na conversão da mesma `.sup` em um único arquivo:
# - merge por timestamp (ordenando por tempo)
# - deduplicação por (timestamp, texto) para evitar repetições
SECONV_MERGE_SRTS = True

# Aplica ações internas do seconv para reduzir fragmentação do OCR.
# (útil quando o seconv gera múltiplos segmentos para o mesmo texto)
SECONV_APPLY_MERGE_ACTIONS = True

# Apaga automaticamente o arquivo .sup extraído após o OCR gerar os .srt.
# Isso reduz consumo de espaço, já que .sup pode ser muito grande.
APAGAR_SUP_APOS_OCR = True
