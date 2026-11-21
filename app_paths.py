"""Пути приложения без Qt; выбор базы никогда не переносит данные."""
import os
import sys
from dataclasses import dataclass
from pathlib import Path


class AmbiguousDatabaseError(ValueError):
    def __init__(self, candidates: list[Path]):
        self.candidates = candidates
        super().__init__(
            "Найдено несколько баз авторов:\n"
            + "\n".join(str(p) for p in candidates)
            + "\nВыберите нужную базу. Файлы не будут перемещены или заменены."
        )


def absolute_path(value: str | Path, base: Path) -> Path:
    path = Path(value).expanduser()
    return (path if path.is_absolute() else base / path).resolve()


def data_root_for_database(db_path: Path) -> Path:
    return db_path.parent.parent if db_path.parent.name.lower() == "database" else db_path.parent


def resolve_data_file(value: str | Path, data_dir: Path) -> Path:
    # Старые Windows-записи используют обратную косую черту.
    return absolute_path(str(value).replace("\\", "/"), data_dir)


@dataclass(frozen=True)
class AppPaths:
    project_dir: Path
    resource_dir: Path
    data_dir: Path
    db_path: Path
    templates_dir: Path

    @property
    def output_dir(self) -> Path:
        return self.data_dir / "output"


def resolve_paths(
    project_dir: str | Path | None = None,
    data_dir: str | Path | None = None,
    db_path: str | Path | None = None,
    templates_dir: str | Path | None = None,
) -> AppPaths:
    """CLI выше переменных окружения; явный путь выше автоматического поиска.

    В исходниках данные по умолчанию рядом с main.py; в exe — LocalAppData.
    Существующие portable-данные рядом с exe также поддерживаются. CWD
    используется только для обнаружения старой базы и разрешения CLI-путей.
    """
    project_dir = project_dir or os.getenv("AUTOMAT_DOCS_PROJECT_DIR")
    data_dir = data_dir or os.getenv("AUTOMAT_DOCS_DATA_DIR")
    db_path = db_path or os.getenv("AUTOMAT_DOCS_DB_PATH")
    templates_dir = templates_dir or os.getenv("AUTOMAT_DOCS_TEMPLATES_DIR")
    cwd = Path.cwd()
    frozen = getattr(sys, "frozen", False)
    install_dir = Path(sys.executable if frozen else __file__).resolve().parent
    project = absolute_path(project_dir, cwd) if project_dir else install_dir
    resources = Path(getattr(sys, "_MEIPASS", install_dir)).resolve()
    local_data = Path(os.getenv("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / "Automat docs"
    default_data = local_data if frozen and not project_dir else project

    if db_path:
        database = absolute_path(db_path, cwd)
        if not database.is_file():
            raise FileNotFoundError(f"Указанная база не найдена: {database}")
        data = absolute_path(data_dir, cwd) if data_dir else data_root_for_database(database)
    elif data_dir or project_dir:
        data = absolute_path(data_dir, cwd) if data_dir else project
        database = data / "database" / "authors.db"
    else:
        roots = list(dict.fromkeys(p.resolve() for p in (default_data, install_dir, cwd, local_data)))
        candidates = [p / "database" / "authors.db" for p in roots if (p / "database" / "authors.db").is_file()]
        if len(candidates) > 1:
            raise AmbiguousDatabaseError(candidates)
        database = candidates[0] if candidates else default_data / "database" / "authors.db"
        data = data_root_for_database(database)

    if templates_dir:
        templates = absolute_path(templates_dir, cwd)
    else:
        choices = list(dict.fromkeys((data / "templates", project / "templates", resources / "templates")))
        templates = next((p for p in choices if p.is_dir()), choices[0])
    return AppPaths(project, resources, data.resolve(), database.resolve(), templates.resolve())
