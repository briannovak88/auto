import asyncio
import importlib.util
import os
import tempfile
import threading
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt6.QtCore import QTimer
from PyQt6.QtGui import QTextCursor
from PyQt6.QtWidgets import QApplication, QMessageBox, QPlainTextEdit

from app_paths import resolve_paths
from main import AutomatDocsWindow
from tests.test_gui import wait_until
from text_engine import API_KEY_ENV, MAX_SOURCE, TextRequest, TextSettings, generate
from text_widget import CloudConsentDialog, TextWorker

HAS_HTTPX = importlib.util.find_spec("httpx") is not None
if HAS_HTTPX:
    import httpx


@unittest.skipUnless(HAS_HTTPX, "Установите requirements-text.txt")
class TextGuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="текст gui ")
        self.root = Path(self.temp.name)
        self.window = AutomatDocsWindow(paths=resolve_paths(data_dir=self.root))
        self.widget = self.window.text
        self.window.navigation.setCurrentRow(2)
        self.window.show()
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(self._close)

    def _close(self):
        self.widget.cancel()
        wait_until(lambda: not self.widget.is_running)
        self.window.close()

    def mock_provider(self, handler):
        async def adapter(request, settings, **kwargs):
            return await generate(request, settings, transport=httpx.MockTransport(handler), **kwargs)
        return patch("text_widget.generate", side_effect=adapter)

    def test_write_and_translate_async_navigation_edit_copy_and_size(self):
        async def handler(request):
            await asyncio.sleep(.12)
            return httpx.Response(200, json={"message": {"content": "Проверенный результат"}, "done": True})

        self.window.resize(1366, 768)
        self.app.processEvents()
        self.assertEqual((self.window.width(), self.window.height()), (1366, 768))
        self.widget.source.setPlainText("Исходный текст")
        self.widget.instruction.setPlainText("Отредактируй")
        self.window.documents.inp_fio.setText("Несохранённый автор")
        ticks = []
        timer = QTimer()
        timer.timeout.connect(lambda: ticks.append(1))
        timer.start(10)
        try:
            with self.mock_provider(handler):
                for mode in (0, 1):
                    self.widget.mode.setCurrentIndex(mode)
                    self.widget.start()
                    self.assertTrue(self.widget.is_running)
                    self.assertTrue(self.widget.source.isReadOnly())
                    self.window.navigation.setCurrentRow(0)
                    wait_until(lambda: not self.widget.is_running)
                    self.assertEqual(self.widget.output.toPlainText(), "Проверенный результат")
                    self.assertEqual(self.widget.source.toPlainText(), "Исходный текст")
                    self.window.navigation.setCurrentRow(2)
            self.assertTrue(ticks)
            self.assertEqual(self.window.documents.inp_fio.text(), "Несохранённый автор")
            self.widget.output.insertPlainText("Исправление: ")
            self.widget.copy_button.click()
            self.assertIn("Исправление", QApplication.clipboard().text())
            self.assertFalse(self.widget.output.isReadOnly())
        finally:
            timer.stop()

    def test_cloud_preview_decline_and_repeated_consent_use_exact_snapshot(self):
        self.widget.provider.setCurrentIndex(1)
        self.widget.mode.setCurrentIndex(1)
        self.widget.source.setPlainText("Текст только выбранного документа")
        self.widget.instruction.setPlainText("Сохрани термины")
        with patch.object(self.widget, "_confirm_cloud", return_value=False) as confirm, patch("text_widget.generate") as send:
            self.widget.start()
            confirm.assert_called_once()
            send.assert_not_called()
            self.assertFalse(self.widget.is_running)
        dialog = CloudConsentDialog(TextRequest("translate", "Проверка полного предпросмотра"),
                                    replace(TextSettings(), provider="cloud"), self.widget)
        self.assertIn("Проверка полного предпросмотра", dialog.findChild(QPlainTextEdit).toPlainText())
        dialog.close()
        calls = []

        async def handler(request):
            calls.append(request)
            return httpx.Response(200, json={"choices": [{"message": {"content": "Translation"}, "finish_reason": "stop"}]})

        with patch.dict(os.environ, {API_KEY_ENV: "synthetic-key"}), self.mock_provider(handler), \
                patch.object(self.widget, "_confirm_cloud", return_value=True) as confirm:
            for index in range(2):
                self.widget.start()
                wait_until(lambda: not self.widget.is_running)
            self.assertEqual(confirm.call_count, 2)
            self.assertEqual(confirm.call_args.args[0].source, "Текст только выбранного документа")
        self.assertEqual(len(calls), 2)
        self.assertEqual(self.widget.output.toPlainText(), "Translation")

    def test_failure_timeout_and_cancel_preserve_editors_and_allow_retry(self):
        self.widget.mode.setCurrentIndex(1)
        self.widget.source.setPlainText("Важный исходный текст")
        self.widget.output.setPlainText("Прежний результат")
        self.widget.settings = replace(self.widget.settings, timeout=1)
        started = threading.Event()

        async def hanging(request):
            started.set()
            await asyncio.sleep(30)

        with self.mock_provider(hanging):
            self.widget.start()
            wait_until(lambda: not self.widget.is_running)
            self.assertIn("Истекло", self.widget.status.text())
            self.widget.start()
            wait_until(started.is_set)
            self.widget.cancel()
            wait_until(lambda: not self.widget.is_running)
            self.assertIn("отменён", self.widget.status.text())
        with self.mock_provider(lambda r: httpx.Response(500, text="private-server-details")):
            self.widget.start()
            wait_until(lambda: not self.widget.is_running)
            self.assertIn("HTTP 500", self.widget.status.text())
            self.assertNotIn("private-server", self.widget.status.text())
        self.assertEqual(self.widget.source.toPlainText(), "Важный исходный текст")
        self.assertEqual(self.widget.output.toPlainText(), "Прежний результат")
        self.assertTrue(self.widget.start_button.isEnabled())

    def test_edited_result_requires_replacement_confirmation(self):
        self.widget.mode.setCurrentIndex(1)
        self.widget.source.setPlainText("Переведи")
        self.widget.output.insertPlainText("Моя правка")
        with patch("text_widget.QMessageBox.question", return_value=QMessageBox.StandardButton.No), patch("text_widget.generate") as send:
            self.widget.start()
            send.assert_not_called()
        self.assertEqual(self.widget.output.toPlainText(), "Моя правка")

    def test_close_cancels_and_waits_for_thread_and_decline_keeps_running(self):
        started = threading.Event()

        async def hanging(request):
            started.set()
            await asyncio.sleep(30)

        self.widget.instruction.setPlainText("Напиши текст")
        with self.mock_provider(hanging):
            self.widget.start()
            wait_until(started.is_set)
            with patch("main.QMessageBox.question", return_value=QMessageBox.StandardButton.No):
                self.window.close()
            self.assertTrue(self.widget.is_running)
            self.assertTrue(self.window.isVisible())
            with patch("main.QMessageBox.question", return_value=QMessageBox.StandardButton.Yes):
                self.window.close()
            wait_until(lambda: not self.widget.is_running)
            wait_until(lambda: not self.window.isVisible())
            self.assertIn("отменён", self.widget.status.text())

    def test_cancel_before_thread_starts_never_calls_provider(self):
        worker = TextWorker(TextRequest("write", instruction="test"), TextSettings())
        worker.cancel()
        async def should_not_execute(*args, **kwargs):
            raise AssertionError("Сеть после отмены")
        with patch("text_widget.generate", side_effect=should_not_execute):
            worker.start()
            wait_until(lambda: worker.isFinished())
        self.assertTrue(worker.cancelled)
        self.assertEqual(worker.error, "")
        worker.deleteLater()

    def test_ocr_transfer_selection_selected_file_replace_and_large_input(self):
        ocr = self.window.ocr
        ocr.run_dir = self.root / "ocr-results" / "batch-test"
        folder = ocr.run_dir / "result"
        folder.mkdir(parents=True)
        (folder / "all.txt").write_text("Общий результат", encoding="utf-8")
        (folder / "first.txt").write_text("Первый документ", encoding="utf-8")
        (folder / "second.txt").write_text("Второй документ", encoding="utf-8")
        ocr.add_files([self.root / "one.pdf", self.root / "two.pdf"])
        ocr.result = {"text_path": "all.txt", "files": [
            {"name": "one.pdf", "source": str(self.root / "one.pdf"), "pages": [{"number": 1, "text_file": "first.txt"}]},
            {"name": "two.pdf", "source": str(self.root / "two.pdf"), "pages": [{"number": 1, "text_file": "second.txt"}]},
        ]}
        ocr.table.selectRow(1)
        with patch("text_widget.generate") as send:
            ocr._send_to_text()
            self.assertIn("Второй документ", self.widget.source.toPlainText())
            self.assertNotIn("Первый документ", self.widget.source.toPlainText())
            self.assertEqual(self.window.navigation.currentRow(), 2)
            send.assert_not_called()
        ocr.table.selectRow(0)
        ocr._remove_files()
        ocr.table.selectRow(0)
        ocr._send_to_text()
        self.assertIn("Второй документ", self.widget.source.toPlainText())
        ocr.add_files([self.root / "not-processed.pdf"])
        ocr.table.selectRow(1)
        ocr._send_to_text()
        self.assertIn("ещё не обработан", ocr.status.text())
        ocr.preview.setPlainText("Выделенный фрагмент\nОстальное")
        cursor = ocr.preview.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.Start)
        cursor.movePosition(QTextCursor.MoveOperation.EndOfLine, QTextCursor.MoveMode.KeepAnchor)
        ocr.preview.setTextCursor(cursor)
        with patch("text_widget.QMessageBox.question", return_value=QMessageBox.StandardButton.No):
            ocr._send_to_text()
            self.assertIn("Второй документ", self.widget.source.toPlainText())
        with patch("text_widget.QMessageBox.question", return_value=QMessageBox.StandardButton.Yes):
            ocr._send_to_text()
        self.assertEqual(self.widget.source.toPlainText(), "Выделенный фрагмент")
        ocr.preview.selectAll()
        ocr.preview.setPlainText("")
        ocr.table.clearSelection()
        (folder / "all.txt").write_text("x" * (MAX_SOURCE + 1), encoding="utf-8")
        ocr._send_to_text()
        self.assertIn("не обрезается", ocr.status.text())
        self.assertEqual(self.widget.source.toPlainText(), "Выделенный фрагмент")
