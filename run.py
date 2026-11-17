from __future__ import annotations

import sys
from pathlib import Path
import traceback

if sys.platform != "win32":
    raise SystemExit("此原型目前仅支持 Windows。")

from map_overlay.native import enable_dpi_awareness
enable_dpi_awareness()

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication
from map_overlay.app import MainWindow, STYLE


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("循图")
    app.setOrganizationName("LocalRouteOverlay")
    app.setStyle("Fusion")
    app.setStyleSheet(STYLE)

    def report_exception(kind, value, trace):
        from PySide6.QtWidgets import QMessageBox
        folder = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
        log = folder / "logs" / "crash.log"
        try:
            log.parent.mkdir(exist_ok=True)
            with log.open("a", encoding="utf-8") as file:
                traceback.print_exception(kind, value, trace, file=file)
            message = f"{value}\n\n错误详情：{log}"
        except OSError:
            message = str(value)
        QMessageBox.critical(None, "循图发生错误", message)

    sys.excepthook = report_exception
    window = MainWindow()
    window.show()
    if len(sys.argv) > 1 and Path(sys.argv[1]).suffix.lower() == ".routepack":
        QTimer.singleShot(100, lambda: window.open_project(sys.argv[1]))
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
