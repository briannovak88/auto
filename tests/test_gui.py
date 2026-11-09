import argparse
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QDate, QEventLoop, QTimer
from PyQt6.QtWidgets import QApplication, QMessageBox, QWidget

from app_paths import AmbiguousDatabaseError, resolve_paths
from main import AutomatDocsWindow, GenerationWorker, select_startup_paths
from tests.fixtures import OUTPUT_NAMES, make_legacy_database, make_templates


def wait_until(predicate, timeout=5000):
    if predicate():
        return
    loop = QEventLoop()
    poll = QTimer()
    poll.timeout.connect(lambda: loop.quit() if predicate() else None)
    poll.start(10)
    QTimer.singleShot(timeout, loop.quit)
    loop.exec()
    poll.stop()
    if not predicate():
        raise AssertionError("Истекло время ожидания фоновой задачи")


class GuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="automat-gui ")
        self.root = Path(self.temp.name)
        self.addCleanup(self.temp.cleanup)
        make_legacy_database(self.root)
        make_templates(self.root / "templates")
        self.window = AutomatDocsWindow(paths=resolve_paths(data_dir=self.root))
        self.docs = self.window.documents
        self.docs._add_module_manually()
        self.window.show()
        self.app.processEvents()
        self.addCleanup(self.window.close)
        for target in ("main.QMessageBox.information", "main.QMessageBox.critical", "main.open_folder_in_explorer"):
            mock = patch(target)
            mock.start()
            self.addCleanup(mock.stop)

    def test_navigation_retains_forms_module_authors_date_and_paths(self):
        self.assertEqual(self.window.navigation.count(), 1)
        self.assertEqual(self.window.navigation.item(0).text(), "Документы")
        self.assertEqual(self.docs.tabs.count(), 2)
        self.docs.inp_fio.setText("Несохранённый тест")
        self.docs.modules_data[0]["program_name"] = "Изменённое название"
        self.docs.date_sign.setDate(QDate(2026, 11, 9))
        self.docs.chk_convert_pdf.setChecked(True)
        authors = list(self.docs.modules_data[0]["author_ids"])
        # Второй экран добавлен только в тесте; OCR в продукте ещё нет.
        other = QWidget()
        self.window.stack.addWidget(other)
        self.window.navigation.addItem("Тестовый экран")
        self.window.navigation.setCurrentRow(1)
        self.window.navigation.setCurrentRow(0)
        self.assertIs(self.window.stack.currentWidget(), self.docs)
        self.assertEqual(self.docs.inp_fio.text(), "Несохранённый тест")
        self.assertEqual(self.docs.modules_data[0]["program_name"], "Изменённое название")
        self.assertEqual(self.docs.modules_data[0]["author_ids"], authors)
        self.assertEqual(self.docs.date_sign.date(), QDate(2026, 11, 9))
        self.assertTrue(self.docs.chk_convert_pdf.isChecked())
        self.assertEqual(self.docs.inp_templates_dir.text(), str(self.root / "templates"))
        self.docs.tbl_authors.selectRow(0)
        self.assertEqual(self.docs.inp_pass_series.text(), "0000")
        self.assertEqual(self.docs.inp_pass_number.text(), "000000")
        self.assertIn("test.png", self.docs.lbl_sig_png.text())

    def test_gui_generates_six_documents_and_refreshes_pages(self):
        self.docs.modules_data[0]["listing_text"] = "\n".join("print('тест')" for _ in range(150))
        self.docs.date_sign.setDate(QDate(2026, 11, 9))
        with patch.dict(sys.modules, {"pythoncom": None}):
            self.docs._run_generation()
            self.assertFalse(self.docs.btn_generate.isEnabled())
            wait_until(lambda: self.docs.worker is None)
        self.assertEqual(self.docs.progress_bar.value(), 100)
        self.assertTrue(self.docs.btn_generate.isEnabled())
        self.assertEqual(self.docs.modules_data[0]["listing_pages"], 3)
        self.assertEqual(self.docs.tbl_modules.item(0, 3).text(), "3")
        folder = next((self.root / "output").iterdir())
        self.assertEqual({p.name for p in folder.iterdir()}, OUTPUT_NAMES)

    def test_close_waits_for_worker_and_can_be_declined(self):
        started, release = threading.Event(), threading.Event()

        def slow_generation(**kwargs):
            started.set()
            if not release.wait(5):
                raise RuntimeError("Тест не освободил поток")
            kwargs["module_info"]["listing_pages"] = 7
            return self.root / "output"

        with patch("main.generate_module_documents", side_effect=slow_generation), patch("main.QMessageBox.question", return_value=QMessageBox.StandardButton.No):
            self.docs._run_generation()
            worker = self.docs.worker
            try:
                wait_until(started.is_set)
                self.window.close()
                self.assertTrue(self.window.isVisible())
                self.assertFalse(self.window._close_when_finished)
                with patch("main.QMessageBox.question", return_value=QMessageBox.StandardButton.Yes):
                    self.window.close()
                self.assertTrue(self.window.isVisible())
                self.assertTrue(self.window._close_when_finished)
                self.assertTrue(worker.isRunning())
            finally:
                release.set()
                wait_until(lambda: self.docs.worker is None)
            wait_until(lambda: not self.window.isVisible())
        self.assertEqual(self.docs.tbl_modules.item(0, 3).text(), "7")

    def test_failure_retains_pages_and_allows_repeat(self):
        def failing_generation(**kwargs):
            kwargs["module_info"]["listing_pages"] = 8
            raise RuntimeError("Не удалось экспортировать PDF. DOCX уже сохранены.")

        with patch("main.generate_module_documents", side_effect=failing_generation):
            self.docs._run_generation()
            wait_until(lambda: self.docs.worker is None)
        self.assertEqual(self.docs.tbl_modules.item(0, 3).text(), "8")
        self.assertTrue(self.docs.btn_generate.isEnabled())
        self.assertIn("DOCX уже сохранены", self.docs.lbl_status.text())
        with patch("main.generate_module_documents", return_value=self.root / "output"):
            self.docs._run_generation()
            wait_until(lambda: self.docs.worker is None)
        self.assertEqual(self.docs.progress_bar.value(), 100)
        self.assertEqual(self.docs.tbl_modules.item(0, 3).text(), "8")

    def test_close_when_worker_finishes_during_confirmation(self):
        started, release = threading.Event(), threading.Event()

        def slow_generation(**kwargs):
            started.set()
            release.wait(5)
            return self.root / "output"

        def finish_during_question(*args):
            release.set()
            wait_until(lambda: self.docs.worker is None)
            return QMessageBox.StandardButton.Yes

        with patch("main.generate_module_documents", side_effect=slow_generation), patch("main.QMessageBox.question", side_effect=finish_during_question):
            self.docs._run_generation()
            try:
                wait_until(started.is_set)
                self.window.close()
            finally:
                release.set()
                wait_until(lambda: self.docs.worker is None)
        self.assertFalse(self.window.isVisible())

    def test_worker_uses_a_snapshot_of_input(self):
        tasks = [{"module": {"listing_pages": 10}, "authors": [{"full_name": "Тест"}]}]
        worker = GenerationWorker(tasks, self.root, self.root, "09.11.2026г.", False)
        tasks[0]["module"]["listing_pages"] = 99
        tasks[0]["authors"][0]["full_name"] = "Изменение"
        self.assertEqual(worker.tasks[0]["module"]["listing_pages"], 10)
        self.assertEqual(worker.tasks[0]["authors"][0]["full_name"], "Тест")

    def test_main_author_order_and_manually_entered_pages(self):
        first = self.docs.authors_cache[0]
        second = dict(first, full_name="Примеров Пример Примерович")
        second_id = self.docs.db.add_author(second)
        self.docs._refresh_authors_ui()
        module = self.docs.modules_data[0]
        module["author_ids"] = [first["id"], second_id]
        self.docs._refresh_modules_table()
        self.docs.tbl_modules.selectRow(0)
        self.docs._on_module_selected()
        self.docs.cmb_main_author.setCurrentIndex(self.docs.cmb_main_author.findData(second_id))
        self.assertEqual(module["main_author_id"], second_id)
        self.docs.tbl_modules.item(0, 3).setText("17")
        with patch("main.generate_module_documents", return_value=self.root / "output") as generate:
            self.docs._run_generation()
            wait_until(lambda: self.docs.worker is None)
        args = generate.call_args.kwargs
        self.assertEqual(args["main_author"]["id"], second_id)
        self.assertEqual([a["id"] for a in args["selected_authors"]], [second_id, first["id"]])
        self.assertEqual(args["module_info"]["listing_pages"], 17)
        self.assertEqual(self.docs.tbl_modules.item(0, 3).text(), "17")
        self.assertTrue(Path(args["main_author"]["signature_png"]).is_absolute())

    def test_ambiguous_database_dialog_can_select_existing_file(self):
        paths = resolve_paths(data_dir=self.root)
        options = argparse.Namespace(project_dir=None, data_dir=None, db_path=None, templates_dir=None)
        with patch("main.resolve_paths", side_effect=[AmbiguousDatabaseError([paths.db_path, self.root / "other.db"]), paths]) as resolver, patch("main.QMessageBox") as message, patch("main.QFileDialog.getOpenFileName", return_value=(str(paths.db_path), "")):
            dialog = message.return_value
            dialog.clickedButton.return_value = dialog.addButton.return_value
            self.assertIs(select_startup_paths(options), paths)
            self.assertEqual(resolver.call_args.kwargs["db_path"], str(paths.db_path))

    def test_real_entry_point_from_another_directory(self):
        entry = Path(__file__).resolve().parents[1] / "main.py"
        # Только автозакрытие для smoke-проверки; выполняется настоящий __main__.
        code = """
import runpy, sys
from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QApplication
original = QApplication.exec
def timed_exec(self):
    QTimer.singleShot(150, self.quit)
    return original()
QApplication.exec = timed_exec
entry, data = sys.argv[1:]
sys.argv = [entry, '--data-dir', data]
runpy.run_path(entry, run_name='__main__')
"""
        other = self.root / "другой cwd"
        other.mkdir()
        env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
        env["PYTHONPATH"] = str(entry.parent)
        result = subprocess.run([sys.executable, "-c", code, str(entry), str(self.root)],
                                cwd=other, env=env, capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
