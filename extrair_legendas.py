#!/usr/bin/env python3
"""
Extrai legendas de arquivos MKV para SRT/ASS/SUP usando mkvtoolnix.
Opcionalmente traduz o SRT em Python (Google Translate V1/V2 ou LibreTranslate).
Legendas em imagem (PGS/SUP): use SubtitleEdit para OCR, ou extraia e traduza só se já for texto.
"""

import os
import sys
import re
import subprocess
import time
import argparse
import threading
import shlex
from pathlib import Path
from typing import List, Dict, Optional, Tuple
from queue import Queue, Empty

try:
    import requests
except ImportError:
    requests = None

try:
    from deep_translator import GoogleTranslator
except ImportError:
    GoogleTranslator = None

try:
    import config
except ImportError:
    config = None

PASTAS_PADRAO = [
    "/media/enzo/backup-sdb2/torrents",
    "/media/enzo/backup-sdb2/series",
]
WATCHER_ESTABILIDADE_PADRAO = 5


def _config(key: str, default):
    if config is None:
        return default
    return getattr(config, key, default)


# Regex para linha de tempo SRT (00:00:01,000 --> 00:00:02,500)
_TIMESTAMP_RE = re.compile(r"^\d{2}:\d{2}:\d{2}[,.]\d{3}\s*-->\s*\d{2}:\d{2}:\d{2}[,.]\d{3}$")


def _parse_srt_blocks(arquivo: str) -> List[Tuple[str, str, str]]:
    """Retorna lista de (numero_linha, linha_timestamp, texto)."""
    with open(arquivo, "r", encoding="utf-8-sig") as f:
        linhas = [ln.rstrip("\n") for ln in f.readlines()]
    blocos = []
    i = 0
    while i < len(linhas):
        linha = linhas[i]
        if re.match(r"^\d+$", linha.strip()):
            num = linha.strip()
            i += 1
            if i < len(linhas) and _TIMESTAMP_RE.match(linhas[i].strip()):
                ts = linhas[i].strip()
                i += 1
                textos = []
                while i < len(linhas) and linhas[i].strip():
                    textos.append(linhas[i])
                    i += 1
                blocos.append((num, ts, "\n".join(textos)))
        i += 1
    return blocos


def _traduzir_texto_libretranslate(texto: str, idioma_destino: str, session: requests.Session) -> str:
    """Traduz um texto via LibreTranslate."""
    url = _config("LIBRETRANSLATE_URL", "https://libretranslate.de/translate").rstrip("/")
    if not url.endswith("/translate"):
        url = f"{url}/translate" if "/translate" not in url else url
    payload = {"q": texto, "source": "auto", "target": idioma_destino, "format": "text"}
    r = session.post(url, data=payload, timeout=30)
    r.raise_for_status()
    j = r.json() if r.content else {}
    out = j.get("translatedText") or j.get("translation") or j.get("translated_text") or texto
    return str(out).strip() or texto


def _traduzir_texto_google(texto: str, idioma_destino: str, api_key: str, session: requests.Session) -> str:
    """Traduz um texto via Google Cloud Translation API v2 (REST)."""
    url = "https://translation.googleapis.com/language/translate/v2"
    params = {"q": texto, "target": idioma_destino, "source": "auto", "key": api_key}
    r = session.post(url, data=params, timeout=30)
    r.raise_for_status()
    j = r.json()
    try:
        return (j.get("data") or {}).get("translations", [{}])[0].get("translatedText", texto) or texto
    except (IndexError, KeyError, TypeError):
        return texto


def _traduzir_lote_google(textos: List[str], idioma_destino: str, api_key: str, session: requests.Session) -> List[str]:
    """Traduz uma lista de textos em uma chamada (API v2 aceita múltiplos q). Retorna lista na mesma ordem."""
    if not textos:
        return []
    url = "https://translation.googleapis.com/language/translate/v2"
    data = [("target", idioma_destino), ("source", "auto"), ("key", api_key)]
    for t in textos:
        data.append(("q", t))
    r = session.post(url, data=data, timeout=60)
    r.raise_for_status()
    j = r.json()
    out = []
    for tr in (j.get("data") or {}).get("translations", []):
        out.append(tr.get("translatedText", "") or "")
    return out if len(out) == len(textos) else [textos[i] if i < len(textos) else "" for i in range(len(textos))]


def _traduzir_texto_google_v1(texto: str, idioma_destino: str) -> str:
    """Traduz um texto via Google Translate sem API key (deep-translator; como SubtitleEdit V1)."""
    if GoogleTranslator is None:
        raise RuntimeError("deep-translator não instalado. Execute: pip install deep-translator")
    try:
        return GoogleTranslator(source="auto", target=idioma_destino).translate(text=texto) or texto
    except Exception:
        return texto


def traduzir_arquivo_srt(arquivo_entrada: str, arquivo_saida: str, idioma_destino: str) -> bool:
    """Traduz um arquivo SRT e salva em arquivo_saida. Retorna True se ok."""
    if requests is None:
        print("Tradução requer: pip install requests")
        return False
    backend = str(_config("TRADUCAO_BACKEND", "libretranslate")).lower().strip()
    if backend == "none":
        return False
    blocos = _parse_srt_blocks(arquivo_entrada)
    if not blocos:
        return False
    session = requests.Session()
    GOOGLE_BATCH = 50

    with open(arquivo_saida, "w", encoding="utf-8") as f:
        if backend == "google":
            api_key = os.environ.get("GOOGLE_TRANSLATE_API_KEY") or _config("GOOGLE_TRANSLATE_API_KEY", None)
            if not api_key:
                print("Erro: defina GOOGLE_TRANSLATE_API_KEY no ambiente ou em config.py para usar Google Translate.")
                return False
            i = 0
            while i < len(blocos):
                batch_blocks = []
                batch_texts = []
                while i < len(blocos) and len(batch_blocks) < GOOGLE_BATCH:
                    num, ts, texto = blocos[i]
                    if not texto.strip():
                        f.write(f"{num}\n{ts}\n\n\n")
                        i += 1
                        continue
                    batch_blocks.append((num, ts, texto))
                    batch_texts.append(texto)
                    i += 1
                if batch_blocks:
                    try:
                        trad_list = _traduzir_lote_google(batch_texts, idioma_destino, api_key, session)
                        for (num, ts, texto), trad in zip(batch_blocks, trad_list):
                            f.write(f"{num}\n{ts}\n{trad or texto}\n\n")
                    except Exception as e:
                        print(f"Erro Google Translate: {e}")
                        for (num, ts, texto) in batch_blocks:
                            f.write(f"{num}\n{ts}\n{texto}\n\n")
        elif backend == "google_v1":
            if GoogleTranslator is None:
                print("Erro: google_v1 requer deep-translator. Execute: pip install deep-translator")
                return False
            for num, ts, texto in blocos:
                f.write(f"{num}\n{ts}\n")
                if texto.strip():
                    try:
                        trad = _traduzir_texto_google_v1(texto, idioma_destino)
                        f.write(trad + "\n\n")
                        time.sleep(0.2)
                    except Exception as e:
                        print(f"Erro Google V1: {e}")
                        f.write(texto + "\n\n")
                else:
                    f.write("\n")
        else:
            # LibreTranslate: um bloco por vez
            for num, ts, texto in blocos:
                f.write(f"{num}\n{ts}\n")
                if texto.strip():
                    try:
                        trad = _traduzir_texto_libretranslate(texto, idioma_destino, session)
                        f.write(trad + "\n\n")
                    except Exception as e:
                        print(f"Erro tradução: {e}")
                        f.write(texto + "\n\n")
                else:
                    f.write("\n")
    return True


class MKVExtractor:
    """Extrai faixas de legenda de MKV usando mkvtoolnix.

    Tradução de SRT é opcional (dependendo de `TRADUCAO_BACKEND`).
    """

    def __init__(self):
        self._verificar_dependencias()

    def _verificar_dependencias(self) -> None:
        try:
            subprocess.run(
                ["mkvmerge", "--version"],
                capture_output=True,
                check=True,
            )
        except (subprocess.CalledProcessError, FileNotFoundError):
            print("Erro: MKVToolNix não encontrado.")
            print("Instale: https://mkvtoolnix.download/ ou: sudo apt install mkvtoolnix")
            sys.exit(1)

    def _listar_faixas_mkvmerge(self, arquivo_mkv: str) -> List[Dict]:
        try:
            r = subprocess.run(
                ["mkvmerge", "-i", arquivo_mkv],
                capture_output=True,
                text=True,
                encoding="utf-8",
            )
            if r.returncode != 0:
                return []
            faixas = []
            for linha in r.stdout.splitlines():
                # Inglês: "Track ID 3: subtitles (SubRip/SRT)" | Português: "ID da faixa 3: subtitles (SubRip/SRT)"
                m = re.match(r"(?:Track ID|ID da faixa) (\d+):\s*([\w-]+)\s*\(([^)]+)\)", linha.strip())
                if m:
                    tid, tipo, codec = m.groups()
                    tipo_lower = tipo.lower().strip()
                    codec_upper = codec.strip().upper()
                    faixas.append({
                        "numero": int(tid),
                        "tipo": tipo_lower,
                        "codec": codec_upper,
                    })
            return faixas
        except Exception:
            return []

    def _idioma_faixa_mkvinfo(self, arquivo_mkv: str, numero_faixa: int) -> Optional[str]:
        try:
            r = subprocess.run(
                ["mkvinfo", arquivo_mkv],
                capture_output=True,
                text=True,
                encoding="utf-8",
            )
            if r.returncode != 0:
                return None
            dentro_track = False
            track_num = None
            for linha in r.stdout.splitlines():
                if "| + Track" in linha and "Track number:" not in linha:
                    dentro_track = True
                    track_num = None
                elif dentro_track and "|  + Track number:" in linha:
                    try:
                        track_num = int(linha.split("Track number:")[1].strip().split("(")[0].strip())
                    except (IndexError, ValueError):
                        pass
                elif dentro_track and track_num == numero_faixa:
                    if "|  + Language (ISO 639-2):" in linha:
                        return linha.split("Language (ISO 639-2):")[1].strip()
                    if "|  + Language:" in linha:
                        return linha.split("Language:")[1].strip()
                if "| + " in linha and "|  + " not in linha and dentro_track:
                    dentro_track = False
            return None
        except Exception:
            return None

    def _eh_faixa_legenda(self, faixa: Dict) -> bool:
        """Considera legenda por tipo (subtitles/Legendas) ou por codec (S_TEXT, SubRip, etc.)."""
        tipo = (faixa.get("tipo") or "").lower()
        codec = (faixa.get("codec") or "").upper()
        if tipo in ("subtitles", "subtitle", "legendas", "legenda", "untertitel"):
            return True
        if "subtitle" in tipo or "legenda" in tipo:
            return True
        # Codec de legenda no Matroska (mkvmerge pode mostrar tipo genérico)
        if codec.startswith("S_TEXT") or "SUBRIP" in codec or "SRT" in codec:
            return True
        if "ASS" in codec or "SSA" in codec or "VOBSUB" in codec or "PGS" in codec or "SUP" in codec:
            return True
        return False

    def listar_faixas(self, arquivo_mkv: str) -> List[Dict]:
        """Lista faixas de legenda do MKV (número, tipo, codec, idioma)."""
        faixas_raw = self._listar_faixas_mkvmerge(arquivo_mkv)
        faixas_legenda = [f for f in faixas_raw if self._eh_faixa_legenda(f)]
        for f in faixas_legenda:
            f["idioma"] = self._idioma_faixa_mkvinfo(arquivo_mkv, f["numero"])
        return faixas_legenda

    def escolher_faixa_auto(self, faixas_legenda: List[Dict]) -> Optional[int]:
        """Preferir inglês; senão a primeira legenda."""
        for f in faixas_legenda:
            lang = (f.get("idioma") or "").lower()
            if lang.startswith("en"):
                return f["numero"]
        if faixas_legenda:
            return faixas_legenda[0]["numero"]
        return None

    def extrair_legenda(
        self,
        arquivo_mkv: str,
        numero_faixa: int,
        arquivo_saida: Optional[str] = None,
    ) -> Optional[str]:
        """Extrai uma faixa de legenda do MKV para .srt, .ass ou .sup. Sem OCR."""
        base = Path(arquivo_mkv)
        if not arquivo_saida:
            # Gera o arquivo com o mesmo "stem" do MKV (ex: termina em WEBRip),
            # para ficar consistente com o nome do episódio/arquivo original.
            # Isso evita criar nomes do tipo `*_faixa2`/`*_faixa3`.
            arquivo_saida = str(base.parent / base.stem)

        base_saida = Path(arquivo_saida)
        # IMPORTANTE:
        # `Path.suffix` considera qualquer coisa após o último ponto como "extensão".
        # Como nomes de episódios costumam terminar com ".WEBRip", não queremos
        # remover esse "suffix" acidentalmente.
        # Remova apenas se a pessoa tiver passado explicitamente uma extensão
        # de legenda.
        if base_saida.suffix.lower() in (".srt", ".ass", ".ssa", ".sup"):
            base_saida = base_saida.with_suffix("")
        arquivo_saida_base = str(base_saida)

        def _looks_like_srt(path_base: Path) -> bool:
            """Heurística leve para reconhecer SRT mesmo sem extensão."""
            try:
                if not path_base.exists() or not path_base.is_file():
                    return False
                with open(path_base, "r", encoding="utf-8", errors="ignore") as f:
                    # SRT: primeiro bloco costuma começar com número e depois timestamp.
                    first_nonempty = []
                    while len(first_nonempty) < 2:
                        ln = f.readline()
                        if not ln:
                            break
                        ln = ln.strip()
                        if ln:
                            first_nonempty.append(ln)
                    if len(first_nonempty) < 2:
                        return False
                    if not re.match(r"^\d+$", first_nonempty[0]):
                        return False
                    return bool(_TIMESTAMP_RE.match(first_nonempty[1]))
            except Exception:
                return False

        def _looks_like_ass(path_base: Path) -> bool:
            """Heurística para reconhecer ASS/SSA mesmo sem extensão."""
            try:
                if not path_base.exists() or not path_base.is_file():
                    return False
                with open(path_base, "r", encoding="utf-8", errors="ignore") as f:
                    head = f.read(2000)
                return ("[Script Info]" in head) or ("Dialogue:" in head)
            except Exception:
                return False

        try:
            cmd = [
                "mkvextract",
                "tracks",
                arquivo_mkv,
                f"{numero_faixa}:{arquivo_saida_base}",
            ]
            r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
            if r.returncode != 0:
                print(f"Erro na extração: {r.stderr or r.stdout}")
                return None

            # Primeiro, procure por arquivos com extensão "clássica" (append).
            for ext in (".srt", ".ass", ".ssa", ".sup"):
                p = Path(arquivo_saida_base + ext)
                if p.exists():
                    return str(p)

            # Fallback: mkvextract pode gerar um arquivo sem extensão (ex: termina em ".WEBRip").
            p_sem_ext = Path(arquivo_saida_base)
            if p_sem_ext.exists() and p_sem_ext.is_file():
                if _looks_like_srt(p_sem_ext):
                    destino_srt = Path(str(p_sem_ext) + ".srt")
                    if not destino_srt.exists():
                        try:
                            p_sem_ext.rename(destino_srt)
                        except Exception:
                            # Se renomear falhar, ainda tentamos retornar o base (tradução pode falhar).
                            pass
                    if destino_srt.exists():
                        return str(destino_srt)
                if _looks_like_ass(p_sem_ext):
                    destino_ass = Path(str(p_sem_ext) + ".ass")
                    if not destino_ass.exists():
                        try:
                            p_sem_ext.rename(destino_ass)
                        except Exception:
                            pass
                    if destino_ass.exists():
                        return str(destino_ass)

                # Se não parece SRT nem ASS, tratamos como imagem baseada (ex: PGS/SUP).
                destino_sup = Path(str(p_sem_ext) + ".sup")
                if not destino_sup.exists():
                    try:
                        p_sem_ext.rename(destino_sup)
                    except Exception:
                        pass
                if destino_sup.exists():
                    return str(destino_sup)
            return None
        except Exception as e:
            print(f"Erro ao extrair: {e}")
            return None

    def ja_extraido(self, arquivo_mkv: str) -> bool:
        """True se já tem legenda final (_PT.srt) ou legenda extraída (quando tradução está desligada)."""
        base = Path(arquivo_mkv).stem
        diretorio = Path(arquivo_mkv).parent
        backend = str(_config("TRADUCAO_BACKEND", "libretranslate")).lower().strip()

        # Compatibilidade com o padrão antigo (sem faixa) e o padrão novo (com faixa).
        # - Legenda PT antiga:  {base}_PT.srt
        # - Legenda PT nova:    {base}_faixaN_PT.srt
        pt_srt_legado = diretorio / f"{base}_PT.srt"
        if pt_srt_legado.exists():
            return True

        if backend != "none":
            try:
                for p in diretorio.iterdir():
                    if not p.is_file():
                        continue
                    if p.suffix.lower() != ".srt":
                        continue
                    # Ex.: Bluey.S01E51..._faixa2_PT.srt
                    if p.name.startswith(base + "_faixa") and p.name.endswith("_PT.srt"):
                        return True
            except Exception:
                # Se falhar a varredura, cai no retorno padrão (False).
                pass

            return False

        if backend == "none":
            for p in diretorio.iterdir():
                if not p.is_file():
                    continue
                # Aceita o novo padrão (sem `_faixaN`) e também os antigos (com `_faixaN`).
                if p.suffix.lower() not in (".srt", ".ass", ".sup", ".ssa"):
                    continue
                if p.stem == base or p.stem.startswith(base + "_faixa"):
                    return True
        return False

    def _ocr_sup_via_subtitleedit(self, sup_path: str) -> Optional[str]:
        """
        Faz OCR de um `.sup` (PGS) para `.srt` usando o SubtitleEdit via linha de comando.

        Requer:
        - SUBTITLEEDIT_EXE_PATH em config.py
        - mono instalado (para rodar SubtitleEdit.exe no Linux)
        """
        exe_path = str(_config("SUBTITLEEDIT_EXE_PATH", "")).strip()
        if not exe_path:
            print("OCR PGS/SUP requer SubtitleEdit configurado: defina SUBTITLEEDIT_EXE_PATH em config.py.")
            return None
        mono_cmd = str(_config("SUBTITLEEDIT_MONO_CMD", "mono")).strip() or "mono"
        out_format = str(_config("SUBTITLEEDIT_OCR_OUTPUT_FORMAT", "subrip")).strip() or "subrip"

        sup = Path(sup_path)
        if not sup.exists():
            return None

        out_dir = sup.parent
        expected = sup.with_suffix(".srt")

        # SubtitleEdit pode estar instalado:
        # - como /SubtitleEdit (Linux binary) -> roda sem mono
        # - ou como Mono app / SubtitleEdit.exe -> pode precisar de `mono`
        if exe_path.lower().endswith(".exe"):
            cmd = [mono_cmd, exe_path, "/convert", str(sup), out_format, f"/outputfolder:{str(out_dir)}"]
        else:
            cmd = [exe_path, "/convert", str(sup), out_format, f"/outputfolder:{str(out_dir)}"]
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
        if r.returncode != 0:
            print(f"Erro ao executar SubtitleEdit /convert OCR: {r.stderr or r.stdout}")
            return None

        if expected.exists():
            return str(expected)

        # Fallback: procura uma SRT que "comece" com o stem do .sup
        try:
            for p in out_dir.iterdir():
                if p.is_file() and p.suffix.lower() == ".srt" and p.stem.startswith(sup.stem):
                    return str(p)
        except Exception:
            pass
        return None

    def _ocr_sup_via_seconv(self, sup_path: str) -> Optional[str]:
        """
        Faz OCR de um `.sup` (PGS) para `.srt` usando `subtitleedit-cli` (seconv).

        Este modo tenta primeiro usar Docker (se configurado), pois costuma ser o
        caminho mais fácil para automatizar em Linux.
        """
        mode = str(_config("SECONV_MODE", "docker")).lower().strip()
        out_format = str(_config("SECONV_OCR_OUTPUT_FORMAT", "subrip")).strip() or "subrip"
        sup = Path(sup_path)
        if not sup.exists() or not sup.is_file():
            return None

        # Heurística de reconhecimento de SRT (mesmo que a extensão/arquivo não seja o padrão).
        def looks_like_srt(p: Path) -> bool:
            try:
                if not p.exists() or not p.is_file():
                    return False
                with open(p, "r", encoding="utf-8-sig", errors="ignore") as f:
                    first_nonempty = []
                    while len(first_nonempty) < 2:
                        ln = f.readline()
                        if not ln:
                            break
                        ln = ln.strip()
                        if ln:
                            first_nonempty.append(ln)
                    if len(first_nonempty) < 2:
                        return False
                    if not re.match(r"^\d+$", first_nonempty[0]):
                        return False
                    return bool(_TIMESTAMP_RE.match(first_nonempty[1]))
            except Exception:
                return False

        out_dir = sup.parent

        # Registra timestamp antes do OCR, para conseguirmos identificar o arquivo
        # que acabou de ser gerado mesmo se ele tiver sobrescrito um arquivo anterior.
        start_ts = time.time()

        if mode == "docker":
            image = str(_config("SECONV_DOCKER_IMAGE", "")).strip()
            if not image:
                print("seconv via Docker requer SECONV_DOCKER_IMAGE configurado.")
                return None
            run_opts = str(_config("SECONV_DOCKER_RUN_OPTS", "")).strip()
            docker_run_parts = shlex.split(run_opts) if run_opts else []

            # Monta o diretório do arquivo em /subtitles (conforme README do seconv).
            # Passamos apenas o basename para dentro do container.
            ocr_db = str(_config("SECONV_OCR_DB", "Latin")).strip() or "Latin"
            seconv_actions = []
            if str(_config("SECONV_APPLY_MERGE_ACTIONS", True)).lower() in ("1", "true", "yes", "sim"):
                # Ações internas do seconv para reduzir fragmentação.
                seconv_actions = ["/MergeSameTimeCodes", "/MergeSameTexts"]

            cmd = [
                "docker",
                "run",
                "--rm",
                *docker_run_parts,
                "-v",
                f"{str(out_dir)}:/subtitles",
                image,
                sup.name,
                out_format,
                f"/ocrdb:{ocr_db}",
                *seconv_actions,
            ]
        else:
            # Modo local: assume que `seconv` está no PATH.
            ocr_db = str(_config("SECONV_OCR_DB", "Latin")).strip() or "Latin"
            seconv_actions = []
            if str(_config("SECONV_APPLY_MERGE_ACTIONS", True)).lower() in ("1", "true", "yes", "sim"):
                seconv_actions = ["/MergeSameTimeCodes", "/MergeSameTexts"]
            cmd = ["seconv", sup.name, out_format, f"/ocrdb:{ocr_db}", *seconv_actions]

        try:
            r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
            if r.returncode != 0:
                print(f"Erro ao executar seconv: {r.stderr or r.stdout}")
                return None
        except FileNotFoundError:
            print("Erro: docker/seconv não encontrado no sistema. Configure SECONV_MODE ou instale dependências.")
            return None
        except Exception as e:
            print(f"Erro ao executar seconv: {e}")
            return None

        # Aguarda um pouco para o FS atualizar metadados (melhora detecção em alguns discos/NFS).
        time.sleep(0.5)

        # Coleta de todos os SRT gerados na conversão.
        # Critérios:
        # - parecem SRT
        # - são do mesmo "sup" (prefixo do stem)
        # - foram gerados após start_ts (com tolerância)
        seconv_merge_srt = bool(_config("SECONV_MERGE_SRTS", True))
        generated_srt_paths: List[Path] = []
        tol = 2.0
        try:
            for p in out_dir.glob("*.srt"):
                try:
                    if not p.is_file():
                        continue
                    if not looks_like_srt(p):
                        continue
                    if not p.stem.startswith(sup.stem):
                        continue
                    st = p.stat()
                    if st.st_size <= 0:
                        continue
                    if st.st_mtime < (start_ts - tol):
                        continue
                    generated_srt_paths.append(p)
                except Exception:
                    continue
        except Exception:
            pass

        # Se não achou por critério de mtime (resolução/overwrites), cai para "qualquer SRT parecendo SRT do mesmo sup".
        if not generated_srt_paths:
            try:
                generated_srt_paths = [
                    p for p in out_dir.glob("*.srt")
                    if p.is_file() and looks_like_srt(p) and p.stem.startswith(sup.stem) and p.stat().st_size > 0
                ]
            except Exception:
                generated_srt_paths = []

        generated_srt_paths = sorted(generated_srt_paths, key=lambda x: x.stat().st_mtime)

        if not generated_srt_paths:
            return None

        if not seconv_merge_srt:
            # Comportamento antigo: retornar o mais recente.
            return str(generated_srt_paths[-1])

        # Merge/dedup em um único SRT consolidado.
        merged_path = out_dir / f"{sup.stem}_OCR_MERGED.srt"

        def time_to_ms(ts_line: str) -> int:
            # ts_line: "00:00:01,000 --> 00:00:02,500"
            try:
                start_str, _ = [x.strip() for x in ts_line.split("-->", 1)]
                # start_str: "00:00:01,000" ou "00:00:01.000"
                parts = start_str.split(":")
                if len(parts) != 3:
                    return 0
                hh = int(parts[0])
                mm = int(parts[1])
                ss_ms = parts[2]
                if "," in ss_ms:
                    ss, ms = ss_ms.split(",", 1)
                else:
                    ss, ms = ss_ms.split(".", 1)
                return (((hh * 60) + mm) * 60 + int(ss)) * 1000 + int(ms.ljust(3, "0")[:3])
            except Exception:
                return 0

        def parse_start_end_ms(ts_line: str) -> Tuple[int, int]:
            try:
                start_str, end_str = [x.strip() for x in ts_line.split("-->", 1)]
                def parse_one(t: str) -> int:
                    parts = t.split(":")
                    hh = int(parts[0])
                    mm = int(parts[1])
                    ss_ms = parts[2]
                    if "," in ss_ms:
                        ss, ms = ss_ms.split(",", 1)
                    else:
                        ss, ms = ss_ms.split(".", 1)
                    return (((hh * 60) + mm) * 60 + int(ss)) * 1000 + int(ms.ljust(3, "0")[:3])
                return parse_one(start_str), parse_one(end_str)
            except Exception:
                return 0, 0

        # Dedup por (start_ms,end_ms,texto normalizado)
        dedup = True
        seen_keys = set()
        merged_blocks: List[Tuple[int, str, str]] = []

        for sp in generated_srt_paths:
            blocks = _parse_srt_blocks(str(sp))
            for _, ts, text in blocks:
                start_ms, end_ms = parse_start_end_ms(ts)
                norm_text = re.sub(r"\s+", " ", (text or "").strip())
                key = (start_ms, end_ms, norm_text)
                if dedup and key in seen_keys:
                    continue
                seen_keys.add(key)
                merged_blocks.append((start_ms, ts, text))

        merged_blocks.sort(key=lambda x: x[0])

        print("seconv OCR (PGS): arquivos SRT detectados:", ", ".join([p.name for p in generated_srt_paths]))
        print(f"seconv OCR (PGS): consolidando em {merged_path}")

        # Escreve SRT consolidado com numeração sequencial.
        with open(merged_path, "w", encoding="utf-8") as f:
            idx = 1
            for _, ts, text in merged_blocks:
                f.write(f"{idx}\n{ts}\n{text or ''}\n\n")
                idx += 1

        return str(merged_path)

    def _pt_srt_destino_para_srt_extraido(self, arquivo_srt: Path) -> Path:
        """
        Converte o nome do SRT extraído para o destino PT preservando a faixa.
        Ex.: `Filme_faixa3.srt` -> `Filme_faixa3_PT.srt`.
        """
        return arquivo_srt.parent / f"{arquivo_srt.stem}_PT.srt"

    def traduzir_srt_extraido(self, arquivo_srt: str) -> bool:
        """
        Traduz um SRT extraído (gerado a partir do MKV) para o arquivo *_PT.srt.

        Este watcher também lida com um caso comum: o arquivo pode ser um
        "SRT sem extensão" (ex: o mkvextract deixa o arquivo terminar em
        `.WEBRip` em vez de `.srt`).
        Retorna True se traduziu (ou se já havia destino).
        """
        src = Path(arquivo_srt)
        if not src.is_file():
            return False

        # Evita traduzir o destino e evita loop.
        if src.stem.endswith("_PT") or src.name.endswith("_PT.srt") or src.name.endswith("_PT"):
            return False

        def _looks_like_srt(path_base: Path) -> bool:
            """Heurística leve para reconhecer SRT mesmo sem extensão."""
            try:
                if not path_base.exists() or not path_base.is_file():
                    return False
                with open(path_base, "r", encoding="utf-8", errors="ignore") as f:
                    first_nonempty = []
                    while len(first_nonempty) < 2:
                        ln = f.readline()
                        if not ln:
                            break
                        ln = ln.strip()
                        if ln:
                            first_nonempty.append(ln)
                    if len(first_nonempty) < 2:
                        return False
                    if not re.match(r"^\d+$", first_nonempty[0]):
                        return False
                    return bool(_TIMESTAMP_RE.match(first_nonempty[1]))
            except Exception:
                return False

        if not _looks_like_srt(src):
            return False

        # Se o arquivo não é `.srt`, renomeia para garantir consistência do stem/destino.
        if src.suffix.lower() != ".srt":
            novo = src.with_name(src.name + ".srt")
            if not novo.exists():
                try:
                    src.rename(novo)
                except Exception:
                    # Se não renomear, ainda tentamos traduzir o conteúdo.
                    novo = src
            src = novo

        idioma = _config("IDIOMA_DESTINO", "pt")
        backend = str(_config("TRADUCAO_BACKEND", "libretranslate")).lower().strip()
        if backend == "none":
            return False

        destino = self._pt_srt_destino_para_srt_extraido(src)
        if destino.exists():
            return True

        print(f"Traduzindo SRT extraído: {src} -> {destino}")
        try:
            return bool(traduzir_arquivo_srt(str(src), str(destino), idioma))
        except Exception as e:
            print(f"Erro ao traduzir SRT: {e}")
            return False

    def processar_mkv(
        self,
        arquivo_mkv: str,
        numero_faixa: Optional[int] = None,
        interativo: bool = False,
    ) -> bool:
        """Lista faixas, extrai todas (ou uma específica, se informada)."""
        arquivo_mkv = os.path.abspath(arquivo_mkv)
        if not os.path.isfile(arquivo_mkv):
            print(f"Arquivo não encontrado: {arquivo_mkv}")
            return False

        faixas = self.listar_faixas(arquivo_mkv)
        if not faixas:
            print(f"Nenhuma faixa de legenda em: {arquivo_mkv}")
            return False

        base = Path(arquivo_mkv)
        backend = str(_config("TRADUCAO_BACKEND", "libretranslate")).lower().strip()
        idioma = _config("IDIOMA_DESTINO", "pt")

        if numero_faixa is None:
            faixas_alvo = faixas
            if interativo:
                print("Faixas de legenda detectadas (exportando todas):")
                for i, f in enumerate(faixas, 1):
                    idioma_faixa = f.get("idioma") or "?"
                    codec = f.get("codec", "?")
                    print(f"  {i}. Faixa {f['numero']} - Idioma: {idioma_faixa} - Codec: {codec}")
        else:
            faixas_alvo = [f for f in faixas if f.get("numero") == numero_faixa]
            if not faixas_alvo:
                print(f"Faixa {numero_faixa} não encontrada em: {arquivo_mkv}")
                return False

        algum_sucesso = False
        for faixa in faixas_alvo:
            num = faixa["numero"]
            base_saida = str(base.parent / f"{base.stem}_faixa{num}")
            path_extraido = self.extrair_legenda(arquivo_mkv, num, arquivo_saida=base_saida)
            if not path_extraido:
                print(f"Falha ao extrair faixa {num}.")
                continue

            algum_sucesso = True
            print(f"Legenda extraída (faixa {num}): {path_extraido}")

            if backend == "none":
                continue

            # Caso 1: legenda textual já em SRT.
            if path_extraido.lower().endswith(".srt"):
                print(f"Traduzindo faixa {num}...")
                if self.traduzir_srt_extraido(path_extraido):
                    print(f"Legenda traduzida (faixa {num}).")
                else:
                    print(f"Falha na tradução da faixa {num}.")
                continue

            # Caso 2: legenda em imagem (SUP): OCR + tradução.
            if path_extraido.lower().endswith(".sup"):
                srt_ocr = self._ocr_sup_via_seconv(path_extraido)
                if not srt_ocr or not os.path.isfile(srt_ocr):
                    srt_ocr = self._ocr_sup_via_subtitleedit(path_extraido)

                if not srt_ocr or not os.path.isfile(srt_ocr):
                    print(f"Falha no OCR automático da faixa {num}. Abra o .sup no SubtitleEdit para OCR manual.")
                    continue

                # O OCR já gerou (pelo menos) um .srt consolidado.
                # Se configurado, apaga o `.sup` (pode ser muito grande).
                if str(_config("APAGAR_SUP_APOS_OCR", True)).lower().strip() in ("1", "true", "yes", "sim"):
                    try:
                        if os.path.isfile(path_extraido):
                            os.remove(path_extraido)
                    except Exception as e:
                        print(f"Aviso: não foi possível apagar o .sup ({path_extraido}): {e}")

                destino_pt = self._pt_srt_destino_para_srt_extraido(Path(srt_ocr))
                print(f"Traduzindo (após OCR) faixa {num}...")
                if traduzir_arquivo_srt(srt_ocr, str(destino_pt), idioma):
                    print(f"Legenda traduzida (faixa {num}): {destino_pt}")
                else:
                    print(f"Falha na tradução da faixa {num} após OCR.")
                continue

        if backend == "none" and algum_sucesso:
            print("Próximo passo: abra no SubtitleEdit → Ferramentas → Auto-traduzir (Google Translate) → salve como *_PT.srt")
        return algum_sucesso

    def processar_lote(self, pastas: Optional[List[str]] = None) -> None:
        """Varre pastas e extrai legenda de cada MKV que ainda não tem arquivo extraído."""
        pastas = pastas or _config("PASTAS", PASTAS_PADRAO)
        for p in pastas:
            if not os.path.isdir(p):
                print(f"Aviso: pasta não encontrada: {p}")

        pastas_ok = [os.path.abspath(p) for p in pastas if os.path.isdir(p)]
        mkvs = []
        for pasta in pastas_ok:
            for mkv in Path(pasta).rglob("*.mkv"):
                mkvs.append(str(mkv))

        print(f"Encontrados {len(mkvs)} arquivos MKV.")
        for i, mkv in enumerate(mkvs, 1):
            if self.ja_extraido(mkv):
                print(f"[{i}/{len(mkvs)}] Pulando (já extraído): {Path(mkv).name}")
                continue
            print(f"[{i}/{len(mkvs)}] Processando: {mkv}")
            try:
                self.processar_mkv(mkv, numero_faixa=None, interativo=False)
            except Exception as e:
                print(f"Erro: {e}")

    def processar_pasta(self, pasta: str) -> None:
        """
        Processa somente uma pasta (não recursivo) procurando `*.mkv` no nível 1.
        Para cada MKV, extrai todas as faixas de legenda e traduz (se habilitado).
        """
        pasta = os.path.abspath(pasta)
        if not os.path.isdir(pasta):
            print(f"Aviso: pasta não encontrada: {pasta}")
            return

        mkvs = sorted([str(p) for p in Path(pasta).glob("*.mkv")])
        if not mkvs:
            print(f"Nenhum MKV encontrado em: {pasta}")
            return

        print(f"Encontrados {len(mkvs)} arquivos MKV em: {pasta}")
        for i, mkv in enumerate(mkvs, 1):
            if self.ja_extraido(mkv):
                print(f"[{i}/{len(mkvs)}] Pulando (já extraído): {Path(mkv).name}")
                continue
            print(f"[{i}/{len(mkvs)}] Processando: {mkv}")
            try:
                self.processar_mkv(mkv, numero_faixa=None, interativo=False)
            except Exception as e:
                print(f"Erro: {e}")


def run_watcher(extractor: MKVExtractor, pastas: List[str], atraso_seg: float) -> None:
    """Monitora pastas e traduz automaticamente legendas.

    Regra 1: ao detectar um novo `.mkv`, executa a extração e (se habilitado)
    traduz o SRT gerado.

    Regra 2: se um `.srt` extraído (padrão `*_faixaN.srt`) surgir, traduz esse
    SRT para `*_PT.srt` automaticamente (caso já tenha sido extraído por outro
    processo).
    """
    try:
        from watchdog.observers import Observer
        from watchdog.events import FileSystemEventHandler
    except ImportError:
        print("Watcher requer: pip install watchdog")
        sys.exit(1)

    fila: Queue = Queue()
    processando = threading.Lock()

    class Handler(FileSystemEventHandler):
        def _provavel_srt_extraido(self, p: str) -> bool:
            """
            Heurística para reduzir falsos positivos:
            - o arquivo precisa "parecer SRT" (mesmo sem extensão)
            - precisa "corresponder" a um MKV no mesmo diretório
            """
            src = Path(p)
            if not src.is_file():
                return False
            if "_PT" in src.name:
                return False

            # Detector leve de SRT (mesmo sem extensão).
            try:
                with open(src, "r", encoding="utf-8", errors="ignore") as f:
                    first_nonempty = []
                    while len(first_nonempty) < 2:
                        ln = f.readline()
                        if not ln:
                            break
                        ln = ln.strip()
                        if ln:
                            first_nonempty.append(ln)
                    if len(first_nonempty) < 2:
                        return False
                    if not re.match(r"^\d+$", first_nonempty[0]):
                        return False
                    if not _TIMESTAMP_RE.match(first_nonempty[1]):
                        return False
            except Exception:
                return False

            # Correspondência com MKV:
            # - Se o arquivo já é `.srt`, tenta achar MKV pelo stem (removendo `_faixaN`).
            if src.suffix.lower() == ".srt":
                mkv_stem = src.stem
                m = re.match(r"^(.*)_faixa\d+$", src.stem)
                if m:
                    mkv_stem = m.group(1)
                return (src.parent / f"{mkv_stem}.mkv").exists()

            # - Caso sem extensão real (ex: termina em `.WEBRip`):
            #   o stem do MKV costuma começar com o nome do arquivo gerado.
            for mkv in src.parent.glob("*.mkv"):
                if mkv.stem.startswith(src.name):
                    return True
            return False

        def on_created(self, event):
            if event.is_directory:
                return
            p = event.src_path
            lower = p.lower()
            if lower.endswith(".mkv"):
                fila.put(("mkv", p))
                return
            if self._provavel_srt_extraido(p):
                fila.put(("srt", p))

        def on_moved(self, event):
            if event.is_directory:
                return
            p = event.dest_path
            lower = p.lower()
            if lower.endswith(".mkv"):
                fila.put(("mkv", p))
                return
            if self._provavel_srt_extraido(p):
                fila.put(("srt", p))

    def worker():
        while True:
            try:
                kind, path = fila.get(timeout=1.0)
            except Empty:
                continue
            path = os.path.abspath(path)
            if not os.path.isfile(path):
                continue
            with processando:
                time.sleep(atraso_seg)
                if not os.path.isfile(path):
                    continue
                if kind == "mkv":
                    if extractor.ja_extraido(path):
                        continue
                    print(f"Novo MKV detectado: {path}")
                    try:
                        extractor.processar_mkv(path, numero_faixa=None, interativo=False)
                    except Exception as e:
                        print(f"Erro ao processar {path}: {e}")
                elif kind == "srt":
                    try:
                        extractor.traduzir_srt_extraido(path)
                    except Exception as e:
                        print(f"Erro ao traduzir SRT {path}: {e}")

    t = threading.Thread(target=worker, daemon=True)
    t.start()

    observer = Observer()
    for pasta in pastas:
        if os.path.isdir(pasta):
            observer.schedule(Handler(), pasta, recursive=True)
            print(f"Monitorando: {pasta}")
        else:
            print(f"Aviso: pasta não encontrada: {pasta}")

    observer.start()
    print("Watcher ativo. Pressione Ctrl+C para sair.")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        observer.stop()
    observer.join()


def main_interativo() -> None:
    pastas = _config("PASTAS", PASTAS_PADRAO)
    ext = MKVExtractor()

    while True:
        print("\n" + "=" * 50)
        print("EXTRATOR DE LEGENDAS MKV (SubtitleEdit para OCR/tradução)")
        print("=" * 50)
        print("1. Processar um arquivo MKV")
        print("2. Processar todos os MKVs das pastas (lote)")
        print("3. Iniciar watcher (monitorar pastas)")
        print("4. Processar todos os MKVs de uma pasta (somente essa)")
        print("5. Sair")
        op = input("\nEscolha: ").strip()

        if op == "1":
            arq = input("Caminho do arquivo MKV: ").strip()
            if arq and os.path.isfile(arq):
                ext.processar_mkv(arq, numero_faixa=None, interativo=True)
            else:
                print("Arquivo não encontrado.")
        elif op == "2":
            ext.processar_lote(pastas=pastas)
        elif op == "3":
            atraso = _config("WATCHER_ESTABILIDADE_SEGUNDOS", WATCHER_ESTABILIDADE_PADRAO)
            run_watcher(ext, pastas, atraso)
        elif op == "4":
            pasta = input("Caminho da pasta MKV: ").strip()
            if pasta:
                ext.processar_pasta(pasta)
            else:
                print("Caminho inválido.")
        elif op == "5":
            print("Até mais.")
            break
        else:
            print("Opção inválida.")
        input("\nPressione Enter para continuar...")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Extrai legendas de MKV e opcionalmente traduz em Python (Google Translate API ou LibreTranslate)."
    )
    parser.add_argument("--arquivo", "-a", help="Processar um arquivo MKV")
    parser.add_argument("--traduzir-srt", "-t", metavar="ARQUIVO", help="Só traduzir um arquivo SRT existente (salva como <nome>_PT.srt)")
    parser.add_argument("--lote", "-l", action="store_true", help="Processar pastas em lote")
    parser.add_argument("--watch", "-w", action="store_true", help="Watcher nas pastas")
    parser.add_argument("--pastas", "-p", nargs="+", default=None, help="Pastas para lote/watch")
    args = parser.parse_args()

    pastas = args.pastas or _config("PASTAS", PASTAS_PADRAO)

    # Modo: traduzir SRT já existente (não depende de MKVToolNix)
    if args.traduzir_srt:
        arquivo_entrada = os.path.abspath(args.traduzir_srt)
        if not os.path.isfile(arquivo_entrada):
            print(f"Arquivo não encontrado: {arquivo_entrada}")
            return
        # Aceita também SRT sem extensão (ex: o mkvextract pode gerar `...WEBRip`).

        idioma = _config("IDIOMA_DESTINO", "pt")
        p_entrada = Path(arquivo_entrada)
        if p_entrada.name.lower().endswith(".srt"):
            base_nome = p_entrada.stem
        else:
            # Não usar `.stem`, pois `Path` trata o texto após o último ponto como "extensão"
            # (ex: `...WEBRip` viraria `...`).
            base_nome = p_entrada.name
        arquivo_saida = str(p_entrada.parent / f"{base_nome}_PT.srt")

        print("Traduzindo SRT...")
        if traduzir_arquivo_srt(arquivo_entrada, arquivo_saida, idioma):
            print(f"Legenda traduzida: {arquivo_saida}")
        else:
            print("Falha na tradução. Verifique TRADUCAO_BACKEND e a configuração do provedor.")
        return

    ext = MKVExtractor()

    if args.arquivo:
        ext.processar_mkv(
            args.arquivo,
            numero_faixa=None,
            interativo=False,
        )
        return

    if args.lote:
        ext.processar_lote(pastas=pastas)
        return

    if args.watch:
        atraso = _config("WATCHER_ESTABILIDADE_SEGUNDOS", WATCHER_ESTABILIDADE_PADRAO)
        run_watcher(ext, pastas, atraso)
        return

    main_interativo()


if __name__ == "__main__":
    main()
