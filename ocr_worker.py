"""Точка входа отдельного OCR-процесса: маленькие JSONL-события в stdout."""
import argparse
import contextlib
import json
import os
import sys
from pathlib import Path


def protocol_stdout():
    if sys.stdout is not None:
        return sys.stdout
    # PyInstaller --noconsole на Windows оставляет sys.stdout=None, хотя
    # QProcess передал дочернему процессу действующий pipe Win32.
    try:
        if sys.platform == "win32":
            import ctypes
            import msvcrt
            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel.GetStdHandle.restype = ctypes.c_void_p
            handle = kernel.GetStdHandle(-11)
            if not handle or handle == ctypes.c_void_p(-1).value:
                return None
            fd = msvcrt.open_osfhandle(handle, os.O_WRONLY | os.O_BINARY)
            return os.fdopen(fd, "w", encoding="utf-8", buffering=1)
        return os.fdopen(os.dup(1), "w", encoding="utf-8", buffering=1)
    except (OSError, ValueError):
        return None  # Результат всё равно будет доступен в result.json.


def main(argv=None):
    parser = argparse.ArgumentParser(description="Локальный OCR-воркер")
    parser.add_argument("request", type=Path)
    args = parser.parse_args(argv)
    stream = protocol_stdout()

    def emit(event):
        if stream is not None:
            try:
                stream.write(json.dumps(event, ensure_ascii=True) + "\n")
                stream.flush()
            except (OSError, ValueError):
                pass

    # Сообщения сторонних библиотек не смешиваются с протоколом событий.
    diagnostic = sys.stderr or open(os.devnull, "w", encoding="utf-8")
    try:
        with contextlib.redirect_stdout(diagnostic):
            from ocr_engine import OcrSettings, process_batch
            request = json.loads(args.request.read_text(encoding="utf-8"))
            files = request["files"]
            if not files:
                raise ValueError("Не выбраны PDF-файлы.")
            cancel_path = Path(request["cancel_path"])
            settings = OcrSettings(Path(request["model_dir"]), bool(request.get("gpu", False)),
                                   int(request.get("dpi", 200)))
            result = process_batch(files, Path(request["output_dir"]), settings,
                                   on_event=emit, cancelled=cancel_path.exists)
        return 1 if result["status"] == "failed" else 0
    except Exception as exc:
        emit({"event": "fatal", "error": str(exc)[:1600]})
        return 2


if __name__ == "__main__":
    sys.exit(main())
