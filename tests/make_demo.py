"""Создать отдельную синтетическую папку для ручной проверки на Windows."""
import argparse
from pathlib import Path

from openpyxl import Workbook

from tests.fixtures import make_legacy_database, make_templates


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dest", required=True, type=Path, help="Новая, ещё не существующая папка")
    args = parser.parse_args()
    destination = args.dest.expanduser().resolve()
    if destination.exists():
        parser.error(f"Папка уже существует: {destination}. Выберите новую папку для тестовых данных.")
    destination.mkdir(parents=True)
    make_legacy_database(destination)
    make_templates(destination / "templates")
    listings = destination / "листинги"
    listings.mkdir()
    (listings / "01_Тестовый модуль.py").write_text(
        "# Синтетический листинг\n" + "print('тест')\n" * 150, encoding="utf-8"
    )
    workbook = Workbook()
    workbook.active.append(["№", "Название", "Листов", "Год"])
    workbook.active.append([1, "Тестовый модуль Excel", 3, 2026])
    workbook.save(destination / "модули.xlsx")
    print(f"Тестовые данные созданы: {destination}")
    print("PFX содержит тестовые байты, это не сертификат и не электронная подпись.")


if __name__ == "__main__":
    main()
