# Extrator e tradutor de legendas MKV

Extrai faixas de legenda de arquivos MKV para SRT, ASS ou SUP. A **tradução** pode ser feita **no próprio script** (Google Translate API ou LibreTranslate) ou manualmente no SubtitleEdit. **OCR** de legendas em imagem (PGS/SUP) continua no SubtitleEdit.

## Fluxo

1. **Este script** — extrai **todas as faixas de legenda** do MKV para arquivos (`.srt`, `.ass` ou `.sup`). Se a legenda for **texto** (SRT ou ASS/SSA) e a tradução estiver ativa em `config.py`, o script **traduz em Python** e gera `*_PT.srt` ou `*_PT.ass` por faixa (ASS/SSA requer `pysubs2`; veja `requirements.txt`).
2. **SubtitleEdit** (opcional) — para legendas em **imagem** (PGS/SUP): abra o MKV ou o `.sup` e use **Ferramentas > OCR**. Para traduzir manualmente em vez do script: **Ferramentas > Auto-traduzir** (Google Translate).

## Pré-requisitos

- **MKVToolNix** — para extração das faixas.
  - Linux (Ubuntu/Debian): `sudo apt install mkvtoolnix`
  - macOS: `brew install mkvtoolnix`
  - Windows: [mkvtoolnix.download](https://mkvtoolnix.download/) e adicionar ao PATH.

- **SubtitleEdit** — para OCR e tradução (instale separadamente).

- **Python 3** — para o script de extração. Instale dependências: `pip install -r requirements.txt` (inclui `watchdog` para o watcher e `pysubs2` para traduzir ASS/SSA).

## Configuração

Edite `config.py`:

- **PASTAS** — pastas onde estão os MKVs (modo lote e watcher). Padrão: `/media/enzo/backup-sdb2/torrents` e `.../series`.
- **IDIOMA_DESTINO** — idioma da tradução (ex.: `pt`).
- **TRADUCAO_BACKEND** — `"libretranslate"` (grátis, sem chave; padrão) ou `"none"` (só extrai; traduza no SubtitleEdit).
- **LIBRETRANSLATE_URL** — URL da API LibreTranslate.
- **WATCHER_ESTABILIDADE_SEGUNDOS** — tempo de espera após detectar novo MKV antes de extrair (evitar arquivo em download).
- **EMBUTIR_SRT_NO_MKV** — se `True`, ao final gera um novo MKV com `*_PT.srt` embutido (ver `docs/embutir-legenda-no-mkv.md`).

### LibreTranslate local via Docker (estrutura física em `docker/`)

Este projeto inclui configuração dedicada em `docker/libretranslate/`, no mesmo estilo do `subtitleedit-cli`.

Build manual:

```bash
docker build -t libretranslate-local:1.0 -f docker/libretranslate/docker/Dockerfile .
```

Run manual:

```bash
docker run --rm -p 5000:5000 --name libretranslate-local libretranslate-local:1.0
```

Ou com compose:

```bash
docker compose up -d libretranslate
```

Com isso, use no `config.py`:

```python
LIBRETRANSLATE_URL = "http://localhost:5000/translate"
```

## Uso

### Menu interativo

```bash
python extrair_legendas.py
```

Opções: processar um arquivo, processar pastas em lote, processar uma pasta (somente essa), iniciar watcher, sair.

### Linha de comando

```bash
# Processar um MKV (extrai todas as faixas de legenda)
python extrair_legendas.py --arquivo /caminho/filme.mkv

# Traduzir um SRT ou ASS/SSA já existente (gera <nome>_PT.srt ou <nome>_PT.ass)
python extrair_legendas.py --traduzir-srt /caminho/legenda.srt
python extrair_legendas.py --traduzir-srt /caminho/legenda.ass

# Processar em lote todas as pastas do config
python extrair_legendas.py --lote

# Watcher: monitora pastas e extrai todas as faixas de novos MKVs
python extrair_legendas.py --watch

# Pastas diferentes das do config
python extrair_legendas.py --pastas /path/torrents /path/series --lote
```

### Resultado

Para cada MKV processado são gerados, no mesmo diretório:

- `nome_do_arquivo_faixaN.srt` (ou `.ass` / `.sup`, conforme o codec), para cada faixa detectada.
- Se a tradução estiver ativa (`TRADUCAO_BACKEND` diferente de `"none"`): também `nome_do_arquivo_faixaN_PT.srt` (SRT) ou `nome_do_arquivo_faixaN_PT.ass` (ASS/SSA).

## Arquivos de instrução do projeto

- `INSTRUCOES_PROJETO.md`: guia de operação e convenções do projeto.
- `instrucoes/projeto.md`: registro contínuo de instruções e decisões.

## Traduzir no SubtitleEdit com Google Translate API

Depois que o script gerar o arquivo de legenda (ex.: `Bluey.S01E51..._faixa2.srt` em inglês):

1. Abra o **SubtitleEdit** e abra esse arquivo `.srt` (Arquivo > Abrir).
2. No menu: **Ferramentas** → **Auto-traduzir** (ou "Auto-translate").
3. Na janela que abrir, escolha o **motor de tradução**: **Google Translate** (ou "Google Translate V1").
4. Defina o **idioma de origem** (ex.: Inglês) e o **idioma de destino** (ex.: Português).
5. Clique em **Iniciar** / **Start** e aguarde a tradução.
6. Salve o arquivo: **Arquivo** → **Salvar como** e use um nome como `nome_do_episodio_PT.srt`.

 (Opcional: em Opções > Configurações > Tradução você pode configurar a chave da API do Google Translate, se quiser usar quota própria.)

## SubtitleEdit: OCR (legendas em imagem)

- **Legendas em imagem (PGS/SUP):** abra o MKV ou o `.sup` extraído no SubtitleEdit e use **Ferramentas > OCR** (ex.: Google Lens Sharp ou o método disponível na sua versão) para converter imagens em texto. Depois use **Ferramentas > Auto-traduzir** como acima.

O SubtitleEdit não oferece OCR nem tradução por linha de comando; essas etapas são feitas manualmente na interface.

## OCR Automático em PGS/SUP (watcher) via seconv

Para legendas em imagem (`HDMV PGS` / `.sup`), o watcher deste script pode fazer OCR automaticamente usando o `subtitleedit-cli` (também conhecido como `seconv`) via Docker.

O fluxo é:
1. `mkvextract` extrai a faixa `.sup`
2. `seconv` roda OCR e gera um ou mais `.srt`
3. quando houver múltiplos `.srt`, o script consolida tudo em um `*_OCR_MERGED.srt`
4. o script traduz o `*_OCR_MERGED.srt` para `*_PT.srt`

Configuração (em `config.py`):
- `SECONV_DOCKER_IMAGE` (ex.: `seconv:1.0`)
- `SECONV_OCR_DB` (ex.: `Latin`)
- `SECONV_MERGE_SRTS` (consolidar múltiplos `.srt`)

Se o OCR ficar incompleto, reconstruir a imagem Docker com uma versão mais nova do `subtitleedit-cli` costuma melhorar a cobertura.

## Integração Django (API + Celery)

Além do modo CLI (script), este repositório inclui um backend Django para gerenciar extração/tradução em background.

### O que o Django oferece

- API autenticada por token para criar e consultar jobs.
- `Celery` (com `Redis`) para executar extração/tradução sem travar a API.
- `Management command` `run_mkv_watcher` para detectar novos arquivos `.mkv` e enfileirar jobs.

### Pré-requisitos

- `PostgreSQL` (recomendado; por padrão o Django cai para SQLite quando Postgres não estiver configurado via env).
- `Redis` para o broker do Celery.
- `MKVToolNix` e `mkvmerge`/`mkvextract` disponíveis no `PATH` do ambiente onde o worker Celery roda.
- Se usar OCR automatizado: configurar `seconv` via Docker (ver `docker/`) e os parâmetros em `config.py` (ex.: `SECONV_DOCKER_IMAGE`, `SECONV_OCR_DB`, etc.).

### Configuração via variáveis de ambiente

No ambiente do Django/worker, configure:

- `DATABASE_URL` (opcional; se definido, substitui `POSTGRES_*`)
- `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_HOST`, `POSTGRES_PORT` (quando `DATABASE_URL` não estiver definido)
- `DJANGO_SECRET_KEY` (opcional; default inseguro-dev apenas para desenvolvimento)
- `DJANGO_DEBUG` (opcional; default `1`)
- `DJANGO_ALLOWED_HOSTS` (opcional; default `*`)
- `CELERY_BROKER_URL` (default: `redis://localhost:6379/0`)
- `CELERY_RESULT_BACKEND` (default: `django-db`)
- `MKV_ALLOWED_ROOTS` (lista separada por vírgula com as pastas permitidas; usado para validar `mkv_path` e também para o watcher)
- `WATCHER_ESTABILIDADE_SEGUNDOS` (default: `5`)
- `WATCHER_IDIOMA_DESTINO` (default: `pt`)
- `WATCHER_TRANSLATION_BACKEND` (default: `libretranslate`)

### Setup (migrations + usuário + token)

```bash
pip install -r requirements.txt
python manage.py makemigrations
python manage.py migrate
python manage.py createsuperuser
```

Para criar um token DRF:

```bash
python manage.py shell
```

No shell, execute:

```python
from django.contrib.auth import get_user_model
from rest_framework.authtoken.models import Token

User = get_user_model()
u = User.objects.get(username="SEU_USUARIO")
Token.objects.get_or_create(user=u)
print(Token.objects.get(user=u).key)
```

### Rodar servidor e worker

Servidor:

```bash
python manage.py runserver 0.0.0.0:8000
```

Worker Celery:

```bash
celery -A tradutor_legendas worker -l info
```

Watcher:

```bash
python manage.py run_mkv_watcher
```

### API (rotas)

- `POST /api/jobs/` cria um job (gera a extração/tradução em background).
- `GET /api/jobs/` lista jobs.
- `GET /api/jobs/{id}/` consulta status/resultado.

Autenticação: envie `Authorization: Token <token>`.

Exemplo de criação:

```bash
curl -X POST http://localhost:8000/api/jobs/ \
  -H "Authorization: Token SEU_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "mkv_path": "/caminho/para/filme.mkv",
    "track_number": null,
    "idioma_destino": "pt",
    "translation_backend": "libretranslate"
  }'
```

### UI Web (Django Templates)

A interface server-side fica em:

- Login: `GET /web/login/`
- Lista de jobs: `GET /web/jobs/`
- Criar job: `GET /web/jobs/create/`
- Detalhe do job: `GET /web/jobs/<uuid>/`

Pré-requisito: você precisa estar logado com um usuário Django (criado via `python manage.py createsuperuser` ou outro método).

Fluxo:

1. Acesse `http://localhost:8000/web/login/` e faça login.
2. Vá em `http://localhost:8000/web/jobs/create/`.
3. Faça upload de um arquivo `.mkv` na página de criação de job.
4. O job será enfileirado via Celery e o status/log aparece em `http://localhost:8000/web/jobs/<uuid>/`.

Por padrão, os arquivos enviados pela UI web são salvos em `uploaded_mkvs/` na raiz do projeto (configurável por `MKV_UPLOAD_DIR`) e depois processados pelo worker.
