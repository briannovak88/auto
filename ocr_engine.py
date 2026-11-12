"""Локальное пакетное OCR. Импорт модуля не загружает Qt, PyTorch и модели."""
import json
import os
import re
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class OcrSettings:
    model_dir: Path
    gpu: bool = False
    dpi: int = 200


class Cancelled(Exception):
    pass


def atomic_text(path: Path, text: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix=".writing-", suffix=".tmp")
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(text)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def save_manifest(folder: Path, manifest: dict):
    atomic_text(folder / "result.json", json.dumps(manifest, ensure_ascii=False, indent=2))


class EasyOcrBackend:
    """Один Reader на пакет; загрузки из сети при обработке запрещены."""
    def __init__(self, settings: OcrSettings, cancelled=lambda: False):
        self.settings = settings
        self.cancelled = cancelled
        self.reader = None
        self.load_error = None

    def load(self):
        if self.load_error:
            raise RuntimeError(self.load_error)
        if self.reader is not None:
            return
        try:
            import easyocr
            import torch
            torch.set_num_threads(min(4, os.cpu_count() or 1))
            if self.settings.gpu and not torch.cuda.is_available():
                raise RuntimeError("CUDA недоступна. Выберите CPU или установите PyTorch с CUDA для вашей NVIDIA.")
            self.reader = easyocr.Reader(
                ["ru", "en"], gpu=self.settings.gpu,
                model_storage_directory=str(self.settings.model_dir),
                user_network_directory=str(self.settings.model_dir / "user_network"),
                download_enabled=False, verbose=False,
            )
        except ImportError as exc:
            self.load_error = "Не установлены зависимости OCR. Установите requirements-ocr.txt по README."
            raise RuntimeError(self.load_error) from exc
        except Exception as exc:
            self.load_error = (
                f"Не удалось загрузить локальные модели OCR: {exc}. "
                "Подготовьте модели командой python -m ocr_setup --model-dir <папка> "
                "или выберите CPU при ошибке CUDA."
            )
            raise RuntimeError(self.load_error) from exc

    def recognize(self, image) -> str:
        if self.cancelled():
            raise Cancelled()
        self.load()
        if self.cancelled():
            raise Cancelled()
        # workers=0: пакет не создаёт дочерние процессы загрузчика данных.
        lines = self.reader.readtext(image, detail=0, paragraph=True, workers=0, batch_size=1)
        return "\n".join(str(line) for line in lines)


def page_method(page, text: str) -> str:
    letters = sum(char.isalnum() for char in text)
    images = page.get_image_info()
    area = max(1, page.rect.width * page.rect.height)
    image_area = sum(max(0, item["bbox"][2] - item["bbox"][0]) *
                     max(0, item["bbox"][3] - item["bbox"][1]) for item in images)
    usable = letters >= 6 and text.count("\ufffd") <= max(1, len(text) // 50)
    # Небольшой заголовок поверх скана не считается готовым текстовым слоем.
    if usable and not (image_area / area > .4 and letters < 80):
        return "text"
    if not text.strip() and not images and not page.get_drawings():
        return "blank"
    return "ocr"


def clean_xml_text(text: str) -> str:
    return re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff\ufffe\uffff]", "", text)


def export_results(folder: Path, manifest: dict, cancelled=lambda: False):
    """DOCX заменяется атомарно, только после успешного завершения записи."""
    from docx import Document
    partial = manifest["status"] != "completed"
    stem = "result-partial" if partial else "result"
    document = Document()
    document.add_heading("Результат распознавания", 0)
    chunks = []
    if partial:
        notice = "НЕПОЛНЫЙ РЕЗУЛЬТАТ: обработка отменена или часть файлов/страниц завершилась с ошибкой."
        document.add_paragraph(notice)
        chunks.append(notice)
    for file_result in manifest["files"]:
        heading = f"Файл {file_result['index']}: {file_result['name']}"
        document.add_heading(clean_xml_text(heading), 1)
        chunks.extend(["", heading])
        if file_result.get("error"):
            message = "Ошибка файла: " + file_result["error"]
            document.add_paragraph(clean_xml_text(message))
            chunks.append(message)
        for page in file_result["pages"]:
            heading = f"Страница {page['number']}"
            document.add_heading(heading, 2)
            text = (folder / page["text_file"]).read_text(encoding="utf-8") if page.get("text_file") else ""
            if page.get("error"):
                text = "Ошибка страницы: " + page["error"]
            elif not text.strip():
                text = "[Текст не обнаружен]"
            document.add_paragraph(clean_xml_text(text))
            chunks.extend([heading, text])
    text_path = folder / f"{stem}.txt"
    atomic_text(text_path, "\n".join(chunks))
    manifest["text_path"] = text_path.name
    descriptor, temporary = tempfile.mkstemp(dir=folder, prefix=".writing-", suffix=".docx")
    os.close(descriptor)
    try:
        document.save(temporary)
        # Если отмена пришла в ходе экспорта, маркируем уже собранный результат.
        if cancelled() and manifest["status"] == "completed":
            manifest["status"] = "cancelled"
            text_path.unlink(missing_ok=True)
            return export_results(folder, manifest)
        docx_path = folder / f"{stem}.docx"
        os.replace(temporary, docx_path)
        manifest["docx_path"] = docx_path.name
    finally:
        Path(temporary).unlink(missing_ok=True)


def process_batch(files, output_dir: Path, settings: OcrSettings, on_event=None,
                  cancelled=lambda: False, backend=None) -> dict:
    """Порядок files сохраняется. Ошибка одного PDF не останавливает пакет.

    output_dir должна быть новой папкой; это защищает прежние результаты.
    backend можно подменить в тестах, не загружая модель.
    """
    if not 72 <= settings.dpi <= 300:
        raise ValueError("Разрешение OCR должно быть от 72 до 300 DPI.")
    folder = Path(output_dir).resolve()
    folder.mkdir(parents=True, exist_ok=False)
    started = time.monotonic()
    emit = on_event or (lambda event: None)
    manifest = {"version": 1, "status": "running", "files": [], "pages_total": 0,
                "pages_done": 0, "errors": 0, "text_path": "", "docx_path": ""}
    paths = [Path(path).resolve() for path in files]
    manifest["files"] = [
        {"index": index, "name": path.name, "source": str(path), "page_count": 0,
         "status": "pending", "pages": [], "error": ""}
        for index, path in enumerate(paths, 1)
    ]
    backend = backend or EasyOcrBackend(settings, cancelled)

    def check_cancel():
        if cancelled():
            raise Cancelled()

    def progress(kind, **values):
        emit({"event": kind, **values})

    try:
        try:
            import pymupdf
        except ImportError as exc:
            raise RuntimeError("Не установлен PyMuPDF. Установите requirements-ocr.txt.") from exc
        # Лёгкий первый проход узнаёт общее число страниц, не рендерит PDF.
        for item in manifest["files"]:
            check_cancel()
            try:
                with pymupdf.open(item["source"]) as pdf:
                    if not pdf.is_pdf:
                        raise ValueError("Файл не является PDF.")
                    if pdf.needs_pass:
                        raise ValueError("PDF защищён паролем; сначала сохраните доступную копию.")
                    item["page_count"] = len(pdf)
                    if not len(pdf):
                        raise ValueError("PDF не содержит страниц.")
                    manifest["pages_total"] += len(pdf)
            except Exception as exc:
                item.update(status="error", error=str(exc)[:1200])
                manifest["errors"] += 1
            progress("file_info", file=item["index"] - 1, pages=item["page_count"], error=item["error"])
        progress("batch_started", files=len(paths), pages=manifest["pages_total"])
        save_manifest(folder, manifest)
        for item in manifest["files"]:
            check_cancel()
            row = item["index"] - 1
            if item["error"]:
                progress("file_finished", file=row, status="error", error=item["error"])
                continue
            item["status"] = "running"
            try:
                with pymupdf.open(item["source"]) as pdf:
                    for number, page in enumerate(pdf, 1):
                        check_cancel()
                        progress("page_started", file=row, page=number, pages=item["page_count"],
                                 done=manifest["pages_done"], total=manifest["pages_total"])
                        result = {"number": number, "method": "", "text_file": "", "error": ""}
                        try:
                            text = page.get_text("text", sort=True)
                            result["method"] = page_method(page, text)
                            if result["method"] == "ocr":
                                progress("recognizing", file=row, page=number)
                                # Ограничение размера предотвращает чрезмерный расход RAM на плакатах.
                                scale = min(settings.dpi / 72, 3500 / max(page.rect.width, page.rect.height))
                                pixmap = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale),
                                                        colorspace=pymupdf.csRGB, alpha=False)
                                import numpy as np
                                image = np.frombuffer(pixmap.samples, dtype=np.uint8).reshape(pixmap.height, pixmap.width, 3)
                                check_cancel()
                                text = backend.recognize(image)
                            check_cancel()
                            relative = f"pages/{item['index']:04d}_{number:05d}.txt"
                            atomic_text(folder / relative, text)
                            result["text_file"] = relative
                        except Cancelled:
                            raise
                        except Exception as exc:
                            result["error"] = str(exc)[:1200]
                            manifest["errors"] += 1
                        item["pages"].append(result)
                        manifest["pages_done"] += 1
                        save_manifest(folder, manifest)
                        progress("page_finished", file=row, page=number, method=result["method"],
                                 error=result["error"], done=manifest["pages_done"], total=manifest["pages_total"])
                item["status"] = "partial" if any(p["error"] for p in item["pages"]) else "completed"
            except Cancelled:
                raise
            except Exception as exc:
                item.update(status="error", error=str(exc)[:1200])
                manifest["errors"] += 1
            progress("file_finished", file=row, status=item["status"], error=item["error"])
        manifest["status"] = "partial" if manifest["errors"] else "completed"
    except Cancelled:
        manifest["status"] = "cancelled"
    except Exception as exc:
        manifest.update(status="failed", fatal_error=str(exc)[:1600])
        manifest["errors"] += 1
    finally:
        for item in manifest["files"]:
            if item["status"] in ("running", "pending"):
                item["status"] = "cancelled" if manifest["status"] == "cancelled" else "error"
        try:
            progress("exporting")
            export_results(folder, manifest, cancelled)
        except Exception as exc:
            manifest.update(status="failed", fatal_error=f"Не удалось сохранить результат: {exc}")
            manifest["errors"] += 1
        manifest["elapsed_seconds"] = round(time.monotonic() - started, 3)
        save_manifest(folder, manifest)
        progress("finished", status=manifest["status"], manifest=str(folder / "result.json"),
                 done=manifest["pages_done"], total=manifest["pages_total"], errors=manifest["errors"])
    return manifest
