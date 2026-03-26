# Embutir legenda `.srt` no `.mkv` (mux automático)

O Matroska (MKV) permite incluir faixas de legenda. Este projeto pode, ao final da tradução, **gerar um novo MKV** contendo as legendas PT como novas faixas, sem re-encode de vídeo/áudio, usando `mkvmerge` (MKVToolNix).

## Requisitos

- `mkvmerge` instalado (MKVToolNix)
  - Ubuntu/Debian: `sudo apt install mkvtoolnix`

## Como habilitar

No `config.py`:

```python
EMBUTIR_SRT_NO_MKV = True
MKV_MUX_SUFFIX = "_COM_LEGENDA"
MKV_MUX_SET_DEFAULT = False
```

## Resultado

Ao processar um arquivo `Filme.mkv`, se o script gerar `*_PT.srt`, ele cria:

- `Filme_COM_LEGENDA.mkv`

Incluindo uma ou mais faixas `.srt` adicionadas.

## Observações

- O arquivo original **não é alterado**.
- Para PT-BR, o código de idioma usado é `pt-BR` (quando o destino estiver como `pt`, `pt-BR` ou `pt_br`, o script normaliza para `pt-BR`).
