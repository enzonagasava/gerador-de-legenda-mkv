#!/bin/bash
# Usa o ambiente virtual do projeto se existir
cd "$(dirname "$0")"
if [ -d .venv ]; then
    exec .venv/bin/python3 extrair_legendas.py "$@"
else
    exec python3 extrair_legendas.py "$@"
fi
