"""Состояние между вызовами: рабочая вкладка, флаг автозапуска, каталог данных.

Каталог по умолчанию `~/.marty-bridge`, переопределяется переменной MB_HOME.
Держим состояние вне пакета: после обновления пакета рабочая вкладка не теряется.
"""

from __future__ import annotations

import json
import os
from pathlib import Path


def home() -> Path:
    """Каталог данных. Создается при первом обращении."""
    raw = os.environ.get("MB_HOME")
    path = Path(raw).expanduser() if raw else Path.home() / ".marty-bridge"
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    return path


def _file() -> Path:
    return home() / "state.json"


def load() -> dict:
    try:
        return json.loads(_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save(data: dict) -> None:
    try:
        _file().write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    except OSError:
        pass


def tab() -> int | None:
    """Активная рабочая вкладка. None значит «взять текущую в браузере»."""
    value = load().get("tab")
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def set_tab(tab_id: int) -> int:
    save({**load(), "tab": int(tab_id)})
    return int(tab_id)


def off_flag() -> Path:
    """Файл, который выключает автозапуск моста."""
    return home() / "bridge.off"


def log_path(name: str) -> Path:
    return home() / name


def _loadable(folder: Path) -> bool:
    """Годится ли папка для загрузки в браузер.

    Chrome отказывается грузить расширение, если на верхнем уровне есть имена,
    начинающиеся с подчеркивания: `__pycache__` рядом с манифестом ломает
    установку с ошибкой «Filenames starting with _ are reserved».
    """
    try:
        if not (folder / "manifest.json").exists():
            return False
        for entry in folder.iterdir():
            if entry.name.startswith(("_", ".")):
                return False
    except OSError:
        return False
    return True


def extension_dir() -> str:
    """Папка расширения, которую человек грузит в браузер.

    Порядок: явная переменная MB_EXTENSION_DIR, затем папка рядом с пакетом,
    затем текущий каталог. Пустая строка значит «не нашли».
    """
    raw = os.environ.get("MB_EXTENSION_DIR")
    if raw:
        return raw
    roots = []
    try:
        roots.append(Path(__file__).resolve().parents[1] / "extension")
    except (IndexError, OSError):
        pass
    roots.append(Path.cwd() / "extension")
    for candidate in roots:
        if _loadable(candidate):
            return str(candidate)
    return ""
