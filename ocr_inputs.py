"""Форматы OCR: PDF и одиночные JPG/PNG. Тяжёлые библиотеки импортируются по запросу."""
from contextlib import contextmanager
from io import BytesIO
from pathlib import Path

IMAGE_SUFFIXES = frozenset({".jpg", ".jpeg", ".png"})
SUPPORTED_SUFFIXES = IMAGE_SUFFIXES | {".pdf"}
MAX_IMAGE_PIXELS = 80_000_000
IMAGE_DPI = 200


def _validate_image(image):
    if image.format not in ("PNG", "JPEG"):
        raise ValueError("Поддерживаются изображения JPG/JPEG и PNG.")
    if image.width * image.height > MAX_IMAGE_PIXELS:
        raise ValueError("Изображение больше 80 мегапикселей. Уменьшите его перед распознаванием.")
    if getattr(image, "n_frames", 1) != 1:
        raise ValueError("Анимированный PNG не поддерживается. Сохраните нужный кадр отдельной картинкой.")


def inspect_input(path):
    """Количество страниц без OCR; картинки считаются одной страницей."""
    suffix = Path(path).suffix.lower()
    if suffix == ".pdf":
        import pymupdf
        with pymupdf.open(path) as document:
            if not document.is_pdf:
                raise ValueError("Файл с расширением PDF не является PDF.")
            if document.needs_pass:
                raise ValueError("PDF защищён паролем; сначала сохраните доступную копию.")
            if not len(document):
                raise ValueError("PDF не содержит страниц.")
            return len(document)
    if suffix in IMAGE_SUFFIXES:
        from PIL import Image
        with Image.open(path) as image:
            _validate_image(image)
            image.verify()
        return 1
    raise ValueError("Поддерживаются PDF, JPG/JPEG и PNG.")


@contextmanager
def open_input(path):
    """Единый постраничный интерфейс, без изменения исходного файла.

    Картинка становится одной временной PDF-страницей в памяти. EXIF-поворот
    применяется, прозрачность заменяется белым фоном, длинная сторона <=3500 px.
    Размер страницы для DOCX рассчитывается при 200 DPI; оригинал не увеличивается.
    """
    import pymupdf
    if Path(path).suffix.lower() == ".pdf":
        with pymupdf.open(path) as document:
            yield document
        return
    from PIL import Image, ImageOps
    with Image.open(path) as source:
        _validate_image(source)
        normalized = ImageOps.exif_transpose(source)
        try:
            normalized.thumbnail((3500, 3500), Image.Resampling.LANCZOS)
            # RGBA также учитывает palette transparency и корректно переводит CMYK.
            with normalized.convert("RGBA") as rgba:
                with Image.new("RGB", rgba.size, "white") as rgb:
                    with rgba.getchannel("A") as alpha:
                        rgb.paste(rgba, mask=alpha)
                    width, height = rgb.size
                    stream = BytesIO()
                    rgb.save(stream, format="PNG")
        finally:
            normalized.close()
    with pymupdf.open() as document:
        page = document.new_page(width=width * 72 / IMAGE_DPI, height=height * 72 / IMAGE_DPI)
        page.insert_image(page.rect, stream=stream.getvalue())
        yield document
