FROM python:3.12-slim

WORKDIR /app

RUN pip install --no-cache-dir libretranslate==1.9.5

EXPOSE 5000

# Mantemos en/pt para startup mais leve no host local.
# Observacao: "br" (pt-BR) nao e um codigo de idioma valido aqui; usamos "pt".
CMD ["libretranslate", "--host", "0.0.0.0", "--port", "5000", "--load-only", "en,pb"]
