from __future__ import annotations

import contextlib
import io
import multiprocessing
import os
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from extrair_legendas import (
    WATCHER_ESTABILIDADE_PADRAO,
    MKVExtractor,
    _config,
    run_watcher,
    traduzir_arquivo_ass,
    traduzir_arquivo_srt,
)


class _QtStream(io.TextIOBase):
    def __init__(self, emit: Callable[[str], None]):
        super().__init__()
        self._emit = emit
        self._buffer = ""

    def write(self, s: str) -> int:
        self._buffer += s
        while "\n" in self._buffer:
            line, self._buffer = self._buffer.split("\n", 1)
            self._emit(line)
        return len(s)

    def flush(self) -> None:
        if self._buffer:
            self._emit(self._buffer)
            self._buffer = ""


class Worker(QThread):
    log_line = Signal(str)
    finished_ok = Signal(str)
    finished_error = Signal(str)

    def __init__(self, mode: str, data: dict, parent=None):
        super().__init__(parent)
        self.mode = mode
        self.data = data

    def run(self) -> None:
        out_stream = _QtStream(self.log_line.emit)
        err_stream = _QtStream(self.log_line.emit)
        try:
            with contextlib.redirect_stdout(out_stream), contextlib.redirect_stderr(err_stream):
                self._run_mode()
        except Exception as exc:
            self.finished_error.emit(str(exc))

    def _run_mode(self) -> None:
        mode = self.mode
        data = self.data
        ext = MKVExtractor()

        if mode == "arquivo":
            mkv_path = data["mkv_path"]
            ok = ext.processar_mkv(mkv_path, numero_faixa=None, interativo=False)
            if not ok:
                raise RuntimeError("Falha no processamento/extração do MKV.")
            self.finished_ok.emit("Processamento concluído.")
            return

        if mode == "listar":
            mkv_path = data["mkv_path"]
            ext.print_conteudo_mkv(mkv_path)
            self.finished_ok.emit("Conteúdo listado com sucesso.")
            return

        if mode == "traduzir":
            src = os.path.abspath(data["subtitle_path"])
            if not os.path.isfile(src):
                raise RuntimeError(f"Arquivo não encontrado: {src}")
            idioma = _config("IDIOMA_DESTINO", "pt")
            p = Path(src)
            suf = p.suffix.lower()
            if suf in (".ass", ".ssa"):
                out = str(p.parent / f"{p.stem}_PT.ass")
                if not traduzir_arquivo_ass(src, out, idioma):
                    raise RuntimeError("Falha na tradução ASS/SSA.")
                self.finished_ok.emit(f"Legenda traduzida: {out}")
                return

            base_nome = p.stem if p.name.lower().endswith(".srt") else p.name
            out = str(p.parent / f"{base_nome}_PT.srt")
            if not traduzir_arquivo_srt(src, out, idioma):
                raise RuntimeError("Falha na tradução SRT.")
            self.finished_ok.emit(f"Legenda traduzida: {out}")
            return

        if mode == "lote":
            pastas = data.get("pastas") or None
            ext.processar_lote(pastas=pastas)
            self.finished_ok.emit("Lote finalizado.")
            return

        if mode == "pasta":
            pasta = data["pasta"]
            ext.processar_pasta(pasta)
            self.finished_ok.emit("Processamento da pasta finalizado.")
            return

        raise RuntimeError("Modo inválido.")


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Tradutor de Legendas")
        self.resize(930, 620)
        self._worker: Worker | None = None
        self._watcher_process: multiprocessing.Process | None = None

        root = QWidget()
        layout = QVBoxLayout(root)

        mode_row = QHBoxLayout()
        mode_row.addWidget(QLabel("Modo"))
        self.mode_combo = QComboBox()
        self.mode_combo.addItem("Processar arquivo MKV", "arquivo")
        self.mode_combo.addItem("Listar conteúdo de MKV (faixas/anexos/capítulos/tags)", "listar")
        self.mode_combo.addItem("Traduzir arquivo de legenda", "traduzir")
        self.mode_combo.addItem("Processar em lote (pastas do config ou custom)", "lote")
        self.mode_combo.addItem("Processar pasta específica", "pasta")
        self.mode_combo.addItem("Watcher (monitoramento contínuo)", "watcher")
        self.mode_combo.currentIndexChanged.connect(self._refresh_mode_ui)
        mode_row.addWidget(self.mode_combo)
        layout.addLayout(mode_row)

        form = QFormLayout()

        self.file_edit = QLineEdit()
        self.file_edit.setPlaceholderText("Selecione um arquivo .mkv")
        file_row = QHBoxLayout()
        file_row.addWidget(self.file_edit)
        self.pick_file_button = QPushButton("Selecionar MKV")
        self.pick_file_button.clicked.connect(self.pick_file)
        file_row.addWidget(self.pick_file_button)
        self.file_widget = QWidget()
        self.file_widget.setLayout(file_row)
        form.addRow("Arquivo MKV", self.file_widget)

        self.subtitle_edit = QLineEdit()
        self.subtitle_edit.setPlaceholderText("Selecione .srt, .ass ou .ssa")
        subtitle_row = QHBoxLayout()
        subtitle_row.addWidget(self.subtitle_edit)
        self.pick_sub_button = QPushButton("Selecionar legenda")
        self.pick_sub_button.clicked.connect(self.pick_subtitle)
        subtitle_row.addWidget(self.pick_sub_button)
        self.subtitle_widget = QWidget()
        self.subtitle_widget.setLayout(subtitle_row)
        form.addRow("Arquivo legenda", self.subtitle_widget)

        self.lote_pastas_edit = QLineEdit()
        self.lote_pastas_edit.setPlaceholderText("Opcional: /pasta/um,/pasta/dois (vazio = usa config.py)")
        form.addRow("Pastas lote", self.lote_pastas_edit)

        self.pasta_edit = QLineEdit()
        self.pasta_edit.setPlaceholderText("Selecione uma pasta")
        pasta_row = QHBoxLayout()
        pasta_row.addWidget(self.pasta_edit)
        self.pick_pasta_button = QPushButton("Selecionar pasta")
        self.pick_pasta_button.clicked.connect(self.pick_folder)
        pasta_row.addWidget(self.pick_pasta_button)
        self.pasta_widget = QWidget()
        self.pasta_widget.setLayout(pasta_row)
        form.addRow("Pasta específica", self.pasta_widget)

        self.watch_pastas_edit = QLineEdit()
        self.watch_pastas_edit.setPlaceholderText("Opcional: /pasta/um,/pasta/dois (vazio = usa config.py)")
        form.addRow("Pastas watcher", self.watch_pastas_edit)

        layout.addLayout(form)

        self.run_button = QPushButton("Processar")
        self.run_button.clicked.connect(self.run_processing)
        layout.addWidget(self.run_button)

        self.stop_watcher_button = QPushButton("Parar watcher")
        self.stop_watcher_button.clicked.connect(self.stop_watcher)
        self.stop_watcher_button.setEnabled(False)
        layout.addWidget(self.stop_watcher_button)

        self.log = QTextEdit()
        self.log.setReadOnly(True)
        layout.addWidget(self.log)

        self.setCentralWidget(root)
        self._refresh_mode_ui()

    def _refresh_mode_ui(self) -> None:
        mode = self.mode_combo.currentData()
        self.file_widget.setVisible(mode in ("arquivo", "listar"))
        self.subtitle_widget.setVisible(mode == "traduzir")
        self.lote_pastas_edit.setVisible(mode == "lote")
        self.pasta_widget.setVisible(mode == "pasta")
        self.watch_pastas_edit.setVisible(mode == "watcher")
        self.stop_watcher_button.setVisible(mode == "watcher")

        if mode == "watcher":
            self.run_button.setText("Iniciar watcher")
        elif mode == "listar":
            self.run_button.setText("Listar conteúdo")
        elif mode == "traduzir":
            self.run_button.setText("Traduzir")
        else:
            self.run_button.setText("Executar")

    def pick_file(self) -> None:
        file_path, _ = QFileDialog.getOpenFileName(self, "Selecionar MKV", str(Path.home()), "Vídeos MKV (*.mkv)")
        if file_path:
            self.file_edit.setText(file_path)

    def pick_subtitle(self) -> None:
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Selecionar legenda",
            str(Path.home()),
            "Legendas (*.srt *.ass *.ssa);;Todos os arquivos (*)",
        )
        if file_path:
            self.subtitle_edit.setText(file_path)

    def pick_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Selecionar pasta", str(Path.home()))
        if folder:
            self.pasta_edit.setText(folder)

    def run_processing(self) -> None:
        mode = self.mode_combo.currentData()

        if mode == "watcher":
            self.start_watcher()
            return

        payload = self._payload_from_ui(mode)
        if payload is None:
            return

        self.log.append(f"Iniciando modo: {mode}")
        self.run_button.setEnabled(False)
        self._worker = Worker(mode, payload)
        self._worker.log_line.connect(self.on_log_line)
        self._worker.finished_ok.connect(self.on_success)
        self._worker.finished_error.connect(self.on_error)
        self._worker.start()

    def _payload_from_ui(self, mode: str) -> dict | None:
        if mode in ("arquivo", "listar"):
            mkv_path = self.file_edit.text().strip()
            if not mkv_path or not Path(mkv_path).is_file():
                QMessageBox.warning(self, "Atenção", "Selecione um arquivo MKV válido.")
                return None
            return {"mkv_path": mkv_path}

        if mode == "traduzir":
            subtitle = self.subtitle_edit.text().strip()
            if not subtitle or not Path(subtitle).is_file():
                QMessageBox.warning(self, "Atenção", "Selecione um arquivo de legenda válido.")
                return None
            return {"subtitle_path": subtitle}

        if mode == "lote":
            return {"pastas": self._parse_csv_paths(self.lote_pastas_edit.text().strip())}

        if mode == "pasta":
            pasta = self.pasta_edit.text().strip()
            if not pasta or not Path(pasta).is_dir():
                QMessageBox.warning(self, "Atenção", "Selecione uma pasta válida.")
                return None
            return {"pasta": pasta}

        return None

    def _parse_csv_paths(self, raw: str) -> list[str] | None:
        if not raw:
            return None
        parts = [p.strip() for p in raw.split(",") if p.strip()]
        return parts or None

    def _watcher_target(self, pastas: list[str] | None) -> None:
        ext = MKVExtractor()
        watch_paths = pastas or _config("PASTAS", [])
        atraso = _config("WATCHER_ESTABILIDADE_SEGUNDOS", WATCHER_ESTABILIDADE_PADRAO)
        run_watcher(ext, watch_paths, atraso)

    def start_watcher(self) -> None:
        if self._watcher_process and self._watcher_process.is_alive():
            QMessageBox.information(self, "Watcher", "Watcher já está em execução.")
            return
        pastas = self._parse_csv_paths(self.watch_pastas_edit.text().strip())
        self._watcher_process = multiprocessing.Process(target=self._watcher_target, args=(pastas,), daemon=True)
        self._watcher_process.start()
        self.stop_watcher_button.setEnabled(True)
        self.log.append("Watcher iniciado.")

    def stop_watcher(self) -> None:
        if not self._watcher_process or not self._watcher_process.is_alive():
            self.log.append("Watcher não está em execução.")
            self.stop_watcher_button.setEnabled(False)
            return
        self._watcher_process.terminate()
        self._watcher_process.join(timeout=3)
        self.stop_watcher_button.setEnabled(False)
        self.log.append("Watcher parado.")

    def on_log_line(self, line: str) -> None:
        if line.strip():
            self.log.append(line)

    def on_success(self, message: str) -> None:
        self.run_button.setEnabled(True)
        self.log.append(message)
        self.log.append("")

    def on_error(self, message: str) -> None:
        self.run_button.setEnabled(True)
        self.log.append(f"Erro: {message}")
        self.log.append("")

    def closeEvent(self, event) -> None:  # type: ignore[override]
        if self._watcher_process and self._watcher_process.is_alive():
            self._watcher_process.terminate()
            self._watcher_process.join(timeout=2)
        super().closeEvent(event)
