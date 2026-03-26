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

# Listar conteúdo do MKV (inspeção: faixas/anexos/capítulos/tags) e sair
python extrair_legendas.py --arquivo /caminho/filme.mkv --listar-conteudo

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

## Repositório desktop (sem Django)

Este repositório foi simplificado para desktop/CLI:

- `core_engine/`: núcleo reutilizável do processamento.
- `main.py`: entrada da GUI em `PySide6`.
- `extrair_legendas.py`: modo CLI legado.

Para abrir GUI:

```bash
python main.py
```

Para forçar modo CLI:

```bash
python main.py --cli
```

## Gerar executável com PyInstaller

Instale dependências:

```bash
pip install -r requirements.txt
```

Build:

```bash
pyinstaller tradutor_legendas.spec
```

Saída esperada: binário em `dist/tradutor-legendas`.

## Projeto web Django

A versão web com monetização foi separada para outro repositório (`tradutor-legendas-web`), mantendo API, filas e novas entidades de plano/assinatura.
