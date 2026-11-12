"""Однократная подготовка или офлайн-проверка моделей EasyOCR ru/en."""
import argparse
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-dir", required=True, type=Path)
    parser.add_argument("--check", action="store_true", help="Проверить без скачивания")
    args = parser.parse_args()
    try:
        import easyocr
        args.model_dir.mkdir(parents=True, exist_ok=True)
        easyocr.Reader(["ru", "en"], gpu=False, model_storage_directory=str(args.model_dir.resolve()),
                       user_network_directory=str(args.model_dir.resolve() / "user_network"),
                       download_enabled=not args.check, verbose=True)
    except Exception as exc:
        parser.exit(1, f"Не удалось подготовить модели: {exc}\n")
    print(f"Модели ru/en готовы: {args.model_dir.resolve()}")


if __name__ == "__main__":
    main()
