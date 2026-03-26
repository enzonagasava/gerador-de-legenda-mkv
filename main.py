from __future__ import annotations

import argparse
import sys

from PySide6.QtWidgets import QApplication

from desktop_app.main_window import MainWindow


def main() -> int:
    parser = argparse.ArgumentParser(description="Tradutor de legendas - app desktop")
    parser.add_argument("--cli", action="store_true", help="Executa o modo CLI legado")
    args, remaining_args = parser.parse_known_args()

    if args.cli:
        from extrair_legendas import main as cli_main

        # Repassa apenas os argumentos do CLI legado, removendo o flag do launcher.
        sys.argv = [sys.argv[0], *remaining_args]
        cli_main()
        return 0

    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
