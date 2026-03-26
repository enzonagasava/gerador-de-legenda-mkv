# LibreTranslate local (Docker)

Este diretório contém a configuração para subir uma API local do LibreTranslate, similar ao padrão usado em `docker/subtitleedit-cli`.

## Build manual

No diretório raiz do projeto:

```bash
docker build -t libretranslate-local:1.0 -f docker/libretranslate/docker/Dockerfile .
```

## Run manual

```bash
docker run --rm -p 5000:5000 --name libretranslate-local libretranslate-local:1.0
```

Endpoint esperado no `config.py`:

```python
LIBRETRANSLATE_URL = "http://127.0.0.1:5000/translate"
```

## Via docker compose

```bash
docker compose up -d libretranslate
```

Teste rápido:

```bash
curl -X POST http://127.0.0.1:5000/translate \
  -d "q=Hello world&source=en&target=pt&format=text"
```
