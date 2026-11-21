"""Воспроизводимый синтетический замер OCR через настоящий GUI и QProcess."""
import argparse
import io
import json
import time
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--models", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path, help="Новая папка для тестовых данных и результатов")
    parser.add_argument("--pages", type=int, default=50, choices=range(1, 101), metavar="1..100")
    parser.add_argument("--font", type=Path, help="Шрифт TTF с кириллицей; по умолчанию Arial или DejaVu Sans")
    args = parser.parse_args()
    fonts = [args.font] if args.font else [Path("C:/Windows/Fonts/arial.ttf"),
                                         Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")]
    font_path = next((p for p in fonts if p and p.is_file()), None)
    if font_path is None:
        parser.error("Укажите --font с путём к TTF-шрифту с кириллицей.")
    root = args.output_dir.expanduser().resolve()
    if root.exists():
        parser.error("Папка --output-dir уже существует. Выберите новую папку.")
    root.mkdir(parents=True)
    import pymupdf
    from PIL import Image, ImageDraw, ImageFont
    image = Image.new("RGB", (1400, 450), "white")
    draw = ImageDraw.Draw(image)
    font = ImageFont.truetype(str(font_path), 42)
    draw.text((45, 70), "Осьминог: тест распознавания", font=font, fill="black")
    draw.text((45, 160), "Octopus local OCR test 2026", font=font, fill="black")
    stream = io.BytesIO()
    image.save(stream, format="PNG")
    pdf = pymupdf.open()
    xref = 0
    for _ in range(args.pages):
        page = pdf.new_page(width=700, height=225)
        xref = page.insert_image(page.rect, stream=stream.getvalue(), xref=xref)
    source = root / "Русский English — синтетический пакет.pdf"
    pdf.save(source)
    pdf.close()

    from PyQt6.QtCore import QTimer
    from PyQt6.QtWidgets import QApplication
    from app_paths import resolve_paths
    from main import AutomatDocsWindow
    app = QApplication([])
    window = AutomatDocsWindow(paths=resolve_paths(data_dir=root))
    window.show()
    window.navigation.setCurrentRow(1)
    ocr = window.ocr
    ocr.model_dir.setText(str(args.models.expanduser().resolve()))
    ocr.add_files([source])
    ticks = []
    timer = QTimer()
    timer.timeout.connect(lambda: ticks.append(time.monotonic()))
    timer.start(50)
    ocr.stopped.connect(app.quit)
    started = time.monotonic()
    ocr.start()
    app.exec()
    timer.stop()
    result = ocr.result or {}
    text = ""
    if result.get("text_path"):
        text = (ocr.run_dir / "result" / result["text_path"]).read_text(encoding="utf-8")
    gaps = [b - a for a, b in zip(ticks, ticks[1:])]
    report = {"status": result.get("status", "failed"), "pages": result.get("pages_done", 0),
              "errors": result.get("errors", 1), "engine_seconds": result.get("elapsed_seconds"),
              "gui_seconds": round(time.monotonic() - started, 3), "gui_ticks": len(ticks),
              "max_gui_gap_seconds": round(max(gaps, default=0), 3), "dpi": 200, "gpu": False,
              "font": str(font_path), "russian_found": "Осьминог" in text, "english_found": "Octopus" in text}
    (root / "benchmark.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    window.close()
    return 0 if report["status"] == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
