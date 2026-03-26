# Registro de Instruções do Projeto

Este arquivo centraliza instruções operacionais e decisões de manutenção.

## Diretriz atual

- O processamento de MKV deve exportar todas as faixas de legenda encontradas.
- O padrão de nomes deve preservar a faixa (`_faixaN`) para evitar perda por sobrescrita.
- Tradução automática deve ocorrer por faixa quando o arquivo extraído for SRT.
- Na interface web de criação de job, o usuário deve enviar o arquivo `.mkv` (upload), sem depender de informar caminho local no servidor.

## Como atualizar este arquivo

- Adicionar novas decisões em seções curtas e datadas.
- Manter texto objetivo e orientado à operação do script.
- Evitar duplicar conteúdo já presente no `README.md`; use este arquivo para regras e histórico.

