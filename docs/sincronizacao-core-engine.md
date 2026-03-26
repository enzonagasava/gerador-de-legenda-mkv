# Sincronização do `core_engine`

Este repositório (desktop) e o repositório web (`tradutor-legendas-web`) compartilham a mesma ideia de núcleo (`core_engine`).

## Estratégia adotada

- Fonte de verdade inicial: repositório desktop.
- Cadência de sync: por release.
- Processo: copiar mudanças do `core_engine/` para o repo web e validar os testes dos dois lados.

## Checklist por release

1. Atualizar `core_engine/` no desktop.
2. Executar smoke local (`python main.py --cli` com um `.mkv` de teste).
3. Replicar as mesmas mudanças em `tradutor-legendas-web/core_engine/`.
4. Rodar `python manage.py test` no repo web.
5. Publicar release/tag em ambos os repositórios com a mesma versão do núcleo.
