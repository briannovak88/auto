import hashlib
import os
import shutil
import sqlite3
import sys
import tempfile
import types
import unittest
import zipfile
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import Mock, patch

from docx import Document
from openpyxl import Workbook
from pypdf import PdfWriter

import app_paths
from app_paths import AmbiguousDatabaseError, resolve_paths
from db import AuthorDB
from generator import (
    convert_folder_docx_to_pdf_via_word, estimate_listing_pages_for_template6,
    generate_module_documents, get_exact_docx_page_count,
    import_modules_from_excel, prepared_template, scan_listings_directory,
)
from tests.fixtures import OUTPUT_NAMES, TEMPLATE_NAMES, make_legacy_database, make_templates


@contextmanager
def changed_directory(path):
    previous = Path.cwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(previous)


@contextmanager
def fake_word():
    pythoncom = types.ModuleType("pythoncom")
    pythoncom.CoInitialize = Mock()
    pythoncom.CoUninitialize = Mock()
    client = types.ModuleType("win32com.client")
    client.DispatchEx = Mock()
    win32com = types.ModuleType("win32com")
    win32com.client = client
    with patch.dict(sys.modules, {"pythoncom": pythoncom, "win32com": win32com, "win32com.client": client}):
        yield pythoncom, client


class CoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="automat-тест ")
        self.root = Path(self.temp.name)
        self.addCleanup(self.temp.cleanup)
        self.env = patch.dict(os.environ, {
            key: "" for key in (
                "AUTOMAT_DOCS_PROJECT_DIR", "AUTOMAT_DOCS_DATA_DIR",
                "AUTOMAT_DOCS_DB_PATH", "AUTOMAT_DOCS_TEMPLATES_DIR",
            )
        })
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_six_documents_legacy_database_signatures_and_unchanged_templates(self):
        legacy = make_legacy_database(self.root / "оригинал")
        original_hash = hashlib.sha256(legacy.read_bytes()).hexdigest()
        data = self.root / "копия данных"
        shutil.copytree(legacy.parent.parent, data)
        templates = data / "templates"
        make_templates(templates)
        before = {p.name: p.read_bytes() for p in templates.iterdir()}
        unrelated = self.root / "другая папка"
        unrelated.mkdir()
        with changed_directory(unrelated):
            paths = resolve_paths(data_dir=data)
            db = AuthorDB(paths.db_path, data_dir=paths.data_dir)
            authors = db.get_all_authors()
            self.assertTrue(db.resolve_signature_path(authors[0]["signature_png"]).is_file())
            self.assertTrue(db.resolve_signature_path(authors[0]["signature_pfx"]).is_file())
            self.assertEqual(authors[0]["pass_series"], "")
            module = {"program_name": "Тестовый модуль", "creation_year": "2026", "listing_pages": 99,
                      "listing_text": "\n".join('print("тест <>&")' for _ in range(150))}
            # Принудительно проверяем резервный расчёт, независимо от наличия Word.
            with patch.dict(sys.modules, {"pythoncom": None}):
                output = generate_module_documents(1, module, authors, authors[0], templates,
                                                   paths.output_dir, "09.11.2026г.", data_dir=data)
        self.assertEqual({p.name for p in output.iterdir()}, OUTPUT_NAMES)
        self.assertEqual(module["listing_pages"], estimate_listing_pages_for_template6(module["listing_text"]))
        text = "\n".join(p.text for p in Document(output / "3. Согласие перс данных.docx").paragraphs)
        self.assertIn("Дата 09.11.2026г.", text)
        self.assertIn("Тестов Тест Тестович", text)
        self.assertIn(f"листов: {module['listing_pages']}", text)
        for name in OUTPUT_NAMES - {"6. Листинг.docx"}:
            with zipfile.ZipFile(output / name) as archive:
                self.assertTrue(any(p.startswith("word/media/") for p in archive.namelist()), name)
        listing = "\n".join(p.text for p in Document(output / "6. Листинг.docx").paragraphs)
        self.assertIn('print("тест <>&")', listing)
        self.assertEqual(before, {p.name: p.read_bytes() for p in templates.iterdir()})
        self.assertEqual(original_hash, hashlib.sha256(legacy.read_bytes()).hexdigest())
        with sqlite3.connect(paths.db_path) as conn:
            self.assertEqual(conn.execute("SELECT signature_png FROM authors").fetchone()[0], "signatures\\png\\test.png")

    def test_old_schema_without_signature_columns_is_readable(self):
        database = make_legacy_database(self.root, with_signatures=False)
        author = AuthorDB(database).get_all_authors()[0]
        self.assertEqual(author["signature_png"], "")
        self.assertEqual(author["signature_pfx"], "")
        self.assertIn("серия 0000", author["passport_info"])

    def test_signature_paths_remain_relative_and_do_not_duplicate_on_update(self):
        db = AuthorDB(make_legacy_database(self.root))
        author = db.get_all_authors()[0]
        db.update_author(author["id"], author)
        self.assertEqual(db.get_all_authors()[0]["signature_png"], "signatures/png/test.png")
        self.assertEqual(len(list((self.root / "signatures/png").iterdir())), 1)
        # Недоступная ссылка сохраняется при редактировании других полей.
        author["signature_png"] = "signatures/png/missing.png"
        db.update_author(author["id"], author)
        self.assertEqual(db.get_all_authors()[0]["signature_png"], author["signature_png"])

    def test_copy_new_signature_and_preserve_absolute_legacy_path(self):
        database = make_legacy_database(self.root)
        db = AuthorDB(database)
        author = db.get_all_authors()[0]
        external = self.root / "external.png"
        shutil.copy2(self.root / "signatures/png/test.png", external)
        with sqlite3.connect(database) as conn:
            conn.execute("UPDATE authors SET signature_png=?", (str(external),))
        self.assertEqual(db.resolve_signature_path(db.get_all_authors()[0]["signature_png"]), external)
        author["signature_png"] = str(external)
        db.update_author(author["id"], author)
        stored = db.get_all_authors()[0]["signature_png"]
        self.assertFalse(Path(stored).is_absolute())
        self.assertEqual(db.resolve_signature_path(stored).read_bytes(), external.read_bytes())

    def test_temporary_template_keeps_name_and_cleans_up(self):
        make_templates(self.root)
        original = self.root / TEMPLATE_NAMES[2]
        before = original.read_bytes()
        original.chmod(0o444)
        self.addCleanup(original.chmod, 0o600)
        with prepared_template(original) as copy:
            self.assertEqual(copy.name, original.name)
            with zipfile.ZipFile(copy) as archive:
                xml = archive.read("word/document.xml").decode()
            self.assertIn("{%p for", xml)
            self.assertIn("{{ sign_date }}", xml)
        self.assertFalse(copy.exists())
        self.assertEqual(before, original.read_bytes())
        with self.assertRaisesRegex(RuntimeError, "Тест"):
            with prepared_template(original) as failed_copy:
                raise RuntimeError("Тест")
        self.assertFalse(failed_copy.exists())
        self.assertEqual(before, original.read_bytes())

    def test_folder_and_excel_import(self):
        listing = Document()
        listing.add_paragraph("Тестовый код")
        listing.save(self.root / "01_Альфа.docx")
        (self.root / "1. Альфа.txt").write_text("дубль", encoding="utf-8")
        (self.root / "02_Бета.py").write_text("print('тест')", encoding="utf-8")
        writer = PdfWriter()
        writer.add_blank_page(width=100, height=100)
        writer.write(self.root / "03_Гамма.pdf")
        modules = scan_listings_directory(self.root)
        self.assertEqual({m["program_name"] for m in modules}, {"Альфа", "Бета", "Гамма"})
        self.assertEqual(next(m for m in modules if m["program_name"] == "Альфа")["listing_text"], "Тестовый код")
        workbook = Workbook()
        sheet = workbook.active
        sheet.append(["№", "Название", "Листов", "Год"])
        sheet.append([1, "Альфа", 3, 2024])
        sheet.append([2, "Альфа", 5, 2025])
        sheet.append([3, "Бета", 7, 2026])
        workbook.save(self.root / "модули.xlsx")
        imported = import_modules_from_excel(self.root / "модули.xlsx")
        self.assertEqual([(m["program_name"], m["listing_pages"], m["creation_year"]) for m in imported],
                         [("Альфа", 3, "2024"), ("Бета", 7, "2026")])

    def test_paths_project_data_environment_and_ambiguity(self):
        project = self.root / "проект"
        cwd = self.root / "cwd"
        make_legacy_database(project)
        make_legacy_database(cwd)
        (project / "templates").mkdir()
        with changed_directory(cwd), patch.object(app_paths, "__file__", str(project / "app_paths.py")), patch.dict(os.environ, {"LOCALAPPDATA": str(self.root / "local")}):
            with self.assertRaises(AmbiguousDatabaseError) as caught:
                resolve_paths()
            self.assertEqual(set(caught.exception.candidates), {project / "database/authors.db", cwd / "database/authors.db"})
            self.assertEqual(resolve_paths(project_dir=project).db_path, project / "database/authors.db")
            self.assertEqual(resolve_paths(data_dir=project).templates_dir, project / "templates")
            self.assertEqual(resolve_paths(db_path=project / "database/authors.db").data_dir, project)
            with patch.dict(os.environ, {"AUTOMAT_DOCS_DATA_DIR": str(project)}):
                self.assertEqual(resolve_paths().db_path, project / "database/authors.db")
            with self.assertRaises(FileNotFoundError):
                resolve_paths(db_path=self.root / "missing.db")

    def test_frozen_resources_are_separate_from_user_data(self):
        install, bundle = self.root / "install", self.root / "bundle"
        install.mkdir()
        (bundle / "templates").mkdir(parents=True)
        cwd = self.root / "empty"
        cwd.mkdir()
        with changed_directory(cwd), patch.object(sys, "frozen", True, create=True), patch.object(sys, "_MEIPASS", str(bundle), create=True), patch.object(sys, "executable", str(install / "Automat docs.exe")), patch.dict(os.environ, {"LOCALAPPDATA": str(self.root / "local")}):
            paths = resolve_paths()
            self.assertEqual(paths.data_dir, self.root / "local/Automat docs")
            self.assertEqual(paths.templates_dir, bundle / "templates")
            self.assertNotEqual(paths.db_path.parent, bundle)
            make_legacy_database(install)
            self.assertEqual(resolve_paths().data_dir, install)

    def test_word_page_count_and_cleanup_with_mock_com(self):
        with fake_word() as (com, client):
            word = client.DispatchEx.return_value
            document = word.Documents.Open.return_value
            document.ComputeStatistics.return_value = 7
            self.assertEqual(get_exact_docx_page_count(self.root / "listing.docx", "тест"), 7)
            document.Repaginate.assert_called_once()
            document.Close.assert_called_once_with(False)
            word.Quit.assert_called_once()
            com.CoUninitialize.assert_called_once()
            document.Close.side_effect = RuntimeError("Ошибка закрытия")
            self.assertEqual(get_exact_docx_page_count(self.root / "listing.docx", "тест"), 1)
            self.assertEqual(word.Quit.call_count, 2)
            self.assertEqual(com.CoUninitialize.call_count, 2)

    def test_pdf_missing_word_keeps_docx_and_explains_requirement(self):
        path = self.root / "document.docx"
        Document().save(path)
        original = path.read_bytes()
        with patch.dict(sys.modules, {"pythoncom": None}):
            with self.assertRaisesRegex(RuntimeError, "Microsoft Word.*pywin32"):
                convert_folder_docx_to_pdf_via_word(self.root)
        self.assertEqual(original, path.read_bytes())
        with fake_word() as (com, client):
            client.DispatchEx.side_effect = RuntimeError("Word отсутствует")
            with self.assertRaisesRegex(RuntimeError, "DOCX уже сохранены"):
                convert_folder_docx_to_pdf_via_word(self.root)
            com.CoUninitialize.assert_called_once()

    def test_pdf_word_export_arguments_with_mock_com(self):
        for name in OUTPUT_NAMES:
            Document().save(self.root / name)
        with fake_word() as (com, client):
            convert_folder_docx_to_pdf_via_word(self.root)
            document = client.DispatchEx.return_value.Documents.Open.return_value
            self.assertEqual(document.ExportAsFixedFormat.call_count, 6)
            self.assertEqual(document.Close.call_count, 6)
            for call in document.ExportAsFixedFormat.call_args_list:
                self.assertEqual(call.kwargs["ExportFormat"], 17)
                self.assertTrue(Path(call.kwargs["OutputFileName"]).is_absolute())
            com.CoUninitialize.assert_called_once()


if __name__ == "__main__":
    unittest.main()
