"""Наставник моста: держит bridge.py запущенным и следит за вкладкой-сторожем.

Задачи:
1. Если мост упал, поднять снова. Если порт уже занят живым мостом, не мешать.
2. Если вкладка-сторож перестала опрашивать мост, открыть ее заново в браузере.
3. Выйти, если рядом лежит файл bridge.off.

Запускается автозапуском при входе в систему, окна консоли не открывает.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

from . import state

PORT = int(os.environ.get("MB_PORT") or os.environ.get("BRIDGE_PORT") or 8765)
KEEPALIVE = f"http://127.0.0.1:{PORT}/"

# Сколько секунд без опроса считаем, что вкладка закрыта.
STALE = 90
# Не чаще одного открытия вкладки в этот интервал.
RETRY = 300
# После трех неудач подряд пауза, чтобы не плодить вкладки при выключенном расширении.
RETRY_AFTER_FAILS = 1800

# Где искать браузер. Первый существующий путь и берем.
BROWSERS = [
    os.environ.get("MB_BROWSER") or "",
    r"C:\Program Files\Yandex\YandexBrowser\Application\browser.exe",
    r"C:\Program Files (x86)\Yandex\YandexBrowser\Application\browser.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Yandex.app/Contents/MacOS/Yandex",
    "/usr/bin/google-chrome",
    "/usr/bin/chromium",
    "/usr/bin/chromium-browser",
]


def log(text: str) -> None:
    stamp = time.strftime("%Y-%m-%d %H:%M:%S")
    try:
        with state.log_path("supervisor.log").open("a", encoding="utf-8") as fh:
            fh.write(f"{stamp} {text}\n")
    except OSError:
        pass


def opener():
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def alive() -> bool:
    try:
        with opener().open(f"http://127.0.0.1:{PORT}/health", timeout=2) as response:
            return response.status == 200
    except (urllib.error.URLError, OSError):
        return False


def health() -> dict:
    try:
        with opener().open(f"http://127.0.0.1:{PORT}/health", timeout=2) as response:
            return json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError):
        return {}


def browser_path() -> str:
    for path in BROWSERS:
        if path and os.path.exists(path):
            return path
    return ""


def open_keepalive() -> bool:
    exe = browser_path()
    if not exe:
        log("браузер не найден, вкладку открыть не могу. Задайте MB_BROWSER")
        return False
    try:
        subprocess.Popen(
            [exe, KEEPALIVE],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        log(f"открыл вкладку-сторож: {KEEPALIVE}")
        return True
    except OSError as exc:
        log(f"не удалось открыть вкладку: {exc!r}")
        return False


def start_bridge() -> bool:
    try:
        subprocess.Popen(
            [sys.executable, "-m", "mbridge.bridge", "--port", str(PORT)],
            cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError as exc:
        log(f"не удалось запустить мост: {exc!r}")
        return False
    return True


def main() -> int:
    if state.off_flag().exists():
        log("найден bridge.off, автозапуск выключен")
        return 0

    log(f"наставник запущен, python: {sys.executable}, порт: {PORT}")

    last_open = 0.0
    fails = 0
    started_at = time.time()

    while True:
        if state.off_flag().exists():
            log("появился bridge.off, выходим")
            return 0

        if not alive():
            log("мост не отвечает, запускаем")
            if not start_bridge():
                time.sleep(30)
                continue
            time.sleep(3)
            if alive():
                log("мост поднялся")
                started_at = time.time()
            continue

        seen = float(health().get("seen") or 0)
        # сразу после старта моста расширение еще не успело опросить его,
        # и это не повод открывать новую вкладку: даем минуту
        if not seen and (time.time() - started_at) < 60:
            time.sleep(10)
            continue

        stale = (time.time() - seen) > STALE if seen else True
        if stale:
            # открытая вкладка сама переподключается, когда мост вернулся,
            # поэтому даем ей 6 секунд и проверяем еще раз, иначе плодятся дубли
            time.sleep(6)
            seen = float(health().get("seen") or 0)
            stale = (time.time() - seen) > STALE if seen else True

        if not stale:
            fails = 0

        pause = RETRY if fails < 3 else RETRY_AFTER_FAILS
        if stale and (time.time() - last_open) > pause:
            last_open = time.time()
            fails += 1
            log(f"вкладка-сторож не опрашивает мост, открываю заново (попытка {fails})")
            open_keepalive()

        time.sleep(30)


if __name__ == "__main__":
    raise SystemExit(main())
