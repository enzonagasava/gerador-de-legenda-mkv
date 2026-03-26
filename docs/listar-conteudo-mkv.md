## Listar conteúdo de um MKV (inspeção)

### Objetivo
Inspecionar um arquivo `.mkv` e listar o que existe “dentro” dele, sem extrair nem traduzir nada:

- **Faixas** (tracks): vídeo/áudio/legendas (id, tipo, codec, idioma, nome, flags).
- **Anexos** (attachments): fonts/imagens embutidas (id, nome, mime, tamanho).
- **Capítulos** e **tags**: indicação de presença (e contagem de capítulos quando detectável).

### Como usar (CLI)

```bash
python extrair_legendas.py --arquivo /caminho/filme.mkv --listar-conteudo
```

### Saída esperada
O script imprime seções:

- `[FAIXAS]` com uma linha por track.
- `[ANEXOS]` com uma linha por attachment (se houver).
- `[CAPÍTULOS]` e `[TAGS]` indicando se foram detectados.

### Como funciona
O modo de inspeção usa preferencialmente:

- `mkvmerge -J <arquivo>` (JSON) para obter detalhes de tracks/anexos.

Se o JSON falhar (por exemplo, por incompatibilidade/erro do `mkvmerge`), o script tenta um fallback simples para listar faixas via `mkvmerge -i`.

### Limitações conhecidas
- Nem todo MKV possui anexos/capítulos/tags.
- Dependendo da versão do MKVToolNix, campos do JSON podem variar; o script faz leitura “best-effort”.
- O fallback (`mkvmerge -i`) é mais limitado (pode não incluir idioma/nome/flags).
