"""Telegram Desktop: читать глазами, писать через буфер обмена.

Почему так, а не через API или веб-версию:

- веб-версия часто недоступна без VPN, а приложение на машине работает всегда;
- прочитать переписку через UI Automation нельзя: интерфейс рисуется сам,
  наружу отдается только контейнер без текста. Поэтому чтение идет скриншотом,
  текст с картинки снимает зрение;
- писать можно штатно: навигация по чатам поиском, текст через буфер обмена,
  Enter на отправку.

Защита: перед отправкой сверяется заголовок окна. Если открылся не тот чат,
текст не уходит, команда возвращает ошибку.

Требования: Windows, установленный Telegram Desktop, пакеты pywinauto,
pyperclip, pillow (ставятся через `pip install "marty-bridge[telegram]"`).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time

TITLE = "Telegram"


class TelegramUnavailable(RuntimeError):
    """Канал недоступен: не та система или не хватает пакетов."""


def _deps():
    if sys.platform != "win32":
        raise TelegramUnavailable(
            "канал Telegram работает только на Windows: нужен UI Automation "
            "и буфер обмена Windows"
        )
    try:
        import ctypes
        import ctypes.wintypes  # noqa: F401
        import pyperclip
        from PIL import ImageGrab
        from pywinauto import Desktop
    except ImportError as exc:
        raise TelegramUnavailable(
            "не хватает пакетов для Telegram: установите "
            'pip install "marty-bridge[telegram]" (%s)' % exc
        ) from exc
    return ctypes, pyperclip, ImageGrab, Desktop


def _user32():
    ctypes, _, _, _ = _deps()
    return ctypes.WinDLL("user32", use_last_error=True)


VK = {
    "ctrl": 0x11, "shift": 0x10, "alt": 0x12, "enter": 0x0D, "esc": 0x1B,
    "tab": 0x09, "f": 0x46, "a": 0x41, "v": 0x56, "c": 0x43, "up": 0x26,
    "down": 0x28, "pgup": 0x21, "pgdn": 0x22, "home": 0x24, "end": 0x23,
}


def telegram_pids() -> set:
    """PID процесса Telegram.exe без сторонних библиотек."""
    out = subprocess.run(
        ["tasklist", "/FI", "IMAGENAME eq Telegram.exe", "/FO", "CSV", "/NH"],
        capture_output=True,
        text=True,
        encoding="cp866",
        errors="replace",
    )
    pids = set()
    for line in (out.stdout or "").splitlines():
        parts = [p.strip('" ') for p in line.split('","')]
        if len(parts) >= 2 and parts[0].lower().startswith("telegram"):
            try:
                pids.add(int(parts[1]))
            except ValueError:
                pass
    return pids


def find_window():
    _, _, _, Desktop = _deps()
    pids = telegram_pids()
    if not pids:
        raise TelegramUnavailable("процесс Telegram.exe не запущен")
    candidates = []
    for window in Desktop(backend="uia").windows():
        try:
            if not window.is_visible():
                continue
            pid = window.process_id()
            cls = window.class_name() or ""
            text = window.window_text() or ""
            if pid in pids and cls == "MainWindow":
                candidates.append((0, window))
            elif pid in pids and text:
                candidates.append((1, window))
        except Exception:  # noqa: BLE001
            continue
    if not candidates:
        raise TelegramUnavailable("окно Telegram не найдено, приложение свернуто?")
    candidates.sort(key=lambda item: item[0])
    return candidates[0][1]


def focus(window):
    user32 = _user32()
    try:
        window.set_focus()
    except Exception:  # noqa: BLE001
        hwnd = window.handle
        user32.ShowWindow(hwnd, 9)
        user32.SetForegroundWindow(hwnd)
    time.sleep(0.25)


def _key(vk: int, up: bool = False):
    _user32().keybd_event(vk, 0, 0x0002 if up else 0, 0)


def chord(*vks: int):
    for vk in vks:
        _key(vk)
    for vk in reversed(vks):
        _key(vk, up=True)


def title(window) -> str:
    return (window.window_text() or "").replace("\u200e", "").strip()


def shot(window, path: str) -> dict:
    _, _, ImageGrab, _ = _deps()
    window.set_focus()
    time.sleep(0.2)
    box = window.rectangle()
    image = ImageGrab.grab(
        bbox=(box.left, box.top, box.right, box.bottom), all_screens=True
    )
    image.save(path)
    return {"path": path, "size": list(image.size), "title": title(window)}


def click(window, x: int, y: int) -> dict:
    """Клик по координатам внутри окна (0,0 это левый верхний угол окна)."""
    user32 = _user32()
    box = window.rectangle()
    user32.SetCursorPos(box.left + int(x), box.top + int(y))
    time.sleep(0.15)
    user32.mouse_event(0x0002, 0, 0, 0, 0)
    user32.mouse_event(0x0004, 0, 0, 0, 0)
    time.sleep(0.2)
    return {"клик": [int(x), int(y)]}


def _with_clipboard(text: str, action):
    """Положить текст в буфер, выполнить действие, вернуть прежний буфер."""
    _, pyperclip, _, _ = _deps()
    try:
        old = pyperclip.paste()
    except Exception:  # noqa: BLE001
        old = None
    pyperclip.copy(text)
    try:
        return action()
    finally:
        if old:
            time.sleep(0.25)
            try:
                pyperclip.copy(old)
            except Exception:  # noqa: BLE001
                pass


def paste(window, text: str) -> dict:
    focus(window)

    def action():
        chord(VK["ctrl"], VK["v"])
        time.sleep(0.35)
        return {"вставлено": len(text)}

    return _with_clipboard(text, action)


def open_chat(window, name: str) -> str:
    """Открыть чат через общий поиск. Возвращает заголовок окна после перехода.

    Ctrl+F ищет внутри открытого чата, поэтому кликаем по полю «Поиск» в левой
    панели: там область поиска общая, по чатам.
    """
    focus(window)
    chord(VK["esc"])
    time.sleep(0.25)
    box = window.rectangle()
    click(window, int((box.right - box.left) * 0.17), 48)
    time.sleep(0.35)
    paste(window, name)
    time.sleep(1.0)
    chord(VK["enter"])
    time.sleep(1.0)
    return title(window)


def send(window, chat: str, text: str) -> dict:
    got = open_chat(window, chat)
    if chat.lower() not in got.lower():
        return {"ok": False, "error": "открылся не тот чат", "заголовок": got}
    box = window.rectangle()
    click(window, int((box.right - box.left) / 2), int(box.bottom - box.top - 45))
    time.sleep(0.2)
    paste(window, text)
    time.sleep(0.35)
    chord(VK["enter"])
    time.sleep(0.6)
    return {"ok": True, "чат": title(window), "отправлено": len(text)}


def _ps_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def send_file(window, chat: str, path: str) -> dict:
    got = open_chat(window, chat)
    if chat.lower() not in got.lower():
        return {"ok": False, "error": "открылся не тот чат", "заголовок": got}
    path = os.path.abspath(path)
    if not os.path.exists(path):
        return {"ok": False, "error": "файла нет: %s" % path}
    # файл в буфер кладется через PowerShell: текстовый буфер файл не передает
    subprocess.run(
        ["powershell", "-NoProfile", "-Command", "Set-Clipboard -LiteralPath %s" % _ps_quote(path)],
        capture_output=True,
    )
    focus(window)
    box = window.rectangle()
    click(window, int((box.right - box.left) / 2), int(box.bottom - box.top - 45))
    chord(VK["ctrl"], VK["v"])
    time.sleep(1.5)
    chord(VK["enter"])
    time.sleep(1.5)
    return {"ok": True, "чат": title(window), "файл": path}


def run(command: str, args: list[str]) -> dict:
    """Выполнить команду канала. Возвращает словарь для печати в JSON."""
    window = find_window()

    if command == "title":
        return {"заголовок": title(window)}
    if command == "shot":
        return shot(window, args[0] if args else "tg.png")
    if command == "open":
        return {"открыт": open_chat(window, args[0])}
    if command == "send":
        return send(window, args[0], args[1])
    if command == "sendfile":
        return send_file(window, args[0], args[1])
    if command == "look":
        got = open_chat(window, args[0])
        time.sleep(0.6)
        path = args[1] if len(args) > 1 else "tg-look.png"
        return {**shot(window, path), "чат": got}
    if command == "esc":
        focus(window)
        chord(VK["esc"])
        return {"ок": True}
    if command == "click":
        focus(window)
        return click(window, int(args[0]), int(args[1]))
    if command == "paste":
        return paste(window, args[0])
    if command == "key":
        focus(window)
        chord(*[VK[part.lower()] for part in args[0].split("+")])
        return {"нажато": args[0]}
    raise ValueError("неизвестная команда Telegram: %s" % command)


def dumps(result: dict) -> str:
    return json.dumps(result, ensure_ascii=False)
