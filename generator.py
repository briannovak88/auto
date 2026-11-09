# generator.py
import math
import re
import shutil
import zipfile
from pathlib import Path

import docx
from docx.shared import Mm
import openpyxl
from docxtpl import DocxTemplate, InlineImage, Listing
from pypdf import PdfReader


def sanitize_folder_name(name: str) -> str:
    """Убирает запрещенные в Windows символы из названия папки модуля."""
    cleaned = re.sub(r'[<>:"/\\|?*]', "_", name.strip())
    return cleaned.rstrip(". ") or "Module"


def clean_module_title(raw_stem: str) -> str:
    """Убирает порядковые номера ('1.', '01_', '1.2)', '№ 1 -') в начале имени файла."""
    cleaned = re.sub(r"^(?:№\s*)?\d+(?:\.\d+)*[\.\)\s_-]+", "", raw_stem.strip()).strip()
    return cleaned or raw_stem.strip()


def fix_docxtpl_tags_in_docx(docx_path: Path):
    """
    1) Исправляет пробелы после '{%' в тегах docxtpl внутри шаблона .docx:
       '{% tr ' -> '{%tr ', '{% p ' -> '{%p ', '{% tc ' -> '{%tc ', '{% r ' -> '{%r '.
    2) Автоматически подставляет метку '{{ sign_date }}' в строку 'Дата ______'
       в шаблоне 3 (Согласие на обработку персональных данных), если её там еще нет.
    """
    if not docx_path.exists():
        return

    if "3." in docx_path.name or "Согласия" in docx_path.name:
        try:
            d = docx.Document(str(docx_path))
            modified_date = False
            for p in d.paragraphs:
                if p.text.strip().startswith("Дата ") and "____" in p.text and "sign_date" not in p.text:
                    if len(p.runs) >= 2:
                        p.runs[0].text = "Дата "
                        p.runs[1].text = "{{ sign_date }}"
                        p.runs[1].underline = True
                        for r in p.runs[2:]:
                            r.text = ""
                    else:
                        p.text = "Дата {{ sign_date }}"
                    modified_date = True
            if modified_date:
                d.save(str(docx_path))
        except Exception:
            pass

    needs_update = False
    try:
        with zipfile.ZipFile(docx_path, "r") as zin:
            for item in zin.infolist():
                if item.filename.endswith(".xml"):
                    content = zin.read(item.filename).decode("utf-8", errors="ignore")
                    if re.search(r"\{%\s+(tr|p|tc|r)\b", content):
                        needs_update = True
                        break
        if not needs_update:
            return

        tmp_path = docx_path.with_suffix(".docx.tmp")
        with zipfile.ZipFile(docx_path, "r") as zin, zipfile.ZipFile(
            tmp_path, "w", zipfile.ZIP_DEFLATED
        ) as zout:
            for item in zin.infolist():
                data = zin.read(item.filename)
                if item.filename.endswith(".xml"):
                    text = data.decode("utf-8", errors="ignore")
                    new_text = re.sub(r"\{%\s+(tr|p|tc|r)\b", r"{%\1", text)
                    data = new_text.encode("utf-8")
                zout.writestr(item, data)
        shutil.move(str(tmp_path), str(docx_path))
    except Exception:
        pass


def estimate_listing_pages_for_template6(listing_text: str) -> int:
    """Резервный расчет числа страниц листинга по параметрам шаблона 6."""
    if not listing_text or not listing_text.strip():
        return 1

    chars_per_line = 84
    lines_per_page = 64
    visual_lines = 2

    for raw_line in listing_text.splitlines():
        expanded = raw_line.expandtabs(4)
        wrapped = max(1, math.ceil(len(expanded) / chars_per_line))
        visual_lines += wrapped

    return max(1, math.ceil(visual_lines / lines_per_page))


def get_exact_docx_page_count(docx_path: Path, fallback_text: str = "") -> int:
    """
    Возвращает ТОЧНОЕ количество страниц сгенерированного файла 6. Листинг.docx
    через MS Word (ComputeStatistics(2)).
    """
    try:
        import pythoncom
        import win32com.client

        pythoncom.CoInitialize()
        word = None
        doc = None
        try:
            word = win32com.client.DispatchEx("Word.Application")
            word.Visible = False
            word.DisplayAlerts = 0
            doc = word.Documents.Open(
                str(docx_path.resolve()),
                ConfirmConversions=False,
                ReadOnly=True,
                AddToRecentFiles=False,
            )
            doc.Repaginate()
            pages = int(doc.ComputeStatistics(2))  # wdStatisticPages = 2
            if pages > 0:
                return pages
        finally:
            if doc is not None:
                doc.Close(False)
            if word is not None:
                word.Quit()
            pythoncom.CoUninitialize()
    except Exception:
        pass

    return estimate_listing_pages_for_template6(fallback_text)


def convert_folder_docx_to_pdf_via_word(folder_path: Path):
    """
    Надежная офлайн-конвертация всех .docx файлов в папке в .pdf через MS Word.
    Корректно работает внутри QThread и в скомпилированном .exe (--noconsole),
    сохраняя все вставленные .png подписи авторов.
    """
    import pythoncom
    import win32com.client

    docx_files = sorted(
        p for p in folder_path.glob("*.docx") if not p.name.startswith("~$")
    )
    if not docx_files:
        return

    pythoncom.CoInitialize()
    word = None
    try:
        word = win32com.client.DispatchEx("Word.Application")
        word.Visible = False
        word.DisplayAlerts = 0

        for docx_file in docx_files:
            pdf_file = docx_file.with_suffix(".pdf")
            doc = None
            try:
                doc = word.Documents.Open(
                    str(docx_file.resolve()),
                    ConfirmConversions=False,
                    ReadOnly=True,
                    AddToRecentFiles=False,
                )
                # wdExportFormatPDF = 17, wdExportOptimizeForPrint = 0
                doc.ExportAsFixedFormat(
                    OutputFileName=str(pdf_file.resolve()),
                    ExportFormat=17,
                    OpenAfterExport=False,
                    OptimizeFor=0,
                    CreateBookmarks=0,
                )
            finally:
                if doc is not None:
                    doc.Close(False)
    finally:
        if word is not None:
            word.Quit()
        pythoncom.CoUninitialize()


def extract_text_from_docx(docx_path: Path) -> str:
    doc = docx.Document(str(docx_path))
    return "\n".join(p.text for p in doc.paragraphs)


def extract_text_from_pdf(pdf_path: Path) -> str:
    reader = PdfReader(str(pdf_path))
    extracted_pages = []
    for page in reader.pages:
        try:
            extracted_pages.append(page.extract_text() or "")
        except Exception:
            pass
    return "\n".join(extracted_pages)


def scan_listings_directory(folder_path: Path, default_year: str = "2026") -> list[dict]:
    ext_priority = {
        ".docx": 1,
        ".pdf": 2,
        ".txt": 3,
        ".py": 4,
        ".cpp": 5,
        ".cs": 6,
        ".js": 7,
    }

    candidates = []
    for entry in folder_path.iterdir():
        if not entry.is_file() or entry.name.startswith("~$"):
            continue
        ext = entry.suffix.lower()
        if ext in ext_priority:
            candidates.append(entry)

    candidates.sort(key=lambda p: (ext_priority[p.suffix.lower()], p.name.lower()))

    seen_module_names = set()
    modules = []

    for entry in candidates:
        module_name = clean_module_title(entry.stem)
        norm_name = module_name.lower()

        if norm_name in seen_module_names:
            continue
        seen_module_names.add(norm_name)

        ext = entry.suffix.lower()
        listing_text = ""
        try:
            if ext == ".pdf":
                listing_text = extract_text_from_pdf(entry)
            elif ext == ".docx":
                listing_text = extract_text_from_docx(entry)
            else:
                listing_text = entry.read_text(encoding="utf-8", errors="replace")
        except Exception as e:
            listing_text = f"// Ошибка чтения файла {entry.name}: {e}"

        pages = estimate_listing_pages_for_template6(listing_text)

        modules.append({
            "program_name": module_name,
            "creation_year": default_year,
            "listing_pages": pages,
            "source_file": str(entry),
            "listing_text": listing_text,
            "_sort_key": entry.name.lower(),
        })

    modules.sort(key=lambda m: m["_sort_key"])
    for m in modules:
        m.pop("_sort_key", None)

    return modules


def import_modules_from_excel(excel_path: Path, default_year: str = "2026") -> list[dict]:
    wb = openpyxl.load_workbook(str(excel_path), data_only=True)
    ws = wb.active
    modules = []
    seen_names = set()

    rows = list(ws.iter_rows(values_only=True))
    if not rows:
        return modules

    start_idx = 0
    first_row_str = " ".join(str(c or "").lower() for c in rows[0])
    if any(k in first_row_str for k in ["название", "модул", "программ", "наименование"]):
        start_idx = 1

    for row in rows[start_idx:]:
        non_empty = [c for c in row if c is not None and str(c).strip() != ""]
        if not non_empty:
            continue

        if len(row) >= 2 and isinstance(row[0], (int, float)) and isinstance(row[1], str):
            prog_name = clean_module_title(str(row[1]))
            pages_val = row[2] if len(row) > 2 else 10
            year_val = row[3] if len(row) > 3 else default_year
        else:
            prog_name = clean_module_title(str(non_empty[0]))
            pages_val = non_empty[1] if len(non_empty) > 1 else 10
            year_val = non_empty[2] if len(non_empty) > 2 else default_year

        norm_name = prog_name.lower()
        if norm_name in seen_names:
            continue
        seen_names.add(norm_name)

        try:
            listing_pages = int(pages_val)
        except (ValueError, TypeError):
            listing_pages = 10

        modules.append({
            "program_name": prog_name,
            "creation_year": str(year_val or default_year).strip(),
            "listing_pages": listing_pages,
            "source_file": "",
            "listing_text": "",
        })

    return modules


def calculate_pages_counts(authors_count: int, listing_pages: int) -> dict:
    if authors_count <= 1:
        addendum_pages = 1
    elif authors_count <= 4:
        addendum_pages = 2
    else:
        addendum_pages = 2 + math.ceil((authors_count - 4) / 3)

    return {
        "listing_pages": max(1, int(listing_pages)),
        "addendum_pages": addendum_pages,
        "consent_pd_pages": max(1, authors_count),
        "consent_info_pages": max(1, authors_count),
    }


def resolve_template_path(templates_dir: Path, primary_name: str, fallback_name: str) -> Path:
    p1 = templates_dir / primary_name
    if p1.exists():
        return p1
    return templates_dir / fallback_name


def attach_signature_images_for_doc(
    doc: DocxTemplate, authors: list[dict], main_author: dict
) -> tuple[list[dict], dict]:
    prepared_authors = []
    for a in authors:
        a_copy = dict(a)
        png_path = a_copy.get("signature_png", "")
        if png_path and Path(png_path).exists():
            a_copy["signature_img"] = InlineImage(doc, str(Path(png_path).resolve()), height=Mm(12))
        else:
            a_copy["signature_img"] = ""
        prepared_authors.append(a_copy)

    main_copy = dict(main_author)
    main_png = main_copy.get("signature_png", "")
    if main_png and Path(main_png).exists():
        main_copy["signature_img"] = InlineImage(doc, str(Path(main_png).resolve()), height=Mm(12))
    else:
        main_copy["signature_img"] = ""

    return prepared_authors, main_copy


def generate_module_documents(
    index: int,
    module_info: dict,
    selected_authors: list[dict],
    main_author: dict,
    templates_dir: Path,
    output_base_dir: Path,
    sign_date: str = "03.11.2026г.",
    convert_pdf: bool = False,
) -> Path:
    program_name = module_info["program_name"].strip()
    creation_year = str(module_info.get("creation_year", "2026")).strip()
    listing_pages = int(module_info.get("listing_pages", 10))
    listing_text = module_info.get("listing_text", "")

    folder_title = sanitize_folder_name(f"{index}. {program_name}")
    module_out_dir = output_base_dir / folder_title
    module_out_dir.mkdir(parents=True, exist_ok=True)

    # ШАГ 1: Сначала создаем 6. Листинг.docx и узнаем его реальное количество страниц
    tpl_listing_path = resolve_template_path(
        templates_dir, "6. Листинг (шаблон).docx", "6. Листинг.docx"
    )
    if not tpl_listing_path.exists():
        raise FileNotFoundError(f"Не найден файл шаблона: {tpl_listing_path}")

    fix_docxtpl_tags_in_docx(tpl_listing_path)
    doc_listing = DocxTemplate(str(tpl_listing_path))
    doc_listing.render({
        "program_name": program_name,
        "listing_text": Listing(listing_text) if listing_text else "",
    })
    out_listing_path = module_out_dir / "6. Листинг.docx"
    doc_listing.save(str(out_listing_path))

    if listing_text and listing_text.strip():
        listing_pages = get_exact_docx_page_count(out_listing_path, listing_text)
        module_info["listing_pages"] = listing_pages

    counts = calculate_pages_counts(len(selected_authors), listing_pages)

    # ШАГ 2: Генерируем остальные 5 документов
    other_templates = [
        ("1. Заявление лицо (шаблон).docx", "1. Заявление лицо.docx", "1. Заявление.docx"),
        ("2. Дополнение к заявлению (шаблон).docx", "2. Дополнение к заявлению.docx", "2. Дополнение к заявлению.docx"),
        ("3. Согласия (паспорта) (шаблон).docx", "3. Согласия (паспорта).docx", "3. Согласие перс данных.docx"),
        ("4. Указания сведений (шаблон).docx", "4. Указания сведений.docx", "4. Согласие указание сведений.docx"),
        ("5. Ходатайство об освобождении платы (шаблон).docx", "5. Ходатайство об освобождении платы.docx", "5. Ходатайство.docx"),
    ]

    for tpl_primary, tpl_fallback, out_filename in other_templates:
        tpl_path = resolve_template_path(templates_dir, tpl_primary, tpl_fallback)
        if not tpl_path.exists():
            raise FileNotFoundError(f"Не найден файл шаблона: {tpl_path}")

        fix_docxtpl_tags_in_docx(tpl_path)

        doc = DocxTemplate(str(tpl_path))
        authors_with_sig, main_with_sig = attach_signature_images_for_doc(
            doc, selected_authors, main_author
        )

        context = {
            "program_name": program_name,
            "creation_year": creation_year,
            "authors_count": len(selected_authors),
            "main_author": main_with_sig,
            "authors": authors_with_sig,
            "listing_pages": counts["listing_pages"],
            "addendum_pages": counts["addendum_pages"],
            "consent_pd_pages": counts["consent_pd_pages"],
            "consent_info_pages": counts["consent_info_pages"],
            "sign_date": sign_date,
        }

        doc.render(context)
        out_path = module_out_dir / out_filename
        doc.save(str(out_path))

    # ШАГ 3: Конвертация всех созданных .docx в .pdf (включая подписи) через MS Word
    if convert_pdf:
        convert_folder_docx_to_pdf_via_word(module_out_dir)

    return module_out_dir