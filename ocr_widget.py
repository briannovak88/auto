"""Интерфейс OCR и управление QProcess. Распознавание выполняется вне GUI."""
import json
import shutil
import sys
import tempfile
from pathlib import Path

from PyQt6.QtCore import QProcess, QProcessEnvironment, QUrl, Qt, pyqtSignal
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import (
    QAbstractItemView, QApplication, QCheckBox, QFileDialog, QGridLayout,
    QGroupBox, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMessageBox,
    QPlainTextEdit, QProgressBar, QPushButton, QSpinBox, QSplitter,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)


def worker_command(request: Path):
    if getattr(sys, "frozen", False):
        return sys.executable, ["--ocr-worker", str(request)]
    return sys.executable, [str(Path(__file__).resolve().with_name("main.py")), "--ocr-worker", str(request)]


class OcrWidget(QWidget):
    stopped = pyqtSignal()

    def __init__(self, paths, parent=None):
        super().__init__(parent)
        self.paths = paths
        self.process = QProcess(self)
        self.process.readyReadStandardOutput.connect(self._read_stdout)
        self.process.readyReadStandardError.connect(self._read_stderr)
        self.process.finished.connect(self._finished)
        self.process.errorOccurred.connect(self._process_error)
        self.files = []
        self.is_running = False
        self.cancel_requested = False
        self.result = None
        self.run_dir = None
        self.buffer = b""
        self.diagnostics = ""
        self.last_error = ""
        self._build_ui()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(10)
        notice = QLabel("Локально · русский и английский · текстовый слой или OCR сканов · порядок файлов сохраняется")
        notice.setWordWrap(True)
        root.addWidget(notice)
        controls = QHBoxLayout()
        self.add_button = QPushButton("＋ Добавить PDF")
        self.add_button.clicked.connect(self._choose_files)
        self.remove_button = QPushButton("Убрать выбранные")
        self.remove_button.setObjectName("SecondaryBtn")
        self.remove_button.clicked.connect(self._remove_files)
        self.clear_button = QPushButton("Очистить список")
        self.clear_button.setObjectName("SecondaryBtn")
        self.clear_button.clicked.connect(self._clear_files)
        controls.addWidget(self.add_button)
        controls.addWidget(self.remove_button)
        controls.addWidget(self.clear_button)
        controls.addStretch()
        root.addLayout(controls)
        splitter = QSplitter(Qt.Orientation.Vertical)
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["PDF-файл", "Страницы", "Состояние"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.table.setColumnWidth(1, 95)
        self.table.verticalHeader().hide()
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        splitter.addWidget(self.table)
        preview_group = QGroupBox("Общий текст результата")
        preview_layout = QVBoxLayout(preview_group)
        self.preview = QPlainTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setPlaceholderText("После обработки здесь появится текст с границами файлов и страниц.")
        preview_layout.addWidget(self.preview)
        splitter.addWidget(preview_group)
        splitter.setSizes([210, 270])
        root.addWidget(splitter, 1)
        settings = QGroupBox("Параметры распознавания")
        grid = QGridLayout(settings)
        self.model_dir = QLineEdit(str(self.paths.data_dir / "models/easyocr"))
        browse_models = QPushButton("Обзор…")
        browse_models.setObjectName("SecondaryBtn")
        browse_models.clicked.connect(self._choose_models)
        grid.addWidget(QLabel("Локальные модели:"), 0, 0)
        grid.addWidget(self.model_dir, 0, 1)
        grid.addWidget(browse_models, 0, 2)
        self.gpu = QCheckBox("Использовать NVIDIA / CUDA (если настроена)")
        self.dpi = QSpinBox()
        self.dpi.setRange(100, 300)
        self.dpi.setValue(200)
        self.dpi.setSuffix(" DPI")
        row = QHBoxLayout()
        row.addWidget(self.gpu)
        row.addStretch()
        row.addWidget(QLabel("Качество сканов:"))
        row.addWidget(self.dpi)
        grid.addLayout(row, 1, 0, 1, 3)
        hint = QLabel("Модели готовятся один раз командой ocr_setup по README. При обработке скачивания отключены.")
        hint.setWordWrap(True)
        grid.addWidget(hint, 2, 0, 1, 3)
        root.addWidget(settings)
        action_row = QHBoxLayout()
        self.start_button = QPushButton("Распознать пакет")
        self.start_button.setObjectName("SuccessBtn")
        self.start_button.clicked.connect(self.start)
        self.cancel_button = QPushButton("Отменить")
        self.cancel_button.setObjectName("DangerBtn")
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self.cancel)
        self.copy_button = QPushButton("Копировать текст")
        self.copy_button.setObjectName("SecondaryBtn")
        self.copy_button.clicked.connect(self._copy_text)
        self.save_button = QPushButton("Сохранить DOCX…")
        self.save_button.setObjectName("SecondaryBtn")
        self.save_button.clicked.connect(self._save_docx)
        self.folder_button = QPushButton("Папка результата")
        self.folder_button.setObjectName("SecondaryBtn")
        self.folder_button.clicked.connect(self._open_result)
        for button in (self.start_button, self.cancel_button, self.copy_button, self.save_button, self.folder_button):
            action_row.addWidget(button)
        root.addLayout(action_row)
        self.progress = QProgressBar()
        self.progress.setValue(0)
        root.addWidget(self.progress)
        self.status = QLabel("Добавьте PDF-файлы. Базовый режим — CPU.")
        self.status.setWordWrap(True)
        root.addWidget(self.status)
        self.edit_controls = [self.add_button, self.remove_button, self.clear_button, settings]
        self._result_buttons(False)

    def add_files(self, filenames):
        if self.is_running:
            return
        seen = {str(path).casefold() if sys.platform == "win32" else str(path) for path in self.files}
        for filename in filenames:
            path = Path(filename).resolve()
            key = str(path).casefold() if sys.platform == "win32" else str(path)
            if key in seen or path.suffix.lower() != ".pdf":
                continue
            seen.add(key)
            self.files.append(path)
            row = self.table.rowCount()
            self.table.insertRow(row)
            item = QTableWidgetItem(path.name)
            item.setToolTip(str(path))
            self.table.setItem(row, 0, item)
            self.table.setItem(row, 1, QTableWidgetItem("—"))
            self.table.setItem(row, 2, QTableWidgetItem("В очереди"))

    def _choose_files(self):
        names, _ = QFileDialog.getOpenFileNames(self, "Выберите PDF в порядке обработки", "", "PDF (*.pdf)")
        self.add_files(names)

    def _remove_files(self):
        if not self.is_running:
            for row in sorted({index.row() for index in self.table.selectedIndexes()}, reverse=True):
                self.files.pop(row)
                self.table.removeRow(row)

    def _clear_files(self):
        if not self.is_running:
            self.files.clear()
            self.table.setRowCount(0)

    def _choose_models(self):
        folder = QFileDialog.getExistingDirectory(self, "Выберите папку моделей EasyOCR", self.model_dir.text())
        if folder:
            self.model_dir.setText(folder)

    def _result_buttons(self, ready):
        self.copy_button.setEnabled(ready)
        self.save_button.setEnabled(ready)
        self.folder_button.setEnabled(ready)

    def start(self):
        if self.is_running:
            return
        if not self.files:
            self.status.setText("Сначала добавьте хотя бы один PDF.")
            return
        if not self.model_dir.text().strip():
            self.status.setText("Укажите папку локальных моделей OCR.")
            return
        base = self.paths.data_dir / "ocr-results"
        try:
            base.mkdir(parents=True, exist_ok=True)
            self.run_dir = Path(tempfile.mkdtemp(prefix="batch-", dir=base))
            models = Path(self.model_dir.text().strip()).expanduser()
            if not models.is_absolute():
                models = self.paths.data_dir / models
            request = {"files": [str(path) for path in self.files], "output_dir": str(self.run_dir / "result"),
                       "model_dir": str(models.resolve()), "gpu": self.gpu.isChecked(), "dpi": self.dpi.value(),
                       "cancel_path": str(self.run_dir / "cancel")}
            request_path = self.run_dir / "request.json"
            request_path.write_text(json.dumps(request, ensure_ascii=False), encoding="utf-8")
        except OSError as exc:
            self.status.setText(f"Не удалось создать папку результата: {exc}")
            return
        self.result = None
        self.preview.clear()
        self.buffer = b""
        self.diagnostics = self.last_error = ""
        self.cancel_requested = False
        self.is_running = True
        self._result_buttons(False)
        for control in self.edit_controls:
            control.setEnabled(False)
        self.start_button.setEnabled(False)
        self.cancel_button.setEnabled(True)
        self.progress.setRange(0, 0)
        self.status.setText("Открытие PDF и подсчёт страниц…")
        for row in range(self.table.rowCount()):
            self.table.item(row, 2).setText("В очереди")
        program, arguments = worker_command(request_path)
        environment = QProcessEnvironment.systemEnvironment()
        environment.insert("PYTHONIOENCODING", "utf-8")
        environment.insert("PYTHONUNBUFFERED", "1")
        self.process.setProcessEnvironment(environment)
        self.process.setProgram(program)
        self.process.setArguments(arguments)
        self.process.start()

    def cancel(self):
        if self.is_running and self.run_dir:
            try:
                (self.run_dir / "cancel").touch()
                self.cancel_requested = True
                self.cancel_button.setEnabled(False)
                self.status.setText("Отмена: дождитесь текущей страницы и сохранения неполного результата…")
            except OSError as exc:
                self.status.setText(f"Не удалось передать отмену: {exc}")

    def _read_stderr(self):
        self.diagnostics = (self.diagnostics + bytes(self.process.readAllStandardError()).decode("utf-8", errors="replace"))[-12000:]

    def _read_stdout(self):
        self.buffer += bytes(self.process.readAllStandardOutput())
        while b"\n" in self.buffer:
            line, self.buffer = self.buffer.split(b"\n", 1)
            try:
                self._event(json.loads(line))
            except (ValueError, TypeError, KeyError):
                continue
        if len(self.buffer) > 65536:
            self.buffer = b""  # Непротокольный вывод библиотеки не раздувает память GUI.

    def _event(self, event):
        kind = event.get("event")
        row = event.get("file", -1)
        valid = isinstance(row, int) and 0 <= row < self.table.rowCount()
        if kind == "batch_started":
            self.progress.setRange(0, max(1, event["pages"]))
        elif kind == "file_info" and valid:
            self.table.item(row, 1).setText(str(event["pages"]))
        elif kind in ("page_started", "recognizing", "page_finished") and valid:
            action = "OCR" if kind == "recognizing" else "Страница"
            self.table.item(row, 2).setText(f"{action} {event['page']}")
            if not self.cancel_requested:
                self.status.setText(f"Файл {row + 1}/{len(self.files)}: {self.files[row].name} · {action.lower()} {event['page']}")
            if kind == "page_finished":
                self.progress.setValue(event["done"])
        elif kind == "file_finished" and valid:
            states = {"completed": "Готово", "partial": "Есть ошибки страниц", "error": "Ошибка"}
            self.table.item(row, 2).setText(states.get(event["status"], event["status"]))
            self.table.item(row, 2).setToolTip(event.get("error", ""))
        elif kind == "exporting":
            self.status.setText("Сохранение общего Word-файла и текста…")
        elif kind == "fatal":
            self.last_error = event.get("error", "Ошибка OCR")

    def _process_error(self, error):
        if error == QProcess.ProcessError.FailedToStart:
            self.last_error = "Не удалось запустить OCR-процесс: " + self.process.errorString()
            self._finished(-1, QProcess.ExitStatus.CrashExit)

    def _finished(self, exit_code, exit_status):
        if not self.is_running:
            return
        self._read_stdout()
        self._read_stderr()
        self.is_running = False
        for control in self.edit_controls:
            control.setEnabled(True)
        self.start_button.setEnabled(True)
        self.cancel_button.setEnabled(False)
        self.progress.setRange(0, 100)
        manifest_path = self.run_dir / "result/result.json"
        try:
            self.result = json.loads(manifest_path.read_text(encoding="utf-8"))
            if self.result["status"] == "running":
                self.result["status"] = "interrupted"
                self.last_error = "Процесс прерван. Завершённые страницы сохранены в папке pages, экспорт не завершён."
            titles = {"completed": "Готово", "partial": "Неполный результат: есть ошибки",
                      "cancelled": "Отменено · неполный результат", "failed": "Ошибка OCR", "interrupted": "Процесс прерван"}
            self.status.setText(f"{titles.get(self.result['status'], 'Завершено')} · "
                                f"страниц {self.result['pages_done']}/{self.result['pages_total']} · "
                                f"ошибок: {self.result['errors']} · {self.result.get('elapsed_seconds', 0):.1f} с")
            for row, item in enumerate(self.result["files"]):
                if row >= self.table.rowCount():
                    break
                states = {"completed": "Готово", "cancelled": "Отменено", "partial": "Есть ошибки", "error": "Ошибка",
                          "running": "Прервано", "pending": "Не обработан"}
                self.table.item(row, 2).setText(states.get(item["status"], item["status"]))
                errors = [item.get("error", "")] + [p["error"] for p in item["pages"] if p.get("error")]
                self.table.item(row, 2).setToolTip("\n".join(filter(None, errors)))
            if self.result.get("fatal_error"):
                self.last_error = self.result["fatal_error"]
            text_path = self.result.get("text_path")
            if text_path:
                with (manifest_path.parent / text_path).open(encoding="utf-8") as stream:
                    preview = stream.read(2_000_000)
                    truncated = bool(stream.read(1))
                self.preview.setPlainText(preview + ("\n[Предпросмотр сокращён; копирование и DOCX содержат полный текст.]" if truncated else ""))
            self._result_buttons(bool(self.result.get("docx_path")))
            self.copy_button.setEnabled(bool(self.result.get("text_path")))
            self.folder_button.setEnabled(True)
            self.progress.setValue(100 if self.result["status"] == "completed" else
                                   round(100 * self.result["pages_done"] / max(1, self.result["pages_total"])))
        except (OSError, ValueError, KeyError) as exc:
            self.last_error = self.last_error or f"OCR-процесс завершился без результата (код {exit_code}): {exc}"
            self.status.setText("Ошибка OCR: " + self.last_error)
            self.folder_button.setEnabled(True)
        if self.last_error:
            self.status.setText(self.status.text() + "\n" + self.last_error)
        if self.diagnostics:
            try:
                (self.run_dir / "diagnostics.log").write_text(self.diagnostics, encoding="utf-8")
            except OSError:
                pass
        self.stopped.emit()

    def _copy_text(self):
        if self.result and self.result.get("text_path"):
            try:
                text = (self.run_dir / "result" / self.result["text_path"]).read_text(encoding="utf-8")
                QApplication.clipboard().setText(text)
            except OSError as exc:
                self.status.setText(f"Не удалось прочитать текст: {exc}")

    def _save_docx(self):
        if not self.result or not self.result.get("docx_path"):
            return
        source = self.run_dir / "result" / self.result["docx_path"]
        filename, _ = QFileDialog.getSaveFileName(self, "Сохранить результат OCR", str(source), "Word (*.docx)")
        if filename:
            target = Path(filename)
            if target.suffix.lower() != ".docx":
                target = target.with_suffix(".docx")
            if source.resolve() == target.resolve():
                return
            try:
                # Копирование экспорта тоже атомарно: окончательное имя появится целиком.
                descriptor, temporary = tempfile.mkstemp(dir=target.parent, suffix=".docx")
                import os
                os.close(descriptor)
                try:
                    shutil.copyfile(source, temporary)
                    Path(temporary).replace(target)
                finally:
                    Path(temporary).unlink(missing_ok=True)
            except OSError as exc:
                self.status.setText(f"Не удалось сохранить DOCX: {exc}")

    def _open_result(self):
        if self.run_dir:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.run_dir)))
