# db.py
import os
import shutil
import sqlite3
from uuid import uuid4
from pathlib import Path

from app_paths import data_root_for_database, resolve_data_file, resolve_paths

DB_PATH = Path(__file__).resolve().parent / "database/authors.db"
SIGNATURES_PNG_DIR = DB_PATH.parent.parent / "signatures/png"
SIGNATURES_PFX_DIR = DB_PATH.parent.parent / "signatures/pfx"


def compute_initials(full_name: str) -> tuple[str, str]:
    """
    Из полного ФИО ('Иванов Иван Иванович') автоматически формирует:
    1) short_name_first: 'И.Иванов' (для Согласия на указание сведений)
    2) short_name_end:   'Иванов И.И.' (для Заявления)
    """
    parts = [p.strip() for p in full_name.strip().split() if p.strip()]
    if not parts:
        return "", ""
    if len(parts) == 1:
        return parts[0], parts[0]

    surname = parts[0]
    first_initial = f"{parts[1][0].upper()}."
    patronymic_initial = f"{parts[2][0].upper()}." if len(parts) > 2 else ""

    short_name_first = f"{first_initial}{surname}"
    short_name_end = f"{surname} {first_initial}{patronymic_initial}"
    return short_name_first, short_name_end


def build_passport_string(
    series: str, number: str, issued_by: str, issue_date: str, code: str
) -> str:
    """
    Автоматически собирает полную строку паспорта по отдельным полям:
    'паспорт РФ серия 9999 №999999, выдан ..., дата выдачи: 11.01.2006 г., код подразделения: 999-999'
    """
    s = series.strip()
    n = number.strip()
    by = issued_by.strip()
    dt = issue_date.strip()
    cd = code.strip()

    if not any([s, n, by, dt, cd]):
        return ""

    dt_formatted = f"{dt} г." if dt and not dt.endswith("г.") else dt
    return (
        f"паспорт РФ серия {s} №{n}, выдан {by}, "
        f"дата выдачи: {dt_formatted}, код подразделения: {cd}"
    )


def copy_signature_file(
    src_path: str, kind: str, full_name: str, *, data_dir: Path | None = None
) -> str:
    """
    Копирует выбранный файл подписи (.png или .pfx) в локальную папку программы
    signatures/png или signatures/pfx и возвращает относительный путь.
    """
    if not src_path:
        return ""
    root = Path(data_dir).resolve() if data_dir is not None else resolve_paths().data_dir
    src = resolve_data_file(src_path, root)
    if not src.exists():
        return src_path  # Не теряем старую ссылку при временно недоступном файле.

    if kind not in ("png", "pfx"):
        raise ValueError(f"Неизвестный тип подписи: {kind}")
    target_dir = root / "signatures" / kind
    target_dir.mkdir(parents=True, exist_ok=True)

    # Если файл уже лежит внутри папки signatures — не копируем повторно
    try:
        if src.resolve().parent == target_dir.resolve():
            return src.relative_to(root).as_posix()
    except Exception:
        pass

    safe_name = "".join(c if c.isalnum() else "_" for c in full_name.strip()) or "author"
    dst_name = f"{safe_name}_{uuid4().hex}{src.suffix.lower()}"
    dst = target_dir / dst_name
    shutil.copy2(src, dst)
    return dst.relative_to(root).as_posix()


class AuthorDB:
    def __init__(self, db_path: Path | None = None, *, data_dir: Path | None = None):
        if db_path is None:
            paths = resolve_paths(data_dir=data_dir)
            self.db_path = paths.db_path
            self.data_dir = paths.data_dir
        else:
            self.db_path = Path(db_path).resolve()
            self.data_dir = Path(data_dir).resolve() if data_dir is not None else data_root_for_database(self.db_path)
        os.makedirs(self.db_path.parent, exist_ok=True)
        (self.data_dir / "signatures/png").mkdir(parents=True, exist_ok=True)
        (self.data_dir / "signatures/pfx").mkdir(parents=True, exist_ok=True)
        self._init_table()

    def resolve_signature_path(self, value: str) -> Path:
        return resolve_data_file(value, self.data_dir)

    def _get_conn(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_table(self):
        with self._get_conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS authors (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    full_name TEXT NOT NULL,
                    short_name_first TEXT NOT NULL,
                    short_name_end TEXT NOT NULL,
                    birth_day TEXT NOT NULL,
                    birth_month TEXT NOT NULL,
                    birth_year TEXT NOT NULL,
                    citizenship TEXT NOT NULL DEFAULT 'Российская Федерация',
                    address TEXT NOT NULL,
                    passport_info TEXT NOT NULL,
                    pass_series TEXT DEFAULT '',
                    pass_number TEXT DEFAULT '',
                    pass_issued_by TEXT DEFAULT '',
                    pass_issue_date TEXT DEFAULT '',
                    pass_code TEXT DEFAULT '',
                    phone TEXT DEFAULT '',
                    email TEXT DEFAULT '',
                    contribution TEXT DEFAULT 'Разработка программного обеспечения.',
                    signature_png TEXT DEFAULT '',
                    signature_pfx TEXT DEFAULT ''
                )
            """)
            # Автоматическая миграция для уже существующей БД
            existing_cols = {
                row["name"] for row in conn.execute("PRAGMA table_info(authors)").fetchall()
            }
            new_columns = {
                "pass_series": "TEXT DEFAULT ''",
                "pass_number": "TEXT DEFAULT ''",
                "pass_issued_by": "TEXT DEFAULT ''",
                "pass_issue_date": "TEXT DEFAULT ''",
                "pass_code": "TEXT DEFAULT ''",
                "signature_png": "TEXT DEFAULT ''",
                "signature_pfx": "TEXT DEFAULT ''",
            }
            for col_name, col_def in new_columns.items():
                if col_name not in existing_cols:
                    conn.execute(f"ALTER TABLE authors ADD COLUMN {col_name} {col_def}")
            conn.commit()

    def get_all_authors(self) -> list[dict]:
        with self._get_conn() as conn:
            rows = conn.execute("SELECT * FROM authors ORDER BY id ASC").fetchall()
            return [dict(r) for r in rows]

    def add_author(self, data: dict) -> int:
        short_first, short_end = compute_initials(data["full_name"])
        passport_info = build_passport_string(
            data.get("pass_series", ""),
            data.get("pass_number", ""),
            data.get("pass_issued_by", ""),
            data.get("pass_issue_date", ""),
            data.get("pass_code", ""),
        ) or data.get("passport_info", "").strip()

        sig_png = copy_signature_file(data.get("signature_png", ""), "png", data["full_name"], data_dir=self.data_dir)
        sig_pfx = copy_signature_file(data.get("signature_pfx", ""), "pfx", data["full_name"], data_dir=self.data_dir)

        with self._get_conn() as conn:
            cur = conn.execute("""
                INSERT INTO authors (
                    full_name, short_name_first, short_name_end,
                    birth_day, birth_month, birth_year,
                    citizenship, address, passport_info,
                    pass_series, pass_number, pass_issued_by, pass_issue_date, pass_code,
                    phone, email, contribution,
                    signature_png, signature_pfx
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                data["full_name"].strip(),
                short_first,
                short_end,
                data["birth_day"].strip().zfill(2),
                data["birth_month"].strip().zfill(2),
                data["birth_year"].strip(),
                data.get("citizenship", "Российская Федерация").strip(),
                data["address"].strip(),
                passport_info,
                data.get("pass_series", "").strip(),
                data.get("pass_number", "").strip(),
                data.get("pass_issued_by", "").strip(),
                data.get("pass_issue_date", "").strip(),
                data.get("pass_code", "").strip(),
                data.get("phone", "").strip(),
                data.get("email", "").strip(),
                data.get("contribution", "Разработка программного обеспечения.").strip(),
                sig_png,
                sig_pfx,
            ))
            conn.commit()
            return cur.lastrowid

    def update_author(self, author_id: int, data: dict):
        short_first, short_end = compute_initials(data["full_name"])
        passport_info = build_passport_string(
            data.get("pass_series", ""),
            data.get("pass_number", ""),
            data.get("pass_issued_by", ""),
            data.get("pass_issue_date", ""),
            data.get("pass_code", ""),
        ) or data.get("passport_info", "").strip()

        sig_png = copy_signature_file(data.get("signature_png", ""), "png", data["full_name"], data_dir=self.data_dir)
        sig_pfx = copy_signature_file(data.get("signature_pfx", ""), "pfx", data["full_name"], data_dir=self.data_dir)

        with self._get_conn() as conn:
            conn.execute("""
                UPDATE authors SET
                    full_name = ?,
                    short_name_first = ?,
                    short_name_end = ?,
                    birth_day = ?,
                    birth_month = ?,
                    birth_year = ?,
                    citizenship = ?,
                    address = ?,
                    passport_info = ?,
                    pass_series = ?,
                    pass_number = ?,
                    pass_issued_by = ?,
                    pass_issue_date = ?,
                    pass_code = ?,
                    phone = ?,
                    email = ?,
                    contribution = ?,
                    signature_png = ?,
                    signature_pfx = ?
                WHERE id = ?
            """, (
                data["full_name"].strip(),
                short_first,
                short_end,
                data["birth_day"].strip().zfill(2),
                data["birth_month"].strip().zfill(2),
                data["birth_year"].strip(),
                data.get("citizenship", "Российская Федерация").strip(),
                data["address"].strip(),
                passport_info,
                data.get("pass_series", "").strip(),
                data.get("pass_number", "").strip(),
                data.get("pass_issued_by", "").strip(),
                data.get("pass_issue_date", "").strip(),
                data.get("pass_code", "").strip(),
                data.get("phone", "").strip(),
                data.get("email", "").strip(),
                data.get("contribution", "Разработка программного обеспечения.").strip(),
                sig_png,
                sig_pfx,
                author_id,
            ))
            conn.commit()

    def delete_author(self, author_id: int):
        with self._get_conn() as conn:
            conn.execute("DELETE FROM authors WHERE id = ?", (author_id,))
            conn.commit()
