"""Редактор текста. Асинхронный HTTP работает в отдельном потоке, вне GUI."""
import asyncio
import threading
from dataclasses import replace

from PyQt6.QtCore import QThread, QTimer, Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QApplication, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QGroupBox,
    QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPlainTextEdit, QProgressBar,
    QPushButton, QSpinBox, QSplitter, QVBoxLayout, QWidget,
)

from text_engine import (
    API_KEY_ENV, MAX_INSTRUCTION, MAX_SOURCE, TextError, TextRequest, TextSettings,
    build_messages, generate, load_settings, save_settings, validate_settings,
)


class TextWorker(QThread):
    """Время жизни заканчивается по finished, а не по выдаче результата."""
    def __init__(self, request, settings, consent=False, parent=None):
        super().__init__(parent)
        self.request, self.settings, self.consent = request, settings, consent
        self.loop = self.task = None
        self._lock = threading.Lock()
        self._cancel = threading.Event()
        self.result = None
        self.error = ""
        self.cancelled = False

    def cancel(self):
        self._cancel.set()
        with self._lock:
            if self.loop is not None and self.task is not None:
                self.loop.call_soon_threadsafe(self.task.cancel)

    def run(self):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            with self._lock:
                self.loop = loop
                self.task = loop.create_task(generate(self.request, self.settings, cloud_consent=self.consent))
                if self._cancel.is_set():
                    self.task.cancel()
            try:
                self.result = loop.run_until_complete(self.task)
            except asyncio.CancelledError:
                self.cancelled = True
            except TextError as exc:
                self.error = str(exc)
            except Exception:
                # Не выводим сырой сетевой exception: он может содержать текст/ключ.
                self.error = "Не удалось обработать ответ провайдера. Исходный текст сохранён."
        finally:
            with self._lock:
                self.loop = self.task = None
            loop.run_until_complete(loop.shutdown_asyncgens())
            loop.run_until_complete(loop.shutdown_default_executor())
            loop.close()
            asyncio.set_event_loop(None)


class ProviderDialog(QDialog):
    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Настройки написания и перевода")
        self.resize(660, 380)
        self.settings = settings
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.fields = {}
        for name, title in (("ollama_url", "Адрес Ollama:"), ("ollama_model", "Модель Ollama:"),
                            ("cloud_url", "Базовый адрес облачного API:"), ("cloud_model", "Модель облака:")):
            field = QLineEdit(getattr(settings, name))
            self.fields[name] = field
            form.addRow(title, field)
        self.timeout = QSpinBox()
        self.timeout.setRange(1, 600)
        self.timeout.setSuffix(" с")
        self.timeout.setValue(settings.timeout)
        form.addRow("Время ожидания:", self.timeout)
        layout.addLayout(form)
        note = QLabel(f"Ollama работает на этом компьютере. Облачный API — OpenAI-совместимый, через HTTPS. "
                      f"Ключ читается из {API_KEY_ENV}; вводить его в адрес нельзя. "
                      "Сохраняются только адреса, имена моделей и тайм-аут.")
        note.setWordWrap(True)
        layout.addWidget(note)
        self.error = QLabel()
        self.error.setWordWrap(True)
        layout.addWidget(self.error)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("Сохранить")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Отмена")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def accept(self):
        candidate = replace(self.settings, **{name: field.text().strip() for name, field in self.fields.items()},
                            timeout=self.timeout.value())
        try:
            validate_settings(candidate)
        except TextError as exc:
            self.error.setText(str(exc))
            return
        self.settings = candidate
        super().accept()


class CloudConsentDialog(QDialog):
    """Показывает весь неизменяемый снимок отправляемых сообщений."""
    def __init__(self, request, settings, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Подтверждение отправки текста в облако")
        self.resize(780, 580)
        layout = QVBoxLayout(self)
        destination = QLabel(f"Получатель: {settings.endpoint}\nМодель: {settings.model}\n"
                             "Будут отправлены все сообщения ниже, включая задание и исходный текст. "
                             "Вложения и другие документы не отправляются. Провайдер может учитывать запрос как платный.")
        destination.setTextFormat(Qt.TextFormat.PlainText)
        destination.setWordWrap(True)
        layout.addWidget(destination)
        preview = QPlainTextEdit()
        preview.setReadOnly(True)
        preview.setPlainText("\n\n".join(f"{message['role'].upper()}\n{message['content']}" for message in build_messages(request)))
        layout.addWidget(preview)
        buttons = QDialogButtonBox()
        send = buttons.addButton("Отправить этот текст", QDialogButtonBox.ButtonRole.AcceptRole)
        cancel = buttons.addButton("Не отправлять", QDialogButtonBox.ButtonRole.RejectRole)
        send.setAutoDefault(False)
        cancel.setDefault(True)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)


class TextWidget(QWidget):
    stopped = pyqtSignal()

    def __init__(self, paths, parent=None):
        super().__init__(parent)
        self.paths = paths
        self.settings_path = paths.data_dir / "text-settings.json"
        self.worker = None
        self._confirming = False
        warning = ""
        try:
            self.settings = load_settings(self.settings_path)
        except TextError as exc:
            self.settings, warning = TextSettings(), str(exc)
        self._build_ui()
        if warning:
            self.status.setText(warning)
        self.timer = QTimer(self)
        self.timer.setInterval(1000)
        self.timer.timeout.connect(self._tick)
        self.seconds = 0

    @property
    def is_running(self):
        return self.worker is not None

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        row = QHBoxLayout()
        self.provider = QComboBox()
        self.provider.addItem("Локально · Ollama", "ollama")
        self.provider.addItem("Облако · OpenAI-совместимый API", "cloud")
        self.provider.currentIndexChanged.connect(self._provider_changed)
        row.addWidget(self.provider)
        self.model_label = QLabel()
        self.model_label.setTextFormat(Qt.TextFormat.PlainText)
        row.addWidget(self.model_label, 1)
        self.settings_button = QPushButton("Настройки…")
        self.settings_button.setObjectName("SecondaryBtn")
        self.settings_button.clicked.connect(self._configure)
        row.addWidget(self.settings_button)
        root.addLayout(row)
        self.notice = QLabel()
        self.notice.setWordWrap(True)
        root.addWidget(self.notice)
        options = QHBoxLayout()
        self.mode = QComboBox()
        self.mode.addItem("Написание и редактирование", "write")
        self.mode.addItem("Перевод", "translate")
        self.mode.currentIndexChanged.connect(self._mode_changed)
        options.addWidget(self.mode)
        options.addStretch()
        options.addWidget(QLabel("Язык результата:"))
        self.language = QComboBox()
        self.language.setEditable(True)
        self.language.addItems(["Русский", "Английский", "Немецкий", "Французский", "Испанский", "Китайский"])
        self.language.lineEdit().setMaxLength(100)
        options.addWidget(self.language)
        root.addLayout(options)
        self.instruction = QPlainTextEdit()
        self.instruction.setPlaceholderText("Задание: напиши деловое письмо, исправь стиль, подготовь описание…")
        self.instruction.setMaximumHeight(82)
        self.instruction.setAccessibleName("Задание или пожелания к переводу")
        root.addWidget(self.instruction)
        editors = QSplitter(Qt.Orientation.Horizontal)
        source_group = QGroupBox("Исходный текст / материал")
        source_layout = QVBoxLayout(source_group)
        self.source = QPlainTextEdit()
        self.source.setPlaceholderText("Вставьте текст или передайте результат из раздела «Распознавание».")
        self.source.setAccessibleName("Исходный текст")
        source_layout.addWidget(self.source)
        self.counter = QLabel()
        source_layout.addWidget(self.counter)
        editors.addWidget(source_group)
        result_group = QGroupBox("Результат · можно редактировать")
        result_layout = QVBoxLayout(result_group)
        self.output = QPlainTextEdit()
        self.output.setPlaceholderText("Проверьте факты, имена и числа перед использованием результата.")
        self.output.setAccessibleName("Редактируемый результат")
        result_layout.addWidget(self.output)
        self.copy_button = QPushButton("Копировать результат")
        self.copy_button.setObjectName("SecondaryBtn")
        self.copy_button.clicked.connect(lambda: QApplication.clipboard().setText(self.output.toPlainText()))
        self.copy_button.setEnabled(False)
        result_layout.addWidget(self.copy_button)
        editors.addWidget(result_group)
        editors.setSizes([500, 500])
        root.addWidget(editors, 1)
        actions = QHBoxLayout()
        self.start_button = QPushButton("Написать текст")
        self.start_button.setObjectName("SuccessBtn")
        self.start_button.clicked.connect(self.start)
        self.cancel_button = QPushButton("Отменить")
        self.cancel_button.setObjectName("DangerBtn")
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self.cancel)
        actions.addWidget(self.start_button)
        actions.addWidget(self.cancel_button)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        actions.addWidget(self.progress, 1)
        root.addLayout(actions)
        self.status = QLabel("Вставьте материал и задайте действие. Для локального режима запустите Ollama.")
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        root.addWidget(self.status)
        self.source.textChanged.connect(self._count)
        self.instruction.textChanged.connect(self._count)
        self.output.textChanged.connect(lambda: self.copy_button.setEnabled(bool(self.output.toPlainText())))
        self._provider_changed()
        self._count()

    def _count(self):
        self.counter.setText(f"Текст: {len(self.source.toPlainText())} / {MAX_SOURCE} · "
                             f"задание: {len(self.instruction.toPlainText())} / {MAX_INSTRUCTION} символов")

    def _provider_changed(self):
        self.settings = replace(self.settings, provider=self.provider.currentData())
        self.model_label.setText("Модель: " + self.settings.model)
        self.model_label.setToolTip(self.settings.endpoint)
        self.notice.setText("Локально: текст отправляется только в Ollama на этом компьютере."
                            if self.settings.provider == "ollama" else
                            "Облако: перед каждым запросом покажем полный отправляемый материал и попросим подтверждение.")

    def _mode_changed(self):
        translating = self.mode.currentData() == "translate"
        self.start_button.setText("Перевести текст" if translating else "Написать текст")
        self.instruction.setPlaceholderText("Необязательные пожелания: терминология, стиль перевода…" if translating else
                                            "Задание: напиши деловое письмо, исправь стиль, подготовь описание…")

    def _configure(self):
        dialog = ProviderDialog(self.settings, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            save_settings(self.settings_path, dialog.settings)
        except (OSError, TextError):
            self.status.setText("Не удалось сохранить настройки. Проверьте доступ к папке данных.")
            return
        self.settings = dialog.settings
        self._provider_changed()
        self.status.setText("Настройки сохранены. Документы и ключи в файл настроек не записываются.")

    def _confirm_cloud(self, request, settings):
        return CloudConsentDialog(request, settings, self).exec() == QDialog.DialogCode.Accepted

    def start(self):
        if self.is_running or self._confirming:
            return
        request = TextRequest(self.mode.currentData(), self.source.toPlainText(),
                              self.instruction.toPlainText(), self.language.currentText())
        settings = self.settings
        try:
            validate_settings(settings)
            build_messages(request)
        except TextError as exc:
            self.status.setText(str(exc))
            return
        self._confirming = True
        try:
            if self.output.toPlainText() and self.output.document().isModified():
                if QMessageBox.question(self, "Изменённый результат", "После успешного запроса заменить отредактированный результат?",
                                        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                        QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
                    return
            consent = settings.provider == "cloud" and self._confirm_cloud(request, settings)
            if settings.provider == "cloud" and not consent:
                self.status.setText("Отправка в облако отменена. Текст не отправлялся.")
                return
        finally:
            self._confirming = False
        self.worker = TextWorker(request, settings, consent, self)
        self.worker.finished.connect(self._finished)
        self._set_busy(True)
        self.seconds = 0
        self._tick(advance=False)
        self.timer.start()
        self.worker.start()

    def _set_busy(self, busy):
        for control in (self.provider, self.mode, self.language, self.settings_button, self.start_button):
            control.setEnabled(not busy)
        for editor in (self.source, self.instruction, self.output):
            editor.setReadOnly(busy)
        self.cancel_button.setEnabled(busy)
        self.progress.setRange(0, 0 if busy else 100)
        if not busy:
            self.progress.setValue(0)

    def _tick(self, advance=True):
        if advance:
            self.seconds += 1
        if self.worker and self.worker._cancel.is_set():
            self.status.setText("Отмена запроса… Сервер мог уже начать обработку; отмена не отзывает отправленный текст.")
        else:
            self.status.setText(f"Ожидание ответа · {self.seconds} с · тайм-аут {self.settings.timeout} с. "
                                "Первая загрузка локальной модели может занять время.")

    def cancel(self):
        if self.worker:
            self.worker.cancel()
            self.cancel_button.setEnabled(False)
            self._tick(advance=False)

    def _finished(self):
        worker = self.worker
        if worker is None:
            return
        self.timer.stop()
        self.worker = None
        self._set_busy(False)
        if worker.cancelled or worker._cancel.is_set():
            self.status.setText("Запрос отменён. Исходный текст и прежний результат сохранены. "
                                "Сервер мог продолжить уже начатую обработку.")
        elif worker.error:
            self.status.setText(worker.error)
        elif worker.result:
            self.output.setPlainText(worker.result.text)
            self.output.document().setModified(False)
            self.progress.setValue(100)
            self.status.setText(worker.result.warning or "Готово. Проверьте и при необходимости исправьте результат.")
        worker.deleteLater()
        self.stopped.emit()

    def receive_ocr(self, text):
        if self.is_running or self._confirming:
            self.status.setText("Дождитесь завершения запроса или отмените его, затем передайте текст из OCR повторно.")
            return False
        if len(text) > MAX_SOURCE:
            self.status.setText(f"Выберите в OCR фрагмент до {MAX_SOURCE} символов. Текст не обрезан и не отправлен.")
            return False
        if self.source.toPlainText() and self.source.toPlainText() != text:
            if QMessageBox.question(self, "Передача из OCR", "Заменить текущий исходный текст выбранным результатом OCR?",
                                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                    QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
                return False
        self.source.setPlainText(text)
        self.status.setText("Текст из OCR перенесён в редактор. Выберите действие; запрос ещё не отправлялся.")
        return True
