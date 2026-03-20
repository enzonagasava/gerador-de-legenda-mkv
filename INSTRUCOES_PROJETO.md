# Instruções do Projeto

Este projeto extrai legendas de arquivos MKV e pode traduzir o resultado para português.

## Objetivo

- Extrair legendas de arquivos `.mkv` para formatos de legenda (`.srt`, `.ass`, `.ssa`, `.sup`).
- Processar todas as faixas de legenda detectadas no MKV durante a exportação.
- Traduzir legendas de texto para `*_PT.srt` quando a tradução estiver habilitada.

## Arquivos principais

- `extrair_legendas.py`: script principal de extração, OCR e tradução.
- `config.py`: configurações de pastas, backend de tradução e OCR.
- `README.md`: guia de uso e configuração.

## Regras operacionais

- Ao processar um MKV, o script deve exportar todas as faixas de legenda disponíveis.
- A saída deve usar sufixo por faixa para evitar sobrescrita, no padrão `*_faixaN`.
- Quando houver tradução automática ativa, cada SRT extraído deve gerar seu respectivo `*_PT.srt`.

## Onde guardar instruções contínuas

As instruções evolutivas, decisões e padrões operacionais do projeto devem ser registradas em:

- `instrucoes/projeto.md`

