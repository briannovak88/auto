"""Синтетические PDF: цифровой текст, сканы, смешанный документ."""
from pathlib import Path


def make_pdf(path: Path, pages=("text",), text="OCTOPUS OCR TEST DOCUMENT"):
    import pymupdf
    pdf = pymupdf.open()
    for index, method in enumerate(pages):
        page = pdf.new_page(width=500, height=650)
        label = f"{text} PAGE {index + 1}"
        if method == "scan":
            original = pymupdf.open()
            source = original.new_page(width=500, height=650)
            source.insert_text((35, 90), label, fontsize=18)
            image = source.get_pixmap(matrix=pymupdf.Matrix(1.5, 1.5)).tobytes("png")
            page.insert_image(page.rect, stream=image)
            original.close()
        elif method == "text":
            page.insert_text((35, 90), label, fontsize=18)
    pdf.save(path)
    pdf.close()
    return path
