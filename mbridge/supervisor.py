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

# Сколько секунд без опроса считаем, что вкладка не работает.
STALE = 120
# Не чаще одного открытия вкладки в этот интервал.
RETRY = 1800
# Больше стольких вкладок за сессию моста не открываем.
MAX_OPENS = 2

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


def browser_running() -> bool:
    """Запущен ли браузер.

    Без этой проверки `browser.exe URL` не открывает вкладку, а запускает
    браузер заново. Если человек закрыл браузер сам, наставник не должен
    возвращать его на экран.
    """
    name = os.path.basename(browser_path() or "").lower()
    if not name:
        return False
    try:
        if os.name == "nt":
            out = subprocess.run(
                ["tasklist", "/FI", "IMAGENAME eq " + name, "/NH"],
                capture_output=True, text=True, timeout=10,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            ).stdout.lower()
            return name in out
        out = subprocess.run(["pgrep", "-f", name], capture_output=True, timeout=10)
        return out.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


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


def should_open_tab(
    now: float,
    seen: float,
    started_at: float,
    last_open: float,
    opens: int,
    ever_seen: bool,
    browser_up: bool,
) -> tuple[bool, str]:
    """Открывать ли вкладку-сторож. Возвращает (решение, причина).

    Вынесено отдельно, потому что именно здесь раньше появлялись дубли:
    наставник открывал вкладку через минуту после старта моста, пока браузер
    еще не успевал загрузить расширение.
    """
    silent = (now - seen) if seen else (now - started_at)
    if silent < STALE:
        return False, "на связи"
    if not ever_seen:
        return False, "расширение ни разу не подключалось"
    if now - last_open < RETRY:
        return False, "недавно открывали"
    if opens >= MAX_OPENS:
        return False, "лимит попыток исчерпан"
    if not browser_up:
        return False, "браузер закрыт"
    return True, f"молчит {int(silent)} сек"


def main() -> int:
    if state.off_flag().exists():
        log("найден bridge.off, автозапуск выключен")
        return 0

    log(f"наставник запущен, python: {sys.executable}, порт: {PORT}")

    last_open = 0.0
    opens = 0
    ever_seen = False
    warned = False
    started_at = time.time()

    while True:
        if state.off_flag().exists():
            log("появился bridge.off, выходим")
            return 0

        if not alive():
            log("мост не отвечает, запускаем")
            # отметку ставим до запуска: если мост поднимется не с первого раза,
            # окно милосердия не должно обнуляться
            started_at = time.time()
            if not start_bridge():
                time.sleep(30)
                continue
            time.sleep(3)
            if alive():
                log("мост поднялся")
            continue

        seen = float(health().get("seen") or 0)
        now = time.time()
        if seen:
            ever_seen = True
            warned = False
            opens = 0

        silent = (now - seen) if seen else (now - started_at)
        if silent < STALE:
            time.sleep(20)
            continue

        ok, why = should_open_tab(
            now, seen, started_at, last_open, opens, ever_seen, browser_running()
        )
        if not ok:
            if not warned:
                log(f"вкладку не открываю: {why}")
                warned = True
            time.sleep(30)
            continue

        last_open = now
        opens += 1
        warned = False
        log(f"вкладка-сторож {why}, открываю заново (попытка {opens})")
        open_keepalive()

        time.sleep(30)


if __name__ == "__main__":
    raise SystemExit(main())
