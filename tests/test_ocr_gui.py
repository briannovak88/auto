import importlib.util
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QTimer, QMimeData, QPoint, QPointF, QUrl, Qt
from PyQt6.QtGui import QDragEnterEvent, QDropEvent
from PyQt6.QtWidgets import QApplication, QMessageBox
from app_paths import resolve_paths
from main import AutomatDocsWindow
from ocr_widget import worker_command
from tests.test_gui import wait_until
from tests.ocr_fixtures import make_pdf


@unittest.skipUnless(importlib.util.find_spec("pymupdf"), "Установите requirements-ocr.txt")
class OcrGuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="ocr gui тест ")
        self.root = Path(self.temp.name)
        self.window = AutomatDocsWindow(paths=resolve_paths(data_dir=self.root))
        self.widget = self.window.ocr
        self.window.show()
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(self._close)

    def _close(self):
        if self.widget.is_running:
            self.widget.process.kill()
            self.widget.process.waitForFinished(5000)
        self.window.close()

    def test_real_qprocess_result_copy_and_switching_screens(self):
        first = make_pdf(self.root / "Первый файл.pdf")
        second = make_pdf(self.root / "Второй.pdf")
        self.widget.add_files([first, second, first])
        self.widget.preserve_layout.setChecked(True)
        self.assertEqual(len(self.widget.files), 2)
        self.window.documents.inp_fio.setText("Несохранённый автор")
        self.window.navigation.setCurrentRow(1)
        ticks = []
        timer = QTimer()
        timer.timeout.connect(lambda: ticks.append(1))
        timer.start(10)
        self.widget.start()
        self.assertTrue(self.widget.is_running)
        self.assertFalse(self.widget.add_button.isEnabled())
        self.window.navigation.setCurrentRow(0)
        wait_until(lambda: not self.widget.is_running, 20000)
        timer.stop()
        self.assertTrue(ticks)
        self.assertEqual(self.widget.result["status"], "completed")
        self.assertTrue(self.widget.result["preserve_layout"])
        self.assertEqual(self.widget.progress.value(), 100)
        self.assertIn("Файл 1: Первый файл.pdf", self.widget.preview.toPlainText())
        self.assertEqual(self.window.documents.inp_fio.text(), "Несохранённый автор")
        self.widget._copy_text()
        self.assertIn("Второй.pdf", QApplication.clipboard().text())
        target = self.root / "экспорт с пробелом.docx"
        with patch("ocr_widget.QFileDialog.getSaveFileName", return_value=(str(target), "")):
            self.widget._save_docx()
        self.assertTrue(target.is_file())

    def test_cancel_real_worker_and_repeat(self):
        source = make_pdf(self.root / "длинный пакет.pdf", ("text",) * 300)
        self.widget.add_files([source])
        original = self.widget._event

        def cancel_after_page(event):
            original(event)
            if event.get("event") == "page_finished" and not self.widget.cancel_requested:
                self.widget.cancel()

        with patch.object(self.widget, "_event", side_effect=cancel_after_page):
            self.widget.start()
            wait_until(lambda: not self.widget.is_running, 20000)
        self.assertEqual(self.widget.result["status"], "cancelled")
        self.assertLess(self.widget.result["pages_done"], 300)
        self.assertTrue(self.widget.copy_button.isEnabled())
        self.assertTrue(self.widget.save_button.isEnabled())
        self.assertIn("НЕПОЛНЫЙ РЕЗУЛЬТАТ", self.widget.preview.toPlainText())
        self.widget._clear_files()
        self.widget.add_files([make_pdf(self.root / "повтор.pdf")])
        previous = self.widget.run_dir
        self.widget.start()
        wait_until(lambda: not self.widget.is_running, 20000)
        self.assertEqual(self.widget.result["status"], "completed")
        self.assertNotEqual(self.widget.run_dir, previous)

    def test_close_cancels_ocr_and_waits_for_process(self):
        self.widget.add_files([make_pdf(self.root / "пакет.pdf", ("text",) * 300)])
        original = self.widget._event
        asked = []

        def close_during_work(event):
            original(event)
            if event.get("event") == "page_started" and not asked:
                asked.append(1)
                self.window.close()

        with patch.object(self.widget, "_event", side_effect=close_during_work), patch("main.QMessageBox.question", return_value=QMessageBox.StandardButton.Yes):
            self.widget.start()
            wait_until(lambda: not self.widget.is_running, 20000)
            wait_until(lambda: not self.window.isVisible())
        self.assertTrue(asked)
        self.assertEqual(self.widget.result["status"], "cancelled")

    def test_failed_start_restores_controls_and_frozen_worker_arguments(self):
        self.widget.add_files([make_pdf(self.root / "файл.pdf")])
        with patch("ocr_widget.worker_command", return_value=(str(self.root / "missing-executable"), [])):
            self.widget.start()
            wait_until(lambda: not self.widget.is_running)
        self.assertTrue(self.widget.start_button.isEnabled())
        self.assertTrue(self.widget.add_button.isEnabled())
        self.assertIn("Не удалось запустить", self.widget.status.text())
        with patch.object(sys, "frozen", True, create=True):
            executable, arguments = worker_command(self.root / "request.json")
        self.assertEqual(executable, sys.executable)
        self.assertEqual(arguments, ["--ocr-worker", str(self.root / "request.json")])

    def test_drag_drop_mixed_files_duplicates_and_busy_state(self):
        from PIL import Image
        pdf = make_pdf(self.root / "Первый.pdf")
        png = self.root / "Второй.PNG"
        jpg = self.root / "Третий.jpeg"
        for path in (png,jpg):
            with Image.new("RGB",(100,100),"white") as image:
                image.save(path)
        unsupported=self.root/"file.txt"
        unsupported.write_text("Unsupported")
        folder=self.root/"directory.png"
        folder.mkdir()
        mime=QMimeData()
        mime.setUrls([QUrl.fromLocalFile(str(p)) for p in (png,pdf,jpg,png,unsupported,folder)] +
                     [QUrl("https://example.com/remote.png")])
        table=self.widget.table
        self.window.navigation.setCurrentRow(1)
        QApplication.processEvents()
        def drop():
            enter=QDragEnterEvent(QPoint(20,20),Qt.DropAction.CopyAction,mime,
                                  Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier)
            QApplication.sendEvent(table.viewport(),enter)
            event=QDropEvent(QPointF(20,20),Qt.DropAction.CopyAction,mime,
                             Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier)
            QApplication.sendEvent(table.viewport(),event)
            return enter,event
        enter,event=drop()
        self.assertTrue(enter.isAccepted())
        self.assertTrue(event.isAccepted())
        self.assertEqual(self.widget.files,[png,pdf,jpg])
        self.assertFalse(table.hint.isVisible())
        drop()
        self.assertEqual(len(self.widget.files),3)
        table.accept_files=False
        enter,event=drop()
        self.assertFalse(enter.isAccepted())
        self.assertFalse(event.isAccepted())
        table.accept_files=True
        self.widget._clear_files()
        self.assertTrue(table.hint.isVisible())
        self.assertTrue(all(p.exists() for p in (png,pdf,jpg)))

    def test_file_picker_includes_images(self):
        with patch("ocr_widget.QFileDialog.getOpenFileNames", return_value=([],"")) as picker:
            self.widget._choose_files()
        self.assertIn("*.jpg",picker.call_args.args[3])
        self.assertIn("*.png",picker.call_args.args[3])


if __name__ == "__main__":
    unittest.main()
