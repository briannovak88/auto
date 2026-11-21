"""Только синтетические данные; файлы создаются во временных папках."""
import sqlite3
import struct
import zlib
from pathlib import Path

from docx import Document


TEMPLATE_NAMES = (
    "1. Заявление лицо (шаблон).docx",
    "2. Дополнение к заявлению (шаблон).docx",
    "3. Согласия (паспорта) (шаблон).docx",
    "4. Указания сведений (шаблон).docx",
    "5. Ходатайство об освобождении платы (шаблон).docx",
    "6. Листинг (шаблон).docx",
)
OUTPUT_NAMES = {
    "1. Заявление.docx", "2. Дополнение к заявлению.docx",
    "3. Согласие перс данных.docx", "4. Согласие указание сведений.docx",
    "5. Ходатайство.docx", "6. Листинг.docx",
}


def make_png(path: Path):
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", 2, 2, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(b"\x00\x00\x00\x00\xff\xff\xff" * 2))
        + chunk(b"IEND", b"")
    )


def make_templates(folder: Path):
    folder.mkdir(parents=True, exist_ok=True)
    for number, name in enumerate(TEMPLATE_NAMES, start=1):
        doc = Document()
        doc.add_paragraph("{{ program_name }}")
        if number == 6:
            doc.add_paragraph("{{ listing_text }}")
        else:
            doc.add_paragraph("{{ creation_year }}; листов: {{ listing_pages }}; авторов: {{ authors_count }}")
            doc.add_paragraph("{{ main_author.full_name }} {{ main_author.signature_img }}")
            # Пробел в {% p ... %} специально требует старого исправления.
            doc.add_paragraph("{% p for author in authors %}")
            doc.add_paragraph("{{ author.full_name }} {{ author.signature_img }}")
            doc.add_paragraph("{% p endfor %}")
            if number == 3:
                line = doc.add_paragraph()
                line.add_run("Дата ")
                line.add_run("___________").underline = True
            else:
                doc.add_paragraph("Дата {{ sign_date }}")
        doc.save(folder / name)


def make_legacy_database(root: Path, *, with_signatures: bool = True) -> Path:
    path = root / "database/authors.db"
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as conn:
        conn.execute("""CREATE TABLE authors (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            full_name TEXT NOT NULL, short_name_first TEXT NOT NULL,
            short_name_end TEXT NOT NULL, birth_day TEXT NOT NULL,
            birth_month TEXT NOT NULL, birth_year TEXT NOT NULL,
            citizenship TEXT NOT NULL, address TEXT NOT NULL,
            passport_info TEXT NOT NULL, phone TEXT DEFAULT '', email TEXT DEFAULT '',
            contribution TEXT DEFAULT 'Тестовая разработка'
        )""")
        conn.execute("""INSERT INTO authors (
            full_name, short_name_first, short_name_end, birth_day, birth_month,
            birth_year, citizenship, address, passport_info
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""", (
            "Тестов Тест Тестович", "Т.Тестов", "Тестов Т.Т.", "01", "01", "2000",
            "Тестовое гражданство", "Учебный адрес",
            "паспорт РФ серия 0000 №000000, выдан Тестовый орган, дата выдачи: 01.01.2020 г., код подразделения: 000-000",
        ))
        if with_signatures:
            conn.execute("ALTER TABLE authors ADD COLUMN signature_png TEXT DEFAULT ''")
            conn.execute("ALTER TABLE authors ADD COLUMN signature_pfx TEXT DEFAULT ''")
            conn.execute("UPDATE authors SET signature_png=?, signature_pfx=?", (
                "signatures\\png\\test.png", "signatures/pfx/test.pfx",
            ))
            make_png(root / "signatures/png/test.png")
            (root / "signatures/pfx").mkdir(parents=True)
            (root / "signatures/pfx/test.pfx").write_bytes(b"SYNTHETIC: NOT A CERTIFICATE")
    return path
