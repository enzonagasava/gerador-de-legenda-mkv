#!/usr/bin/env python3
"""
Extrai legendas de arquivos MKV para SRT/ASS/SUP usando mkvtoolnix.
Opcionalmente traduz o SRT em Python (LibreTranslate).
Legendas em imagem (PGS/SUP): use SubtitleEdit para OCR, ou extraia e traduza só se já for texto.
"""

import os
import sys
import re
import shutil
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
    import config
except ImportError:
    config = None

try:
    import pysubs2
except ImportError:
    pysubs2 = None  # type: ignore

PASTAS_PADRAO = [
    "/media/enzo/backup-sdb2/torrents",
    "/media/enzo/backup-sdb2/series",
]
WATCHER_ESTABILIDADE_PADRAO = 5


def _config(key: str, default):
    # Override por variáveis de ambiente (por job, especialmente via Celery).
    env_val = os.environ.get(key)
    if env_val is not None:
        try:
            if isinstance(default, bool):
                return env_val.strip().lower() in ("1", "true", "yes", "sim", "on")
            if isinstance(default, int):
                return int(env_val)
            if isinstance(default, float):
                return float(env_val)
        except Exception:
            # Em caso de falha de conversão, mantém como string.
            pass
        return env_val

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
    url = _config("LIBRETRANSLATE_URL", "https://de.libretranslate.com/translate").rstrip("/")
    if not url.endswith("/translate"):
        url = f"{url}/translate" if "/translate" not in url else url
    target = (idioma_destino or "").strip()
    # LibreTranslate/Argos costuma expor Português do Brasil como "pt-BR".
    # Mantemos compatibilidade com config antigo ("pt").
    if target.lower() in ("pt", "pt_br", "pt-br"):
        target = "pt-BR"
    payload = {"q": texto, "source": "auto", "target": target, "format": "text"}
    r = session.post(url, data=payload, timeout=30)
    r.raise_for_status()
    if not r.content:
        raise RuntimeError("Resposta vazia da API de tradução.")
    try:
        j = r.json()
    except ValueError as exc:
        sample = (r.text or "").strip().replace("\n", " ")[:180]
        raise RuntimeError(
            f"Resposta invalida da API (status {r.status_code}, content-type: {r.headers.get('content-type', '')}): {sample}"
        ) from exc
    out = j.get("translatedText") or j.get("translation") or j.get("translated_text") or texto
    return str(out).strip() or texto

def traduzir_arquivo_srt(arquivo_entrada: str, arquivo_saida: str, idioma_destino: str) -> bool:
    """Traduz um arquivo SRT e salva em arquivo_saida. Retorna True se ok."""
    if requests is None:
        print("Tradução requer: pip install requests")
        return False
    backend = str(_config("TRADUCAO_BACKEND", "libretranslate")).lower().strip()
    if backend == "none":
        return False
    if backend != "libretranslate":
        print(f"Aviso: TRADUCAO_BACKEND '{backend}' não suportado; usando libretranslate.")
    backend = "libretranslate"
    blocos = _parse_srt_blocks(arquivo_entrada)
    if not blocos:
        return False
    session = requests.Session()

    with open(arquivo_saida, "w", encoding="utf-8") as f:
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


# Regex ASS: mesmo padrão de `SSAEvent.OVERRIDE_SEQUENCE` (pysubs2).
_ASS_OVERRIDE_RE = re.compile(r"{[^}]*}")


def _ass_plain_segment(segment: str) -> str:
    """Texto visível de um segmento de linha ASS (sem tags; \\h vira espaço)."""
    t = _ASS_OVERRIDE_RE.sub("", segment)
    t = t.replace(r"\h", " ")
    t = t.replace(r"\n", "\n")
    return t


def _ass_leading_tags(segment: str) -> Tuple[str, str]:
    """Tags `{...}` no início do segmento antes do texto visível."""
    i = 0
    lead = ""
    while i < len(segment) and segment[i] == "{":
        j = segment.find("}", i)
        if j == -1:
            break
        lead += segment[i : j + 1]
        i = j + 1
    return lead, segment[i:]


def traduzir_arquivo_ass(arquivo_entrada: str, arquivo_saida: str, idioma_destino: str) -> bool:
    """Traduz um arquivo ASS/SSA e salva em `arquivo_saida` (normalmente `.ass`).

    Preserva cabeçalho/estilos do arquivo. Traduz apenas linhas ``Dialogue`` (não ``Comment``).
    Ignora linhas de desenho (drawings). Tags inline complexas podem ser simplificadas; tags
    no início de cada segmento (entre ``\\N``) são preservadas quando possível.
    """
    if pysubs2 is None:
        print("Erro: tradução ASS requer pysubs2. Execute: pip install pysubs2")
        return False
    if requests is None:
        print("Tradução requer: pip install requests")
        return False
    backend = str(_config("TRADUCAO_BACKEND", "libretranslate")).lower().strip()
    if backend == "none":
        return False
    if backend != "libretranslate":
        print(f"Aviso: TRADUCAO_BACKEND '{backend}' não suportado; usando libretranslate.")
    backend = "libretranslate"

    try:
        subs = pysubs2.load(arquivo_entrada, encoding="utf-8-sig")
    except Exception as e:
        print(f"Erro ao carregar ASS/SSA: {e}")
        return False

    # ei -> lista de dicionários por segmento (separados por \\N)
    work: Dict[int, List[Dict[str, str]]] = {}
    for ei, ev in enumerate(subs.events):
        if ev.is_comment:
            continue
        if ev.is_drawing:
            continue
        if not (ev.text or "").strip():
            continue
        parts = ev.text.split("\\N")
        segs: List[Dict[str, str]] = []
        for part in parts:
            lead, rest = _ass_leading_tags(part)
            plain = _ass_plain_segment(rest).strip()
            segs.append({"lead": lead, "rest": rest, "plain": plain})
        work[ei] = segs

    flat: List[str] = []
    flat_refs: List[Tuple[int, int]] = []
    for ei, segs in work.items():
        for si, seg in enumerate(segs):
            if seg["plain"]:
                flat.append(seg["plain"])
                flat_refs.append((ei, si))

    if not flat:
        print("Nada para traduzir (sem texto legível em linhas Dialogue).")
        return False

    session = requests.Session()
    translated: List[str] = []

    for texto in flat:
        if not texto.strip():
            translated.append(texto)
            continue
        try:
            translated.append(_traduzir_texto_libretranslate(texto, idioma_destino, session) or texto)
        except Exception as e:
            print(f"Erro tradução: {e}")
            translated.append(texto)

    if len(translated) != len(flat):
        print("Erro: tradução ASS inconsistente (quantidade de linhas).")
        return False

    for idx, tr in enumerate(translated):
        ei, si = flat_refs[idx]
        seg = work[ei][si]
        tr = (tr or seg["plain"]).replace("\r\n", "\n").replace("\r", "\n")
        tr = tr.replace("\n", " ").strip()
        if not tr:
            tr = seg["plain"]
        seg["translated"] = tr

    for ei, segs in work.items():
        out_parts: List[str] = []
        for seg in segs:
            plain = seg.get("plain") or ""
            if not plain:
                out_parts.append(seg["lead"] + seg["rest"])
            else:
                tr = seg.get("translated", plain)
                out_parts.append(seg["lead"] + tr)
        subs.events[ei].text = "\\N".join(out_parts)

    try:
        subs.save(arquivo_saida, encoding="utf-8", format_="ass")
    except Exception as e:
        print(f"Erro ao salvar ASS: {e}")
        return False
    return True


class MKVExtractor:
    """Extrai faixas de legenda de MKV usando mkvtoolnix.

    Tradução de SRT e ASS/SSA é opcional (dependendo de `TRADUCAO_BACKEND`).
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

    def _mkv_tem_srt_ptbr(self, arquivo_mkv: str) -> bool:
        """
        Retorna True se o MKV já contiver uma faixa de legenda SubRip/SRT em pt-BR.
        Usado para evitar gerar/traduzir legendas duplicadas e evitar mux repetido.
        """
        try:
            faixas = self.listar_faixas(arquivo_mkv)
        except Exception:
            return False

        for f in faixas:
            codec = (f.get("codec") or "").upper()
            idioma = (f.get("idioma") or "").strip().lower()
            if "SUBRIP" not in codec and "SRT" not in codec:
                continue
            # mkvinfo pode retornar ISO-639-2 ("por") ou BCP47 ("pt-BR").
            if idioma in ("por", "pt", "pt-br", "pt_br", "ptbr") or idioma.startswith("pt-") or "portugu" in idioma:
                return True
        return False

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
        pt_ass_legado = diretorio / f"{base}_PT.ass"
        if pt_ass_legado.exists():
            return True

        if backend != "none":
            try:
                for p in diretorio.iterdir():
                    if not p.is_file():
                        continue
                    suf = p.suffix.lower()
                    if suf == ".srt":
                        # Ex.: Bluey.S01E51..._faixa2_PT.srt
                        if p.name.startswith(base + "_faixa") and p.name.endswith("_PT.srt"):
                            return True
                    elif suf == ".ass":
                        if p.name.startswith(base + "_faixa") and p.name.endswith("_PT.ass"):
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

            # Evita que os arquivos gerados em /subtitles fiquem como "root:root".
            # O seconv dentro do container tende a rodar como root por padrão; ao escrever
            # no volume montado, isso acaba refletindo como ownership/permissões restritas.
            try:
                uid = os.getuid()
                gid = os.getgid()
            except AttributeError:
                uid = None
                gid = None

            has_user_flag = any(
                p in ("--user", "-u") or p.startswith("--user=") or p.startswith("-u=")
                for p in docker_run_parts
            )
            if uid is not None and gid is not None and not has_user_flag:
                docker_run_parts = ["--user", f"{uid}:{gid}", *docker_run_parts]

            has_workdir_flag = any(
                p in ("--workdir", "-w") or p.startswith("--workdir=") or p.startswith("-w=")
                for p in docker_run_parts
            )
            if not has_workdir_flag:
                docker_run_parts = ["-w", "/subtitles", *docker_run_parts]

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
            # Modo local: tenta localizar `seconv` no PATH e em caminhos comuns.
            seconv_bin = shutil.which("seconv")
            if not seconv_bin:
                for candidate in (
                    os.path.expanduser("~/.cargo/bin/seconv"),
                    os.path.expanduser("~/.local/bin/seconv"),
                ):
                    if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
                        seconv_bin = candidate
                        break
            if not seconv_bin:
                print("Erro: seconv não encontrado no PATH. Instale o subtitleedit-cli localmente.")
                return None
            ocr_db = str(_config("SECONV_OCR_DB", "Latin")).strip() or "Latin"
            seconv_actions = []
            if str(_config("SECONV_APPLY_MERGE_ACTIONS", True)).lower() in ("1", "true", "yes", "sim"):
                seconv_actions = ["/MergeSameTimeCodes", "/MergeSameTexts"]
            cmd = [seconv_bin, sup.name, out_format, f"/ocrdb:{ocr_db}", *seconv_actions]
            cpuset = str(_config("SECONV_CPUSET", "")).strip()
            if cpuset:
                taskset_bin = shutil.which("taskset")
                if taskset_bin:
                    cmd = [taskset_bin, "-c", cpuset, *cmd]
                else:
                    print("Aviso: SECONV_CPUSET definido, mas `taskset` não está disponível; ignorando limite de CPU.")

        try:
            print(f"[OCR] Executando seconv ({mode}) para: {sup.name}")
            run_kwargs = {"capture_output": True, "text": True, "encoding": "utf-8"}
            if mode == "local":
                # Para o modo local, garante resolução correta do arquivo e saída ao lado do .sup.
                r = subprocess.run(cmd, cwd=str(out_dir), **run_kwargs)
            else:
                r = subprocess.run(cmd, **run_kwargs)
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

    def _pt_ass_destino_para_legenda_extraida(self, arquivo: Path) -> Path:
        """
        Destino PT para ASS/SSA extraído (sempre `.ass`).
        Ex.: `Filme_faixa3.ass` ou `Filme_faixa3.ssa` -> `Filme_faixa3_PT.ass`.
        """
        return arquivo.parent / f"{arquivo.stem}_PT.ass"

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

    def traduzir_ass_extraido(self, arquivo_ass: str) -> bool:
        """
        Traduz um ASS/SSA extraído do MKV para `*_PT.ass`.
        """
        src = Path(arquivo_ass)
        if not src.is_file():
            return False
        if src.stem.endswith("_PT") or "_PT.ass" in src.name:
            return False
        if src.suffix.lower() not in (".ass", ".ssa"):
            return False

        def _looks_like_ass(path_base: Path) -> bool:
            try:
                if not path_base.exists() or not path_base.is_file():
                    return False
                with open(path_base, "r", encoding="utf-8-sig", errors="ignore") as f:
                    head = f.read(4000)
                return ("[Script Info]" in head or "[V4+ Styles]" in head or "[Events]" in head) and (
                    "Dialogue:" in head or "dialogue:" in head.lower()
                )
            except Exception:
                return False

        if not _looks_like_ass(src):
            return False

        idioma = _config("IDIOMA_DESTINO", "pt")
        backend = str(_config("TRADUCAO_BACKEND", "libretranslate")).lower().strip()
        if backend == "none":
            return False

        destino = self._pt_ass_destino_para_legenda_extraida(src)
        if destino.exists():
            return True

        print(f"Traduzindo ASS/SSA extraído: {src} -> {destino}")
        try:
            return bool(traduzir_arquivo_ass(str(src), str(destino), idioma))
        except Exception as e:
            print(f"Erro ao traduzir ASS: {e}")
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

        # Regra (Opção 2): se o MKV já possui uma faixa SubRip/SRT em pt-BR,
        # não gera/traduz nova legenda e não faz mux para evitar duplicatas.
        try:
            if self._mkv_tem_srt_ptbr(arquivo_mkv):
                print("MKV já possui uma legenda SubRip/SRT em pt-BR — pulando tradução e mux.")
                return True
        except Exception:
            # Se a detecção falhar, segue fluxo normal.
            pass

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
        srt_pt_gerados: List[str] = []
        srt_origem_para_limpeza: List[str] = []
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
                srt_origem_para_limpeza.append(path_extraido)
                print(f"Traduzindo faixa {num}...")
                # Mesmo que a tradução falhe ou já exista, tentamos detectar o destino PT.
                try:
                    destino_pt = self._pt_srt_destino_para_srt_extraido(Path(path_extraido))
                    if destino_pt.is_file():
                        srt_pt_gerados.append(str(destino_pt))
                except Exception:
                    destino_pt = None

                if self.traduzir_srt_extraido(path_extraido):
                    try:
                        destino_pt = self._pt_srt_destino_para_srt_extraido(Path(path_extraido))
                        if destino_pt.is_file():
                            srt_pt_gerados.append(str(destino_pt))
                    except Exception:
                        pass
                    print(f"Legenda traduzida (faixa {num}).")
                else:
                    print(f"Falha na tradução da faixa {num}.")
                continue

            # Caso 1b: legenda textual ASS/SSA.
            if path_extraido.lower().endswith((".ass", ".ssa")):
                print(f"Traduzindo faixa {num} (ASS/SSA)...")
                if self.traduzir_ass_extraido(path_extraido):
                    print(f"Legenda traduzida (faixa {num}).")
                else:
                    print(f"Falha na tradução da faixa {num}.")
                continue

            # Caso 2: legenda em imagem (SUP): OCR + tradução.
            if path_extraido.lower().endswith(".sup"):
                srt_ocr = self._ocr_sup_via_seconv(path_extraido)

                if not srt_ocr or not os.path.isfile(srt_ocr):
                    print(f"Falha no OCR automático da faixa {num}.")
                    continue
                srt_origem_para_limpeza.append(str(srt_ocr))

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
                    if destino_pt.is_file():
                        srt_pt_gerados.append(str(destino_pt))
                else:
                    print(f"Falha na tradução da faixa {num} após OCR.")
                    if destino_pt.is_file():
                        srt_pt_gerados.append(str(destino_pt))
                continue

        # Ao final do MKV, opcionalmente embute as legendas PT geradas como novas faixas.
        if algum_sucesso and srt_pt_gerados and str(_config("EMBUTIR_SRT_NO_MKV", False)).lower() in ("1", "true", "yes", "sim"):
            try:
                # Escolhe apenas o MAIOR SRT (normalmente evita incluir legenda curta de abertura).
                srt_pt_para_limpeza = list(srt_pt_gerados)
                unique: List[Path] = []
                seen = set()
                for p in srt_pt_gerados:
                    try:
                        sp = Path(p)
                        if not sp.is_file():
                            continue
                        key = str(sp.resolve(strict=False))
                        if key in seen:
                            continue
                        seen.add(key)
                        unique.append(sp)
                    except Exception:
                        continue

                if unique:
                    unique.sort(key=lambda x: x.stat().st_size if x.exists() else 0, reverse=True)
                    srt_pt_gerados = [str(unique[0])]

                out_suffix = str(_config("MKV_MUX_SUFFIX", "_COM_LEGENDA")).strip() or "_COM_LEGENDA"
                set_default = str(_config("MKV_MUX_SET_DEFAULT", False)).lower() in ("1", "true", "yes", "sim", "on")
                replace_mode = str(_config("MKV_MUX_REPLACE", False)).lower() in ("1", "true", "yes", "sim", "on")
                out_mkv_path = base.with_name(base.stem + out_suffix + base.suffix)
                if out_mkv_path.exists():
                    # Evita sobrescrever: cria sufixo incremental simples.
                    i = 2
                    while True:
                        candidate = base.with_name(base.stem + out_suffix + f"_{i}" + base.suffix)
                        if not candidate.exists():
                            out_mkv_path = candidate
                            break
                        i += 1

                final_out_mkv_path = out_mkv_path
                if replace_mode:
                    # Cria mux em arquivo temporário e depois faz swap.
                    final_out_mkv_path = base.with_name(base.stem + out_suffix + "_TMP" + base.suffix)
                self._mux_srt_no_mkv(
                    arquivo_mkv,
                    srt_pt_gerados,
                    str(final_out_mkv_path),
                    idioma_destino=idioma,
                    set_default=set_default,
                )
                if replace_mode:
                    self._replace_mkv_preserving_original(
                        original_mkv=str(base),
                        muxed_mkv=str(final_out_mkv_path),
                        suffix=out_suffix,
                    )

                # Limpeza opcional: remove os SRTs PT gerados após mux bem-sucedido.
                if str(_config("APAGAR_SRT_PT_APOS_MUX", True)).lower() in ("1", "true", "yes", "sim", "on"):
                    removed = 0
                    for p in srt_pt_para_limpeza:
                        try:
                            sp = Path(p)
                            if sp.is_file():
                                sp.unlink()
                                removed += 1
                        except Exception:
                            continue
                    if removed:
                        print(f"[MUX] SRTs PT removidos após mux: {removed}")

                # Limpeza opcional: remove SRTs originais (extraídos/ocr merged) após mux.
                if str(_config("APAGAR_SRT_ORIGINAL_APOS_MUX", True)).lower() in ("1", "true", "yes", "sim", "on"):
                    removed = 0
                    seen = set()
                    for p in srt_origem_para_limpeza:
                        try:
                            sp = Path(p)
                            key = str(sp.resolve(strict=False))
                            if key in seen:
                                continue
                            seen.add(key)
                            if sp.is_file():
                                sp.unlink()
                                removed += 1
                        except Exception:
                            continue
                    if removed:
                        print(f"[MUX] SRTs originais removidos após mux: {removed}")

                    # Também remove quaisquer SRTs de faixas do MKV atual que ainda restarem
                    # (ex.: <stem>_faixa3.srt), para evitar arquivos soltos.
                    try:
                        extra_removed = 0
                        for p in base.parent.glob(f"{base.stem}_faixa*.srt"):
                            try:
                                if p.is_file():
                                    p.unlink()
                                    extra_removed += 1
                            except Exception:
                                continue
                        if extra_removed:
                            print(f"[MUX] SRTs de faixas removidos após mux: {extra_removed}")
                    except Exception:
                        pass
            except Exception as e:
                print(f"Aviso: falha ao embutir legendas no MKV: {e}")

        if backend == "none" and algum_sucesso:
            print("Próximo passo: abra no SubtitleEdit → Ferramentas → Auto-traduzir → salve como *_PT.srt")
        return algum_sucesso

    def _mux_srt_no_mkv(
        self,
        mkv_path: str,
        srt_paths: List[str],
        out_mkv_path: str,
        idioma_destino: str,
        set_default: bool = False,
    ) -> None:
        mkv = Path(mkv_path)
        out_mkv = Path(out_mkv_path)
        if not mkv.is_file():
            raise FileNotFoundError(f"MKV não encontrado: {mkv_path}")

        srts = []
        for p in srt_paths:
            try:
                sp = Path(p)
                if sp.is_file():
                    srts.append(sp)
            except Exception:
                continue
        if not srts:
            raise FileNotFoundError("Nenhum SRT PT encontrado para mux.")

        lang = (idioma_destino or "").strip() or "pt-BR"
        if lang.lower() in ("pt", "pt_br", "pt-br"):
            lang = "pt-BR"

        cmd: List[str] = ["mkvmerge", "-o", str(out_mkv), str(mkv)]
        for srt in srts:
            # opções aplicam ao próximo arquivo
            cmd += [
                "--language",
                f"0:{lang}",
                "--track-name",
                "0:Português",
                "--default-track",
                f"0:{'yes' if set_default else 'no'}",
                str(srt),
            ]

        print(f"[MUX] Criando MKV com legenda embutida: {out_mkv}")
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
        if r.returncode != 0:
            raise RuntimeError(f"mkvmerge falhou: {r.stderr or r.stdout}")

    def _replace_mkv_preserving_original(self, original_mkv: str, muxed_mkv: str, suffix: str) -> None:
        """
        Faz o muxado assumir o nome do original, preservando o original via move/rename.
        Nunca deleta o original.
        """
        original = Path(original_mkv)
        muxed = Path(muxed_mkv)
        if not original.is_file():
            raise FileNotFoundError(f"MKV original não encontrado: {original_mkv}")
        if not muxed.is_file():
            raise FileNotFoundError(f"MKV muxado não encontrado: {muxed_mkv}")

        move_enabled = str(_config("MKV_ORIGINAL_MOVE_ENABLED", False)).lower() in ("1", "true", "yes", "sim", "on")
        move_dir = str(_config("MKV_ORIGINAL_MOVE_DIR", "")).strip()

        # Decide destino do original
        dest_original: Path
        used_fallback = False
        if move_enabled and move_dir:
            try:
                dest_dir = Path(move_dir).expanduser()
                dest_dir.mkdir(parents=True, exist_ok=True)
                dest_original = dest_dir / original.name
                if dest_original.exists():
                    i = 2
                    while True:
                        cand = dest_dir / f"{original.stem}_{i}{original.suffix}"
                        if not cand.exists():
                            dest_original = cand
                            break
                        i += 1
            except Exception as e:
                used_fallback = True
                print(
                    f"Aviso: não foi possível criar/usar MKV_ORIGINAL_MOVE_DIR='{move_dir}' ({e}). "
                    "Usando fallback para renomear o original ao lado."
                )
        else:
            used_fallback = True

        if used_fallback:
            # fallback local: renomeia ao lado para não sobrescrever
            dest_original = original.with_name(original.stem + suffix + "_ORIGINAL" + original.suffix)
            if dest_original.exists():
                i = 2
                while True:
                    cand = original.with_name(original.stem + suffix + f"_ORIGINAL_{i}" + original.suffix)
                    if not cand.exists():
                        dest_original = cand
                        break
                    i += 1

        print(f"[MUX] Movendo MKV original para: {dest_original}")
        shutil.move(str(original), str(dest_original))

        print(f"[MUX] Substituindo MKV original pelo muxado: {original}")
        shutil.move(str(muxed), str(original))

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
    traduz a legenda gerada (SRT ou ASS/SSA).

    Regra 2: se um `.srt` extraído (padrão `*_faixaN.srt`) surgir, traduz esse
    SRT para `*_PT.srt` automaticamente (caso já tenha sido extraído por outro
    processo).

    Regra 3: idem para `*_faixaN.ass` / `.ssa` → `*_PT.ass`.
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

        def _provavel_ass_extraido(self, p: str) -> bool:
            """`.ass`/`.ssa` no padrão `*_faixaN.*` com MKV correspondente no mesmo diretório."""
            src = Path(p)
            if not src.is_file():
                return False
            if "_PT" in src.name:
                return False
            if src.suffix.lower() not in (".ass", ".ssa"):
                return False
            mkv_stem = src.stem
            m = re.match(r"^(.*)_faixa\d+$", src.stem)
            if m:
                mkv_stem = m.group(1)
            return (src.parent / f"{mkv_stem}.mkv").exists()

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
                return
            if self._provavel_ass_extraido(p):
                fila.put(("ass", p))

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
                return
            if self._provavel_ass_extraido(p):
                fila.put(("ass", p))

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
                elif kind == "ass":
                    try:
                        extractor.traduzir_ass_extraido(path)
                    except Exception as e:
                        print(f"Erro ao traduzir ASS {path}: {e}")

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
        description="Extrai legendas de MKV e opcionalmente traduz em Python (LibreTranslate)."
    )
    parser.add_argument("--arquivo", "-a", help="Processar um arquivo MKV")
    parser.add_argument(
        "--traduzir-srt",
        "-t",
        metavar="ARQUIVO",
        help="Só traduzir um arquivo de legenda existente: .srt → <nome>_PT.srt; .ass/.ssa → <nome>_PT.ass",
    )
    parser.add_argument("--lote", "-l", action="store_true", help="Processar pastas em lote")
    parser.add_argument("--watch", "-w", action="store_true", help="Watcher nas pastas")
    parser.add_argument("--pastas", "-p", nargs="+", default=None, help="Pastas para lote/watch")
    args = parser.parse_args()

    pastas = args.pastas or _config("PASTAS", PASTAS_PADRAO)

    # Modo: traduzir legenda já existente (não depende de MKVToolNix)
    if args.traduzir_srt:
        arquivo_entrada = os.path.abspath(args.traduzir_srt)
        if not os.path.isfile(arquivo_entrada):
            print(f"Arquivo não encontrado: {arquivo_entrada}")
            return

        idioma = _config("IDIOMA_DESTINO", "pt")
        p_entrada = Path(arquivo_entrada)
        suf = p_entrada.suffix.lower()

        if suf in (".ass", ".ssa"):
            arquivo_saida = str(p_entrada.parent / f"{p_entrada.stem}_PT.ass")
            print("Traduzindo ASS/SSA...")
            if traduzir_arquivo_ass(arquivo_entrada, arquivo_saida, idioma):
                print(f"Legenda traduzida: {arquivo_saida}")
            else:
                print("Falha na tradução. Verifique TRADUCAO_BACKEND, pysubs2 (pip install pysubs2) e o provedor.")
            return

        # SRT (aceita também SRT sem extensão: ex. mkvextract gera `...WEBRip`).
        if p_entrada.name.lower().endswith(".srt"):
            base_nome = p_entrada.stem
        else:
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
