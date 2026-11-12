import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from ocr_engine import Cancelled, EasyOcrBackend, OcrSettings, process_batch
from tests.ocr_fixtures import make_pdf


HAS_PDF = importlib.util.find_spec("pymupdf") is not None


@unittest.skipUnless(HAS_PDF, "Установите requirements-ocr.txt для тестов PDF/OCR")
class OcrTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="ocr тест ")
        self.root = Path(self.temp.name)
        self.addCleanup(self.temp.cleanup)
        self.settings = OcrSettings(self.root / "модели")

    def test_text_layer_order_boundaries_and_blank_page_without_models(self):
        first = make_pdf(self.root / "Бета.pdf", ("text", "blank"), "FIRST DOCUMENT")
        second = make_pdf(self.root / "Альфа.pdf", text="SECOND DOCUMENT")
        backend = Mock()
        events = []
        output = self.root / "result"
        result = process_batch([first, second], output, self.settings, events.append, backend=backend)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["pages_done"], 3)
        self.assertEqual([p["method"] for p in result["files"][0]["pages"]], ["text", "blank"])
        backend.recognize.assert_not_called()
        text = (output / result["text_path"]).read_text(encoding="utf-8")
        self.assertLess(text.index("Бета.pdf"), text.index("Альфа.pdf"))
        self.assertIn("Страница 2", text)
        self.assertIn("FIRST DOCUMENT", text)
        self.assertIn("SECOND DOCUMENT", text)
        from docx import Document
        self.assertIn("Файл 1: Бета.pdf", "\n".join(p.text for p in Document(output / result["docx_path"]).paragraphs))
        self.assertEqual(events[-1]["event"], "finished")
        self.assertLess(max(len(json.dumps(event)) for event in events), 2000)

    def test_scan_and_mixed_pages_share_backend(self):
        source = make_pdf(self.root / "смешанный файл.pdf", ("text", "scan", "scan"))
        backend = Mock()
        backend.recognize.side_effect = ["Первый скан · English", "Второй скан"]
        output = self.root / "result"
        result = process_batch([source], output, self.settings, backend=backend)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(backend.recognize.call_count, 2)
        self.assertEqual([p["method"] for p in result["files"][0]["pages"]], ["text", "ocr", "ocr"])
        self.assertIn("Первый скан", (output / result["text_path"]).read_text(encoding="utf-8"))

    def test_corrupt_file_and_page_failure_do_not_stop_other_files(self):
        bad = self.root / "повреждённый.pdf"
        bad.write_bytes(b"NOT A PDF")
        scan = make_pdf(self.root / "scan.pdf", ("scan",))
        good = make_pdf(self.root / "good.pdf")
        backend = Mock()
        backend.recognize.side_effect = RuntimeError("Тестовая ошибка модели")
        result = process_batch([bad, scan, good], self.root / "result", self.settings, backend=backend)
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["errors"], 2)
        self.assertEqual(result["files"][2]["status"], "completed")
        self.assertEqual(result["docx_path"], "result-partial.docx")
        self.assertIn("НЕПОЛНЫЙ РЕЗУЛЬТАТ", (self.root / "result/result-partial.txt").read_text(encoding="utf-8"))

    def test_cancel_saves_partial_and_next_run_succeeds(self):
        source = make_pdf(self.root / "пакет.pdf", ("text",) * 4)
        flag = [False]

        def event(item):
            if item["event"] == "page_finished":
                flag[0] = True

        output = self.root / "cancelled"
        result = process_batch([source], output, self.settings, event, lambda: flag[0])
        self.assertEqual(result["status"], "cancelled")
        self.assertEqual(result["pages_done"], 1)
        self.assertTrue((output / "result-partial.docx").is_file())
        self.assertFalse((output / "result.docx").exists())
        repeat = process_batch([source], self.root / "repeat", self.settings)
        self.assertEqual(repeat["status"], "completed")
        self.assertEqual(repeat["pages_done"], 4)

    def test_atomic_docx_failure_and_existing_output_protection(self):
        source = make_pdf(self.root / "file.pdf")
        output = self.root / "result"
        with patch("docx.document.Document.save", side_effect=OSError("Нет места")):
            result = process_batch([source], output, self.settings)
        self.assertEqual(result["status"], "failed")
        self.assertFalse((output / "result.docx").exists())
        self.assertFalse(list(output.glob(".writing-*")))
        with self.assertRaises(FileExistsError):
            process_batch([source], output, self.settings)

    def test_reader_loaded_once_and_network_download_disabled(self):
        fake_easyocr = types.ModuleType("easyocr")
        fake_easyocr.Reader = Mock()
        fake_easyocr.Reader.return_value.readtext.return_value = ["Тест English"]
        fake_torch = types.ModuleType("torch")
        fake_torch.set_num_threads = Mock()
        fake_torch.cuda = Mock()
        backend = EasyOcrBackend(self.settings)
        with patch.dict(sys.modules, {"easyocr": fake_easyocr, "torch": fake_torch}):
            self.assertEqual(backend.recognize(b"image"), "Тест English")
            backend.recognize(b"second image")
        fake_easyocr.Reader.assert_called_once()
        self.assertFalse(fake_easyocr.Reader.call_args.kwargs["download_enabled"])
        self.assertEqual(fake_easyocr.Reader.call_args.args[0], ["ru", "en"])
        flag = [False]
        reader = Mock()

        def loading(*args, **kwargs):
            flag[0] = True
            return reader

        fake_easyocr.Reader.side_effect = loading
        with patch.dict(sys.modules, {"easyocr": fake_easyocr, "torch": fake_torch}):
            with self.assertRaises(Cancelled):
                EasyOcrBackend(self.settings, lambda: flag[0]).recognize(b"image")
        reader.readtext.assert_not_called()

    def test_missing_models_error_is_cached_and_gpu_requires_cuda(self):
        fake_easyocr = types.ModuleType("easyocr")
        fake_easyocr.Reader = Mock(side_effect=FileNotFoundError("craft_mlt_25k.pth"))
        fake_torch = types.ModuleType("torch")
        fake_torch.set_num_threads = Mock()
        fake_torch.cuda = Mock()
        fake_torch.cuda.is_available.return_value = False
        backend = EasyOcrBackend(self.settings)
        with patch.dict(sys.modules, {"easyocr": fake_easyocr, "torch": fake_torch}):
            for _ in range(2):
                with self.assertRaisesRegex(RuntimeError, "ocr_setup"):
                    backend.recognize(b"image")
            self.assertEqual(fake_easyocr.Reader.call_count, 1)
            with self.assertRaisesRegex(RuntimeError, "CUDA"):
                EasyOcrBackend(OcrSettings(self.root, gpu=True)).recognize(b"image")
            self.assertEqual(fake_easyocr.Reader.call_count, 1)

    def test_early_cancel_lists_all_inputs_and_encrypted_pdf_is_partial(self):
        first = make_pdf(self.root / "first.pdf")
        second = make_pdf(self.root / "second.pdf")
        result = process_batch([first, second], self.root / "cancel", self.settings, cancelled=lambda: True)
        self.assertEqual(result["status"], "cancelled")
        self.assertEqual(len(result["files"]), 2)
        self.assertTrue(all(item["status"] == "cancelled" for item in result["files"]))
        import pymupdf
        encrypted = self.root / "encrypted.pdf"
        with pymupdf.open(first) as pdf:
            pdf.save(encrypted, encryption=pymupdf.PDF_ENCRYPT_AES_256, owner_pw="synthetic-owner", user_pw="synthetic-user")
        result = process_batch([encrypted, second], self.root / "encrypted-result", self.settings)
        self.assertEqual(result["status"], "partial")
        self.assertIn("паролем", result["files"][0]["error"])
        self.assertEqual(result["files"][1]["status"], "completed")

    def test_cancel_during_export_never_publishes_complete_docx(self):
        source = make_pdf(self.root / "file.pdf")
        flag = [False]
        from docx.document import Document
        original = Document.save

        def saving(document, path):
            original(document, path)
            flag[0] = True

        output = self.root / "late-cancel"
        with patch("docx.document.Document.save", new=saving):
            result = process_batch([source], output, self.settings, cancelled=lambda: flag[0])
        self.assertEqual(result["status"], "cancelled")
        self.assertFalse((output / "result.docx").exists())
        self.assertFalse((output / "result.txt").exists())
        self.assertTrue((output / "result-partial.docx").is_file())

    def test_worker_actual_entry_from_other_cwd_without_qt(self):
        source = make_pdf(self.root / "путь с пробелами.pdf")
        request = self.root / "запрос.json"
        request.write_text(json.dumps({"files": [str(source)], "output_dir": str(self.root / "result"),
                                      "model_dir": str(self.root / "нет моделей"),
                                      "cancel_path": str(self.root / "cancel")}), encoding="utf-8")
        entry = Path(__file__).resolve().parents[1] / "main.py"
        env = dict(os.environ, QT_QPA_PLATFORM="not-an-actual-qt-platform")
        done = subprocess.run([sys.executable, str(entry), "--ocr-worker", str(request)],
                              cwd=self.root, env=env, capture_output=True, text=True, timeout=30)
        self.assertEqual(done.returncode, 0, done.stderr)
        events = [json.loads(line) for line in done.stdout.splitlines()]
        self.assertEqual(events[-1]["status"], "completed")
        self.assertIn("page_finished", [item["event"] for item in events])

    def test_main_and_core_import_do_not_load_optional_libraries(self):
        done = subprocess.run([sys.executable, "-c", "import main,ocr_engine; import sys; assert not any(n in sys.modules for n in ['torch','easyocr','pymupdf'])"],
                              cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, timeout=15)
        self.assertEqual(done.returncode, 0, done.stderr)


if __name__ == "__main__":
    unittest.main()
