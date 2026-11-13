"""Локальный мост между командами и расширением.

Расширение само опрашивает мост, поэтому браузеру не нужен порт отладки
и флаги запуска. Слушаем только 127.0.0.1.

Запуск:
    python -m mbridge.bridge [--port 8765]
"""

from __future__ import annotations

import argparse
import json
import os
import threading
import time
import uuid
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from . import state

LOCK = threading.Condition()
PENDING: deque = deque()
RESULTS: dict = {}
LAST_SEEN = {"ts": 0.0}

KEEPALIVE_PAGE = """<!doctype html>
<html lang="ru"><head><meta charset="utf-8">
<title>Marty Bridge</title>
<style>
  body{margin:0;height:100vh;display:flex;align-items:center;justify-content:center;
       font:14px/1.6 system-ui,Segoe UI,Arial;background:#12141a;color:#8b93a7}
  b{color:#4ade80;font-weight:600}
  #hint{font-size:12px;color:#5b6478;margin-top:6px}
</style></head>
<body><div>Marty Bridge работает. Не закрывайте вкладку.<br>
Соединение: <b id="st">запускается</b>
<div id="hint">проверяю расширение</div></div>
<script>
// Самодиагностика. Страница не зависит от расширения: она сама спрашивает мост,
// когда тот последний раз получал опрос от расширения, и честно пишет состояние.
const st = document.getElementById("st");
const hint = document.getElementById("hint");
const started = Date.now();
let reloads = +(sessionStorage.getItem("bdReloads") || 0);

function paint(text, ok, note) {
  st.textContent = text;
  st.style.color = ok ? "#4ade80" : "#f87171";
  hint.textContent = note || "";
}

// Главный признак жизни это поле age из /health: его обновляет каждый опрос
// расширения. Метку bdAlive из keepalive.js держим как дополнение, но решение
// о перезагрузке принимаем по мосту, иначе страница уходит в цикл перезагрузок
// и рвет опрос на полуслове.
function injected() {
  return document.documentElement.dataset.bdAlive === "1";
}

function retry() {
  if (reloads >= 8) {
    paint("расширение не выходит на связь", false,
          "перезагрузил вкладку 8 раз без результата. Включите Marty Bridge " +
          "на странице расширений, он мог выключиться после перезапуска");
    return;
  }
  const delay = reloads < 3 ? 8000 : 30000;
  if (Date.now() - started > delay) {
    reloads += 1;
    sessionStorage.setItem("bdReloads", reloads);
    location.reload();
  }
}

async function tick() {
  let age;
  try {
    const r = await fetch("/health?t=" + Date.now(), { cache: "no-store" });
    const h = await r.json();
    age = (h.age === null || h.age === undefined) ? -1 : Math.round(h.age);
  } catch (e) {
    paint("мост не отвечает", false, "наставник поднимет его сам за несколько секунд");
    return;
  }
  if (age >= 0 && age < 45) {
    reloads = 0;
    sessionStorage.setItem("bdReloads", 0);
    paint("на связи", true, "опрос " + age + " сек назад");
    return;
  }
  if (age < 0) {
    paint("ждем расширение", false,
          "мост поднялся, опросов от расширения еще не было");
  } else {
    paint("расширение молчит " + age + " сек", false,
          "перезагружу вкладку сам, если не очнется");
  }
  retry();
}

tick();
setInterval(tick, 2000);
</script>
</body></html>
"""


def wait_command(timeout: float):
    end = time.time() + timeout
    with LOCK:
        while not PENDING:
            left = end - time.time()
            if left <= 0:
                return None
            LOCK.wait(min(left, 1.0))
        return PENDING.popleft()


def wait_result(cmd_id: str, timeout: float):
    end = time.time() + timeout
    with LOCK:
        while cmd_id not in RESULTS:
            left = end - time.time()
            if left <= 0:
                return None
            LOCK.wait(min(left, 0.5))
        return RESULTS.pop(cmd_id)


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def _json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        size = int(self.headers.get("Content-Length") or 0)
        if not size:
            return {}
        return json.loads(self.rfile.read(size).decode("utf-8"))

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.end_headers()

    def do_GET(self):
        url = urlparse(self.path)
        if url.path in ("/", "/index.html"):
            body = KEEPALIVE_PAGE.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
            return
        if url.path == "/health":
            seen = LAST_SEEN["ts"]
            self._json(
                {
                    "ok": True,
                    "seen": seen,
                    "age": round(time.time() - seen, 1) if seen else None,
                    "waiting": len(PENDING),
                }
            )
            return
        if url.path == "/poll":
            query = parse_qs(url.query)
            try:
                wait = float((query.get("wait") or ["20"])[0])
            except ValueError:
                wait = 20.0
            LAST_SEEN["ts"] = time.time()
            command = wait_command(min(wait, 30.0))
            self._json(command or {})
            return
        self._json({"error": "нет такого пути"}, 404)

    def do_POST(self):
        url = urlparse(self.path)
        try:
            data = self._body()
        except (ValueError, UnicodeDecodeError) as exc:
            self._json({"ok": False, "error": f"плохой JSON: {exc}"}, 400)
            return

        if url.path == "/result":
            cmd_id = data.get("id")
            with LOCK:
                RESULTS[cmd_id] = data
                LOCK.notify_all()
            self._json({"ok": True})
            return

        if url.path == "/cmd":
            cmd_id = uuid.uuid4().hex[:12]
            command = dict(data)
            command["id"] = cmd_id
            with LOCK:
                PENDING.append(command)
                LOCK.notify_all()
            result = wait_result(cmd_id, float(data.get("_timeout") or 60))
            if result is None:
                self._json({"ok": False, "error": "расширение не ответило"}, 504)
                return
            self._json(result)
            return

        if url.path == "/quit":
            # вежливая остановка: отвечаем и гасим сервер из отдельного потока,
            # иначе текущий запрос не успевает получить ответ
            self._json({"ok": True, "stopped": True})
            threading.Thread(target=self.server.shutdown, daemon=True).start()
            return

        self._json({"error": "нет такого пути"}, 404)


def log(text: str) -> None:
    stamp = time.strftime("%Y-%m-%d %H:%M:%S")
    try:
        with state.log_path("bridge.log").open("a", encoding="utf-8") as fh:
            fh.write(f"{stamp} {text}\n")
    except OSError:
        pass


def main() -> int:
    parser = argparse.ArgumentParser(description="Marty Bridge: локальный мост")
    parser.add_argument(
        "--port", type=int, default=int(os.environ.get("MB_PORT") or 8765)
    )
    args = parser.parse_args()

    try:
        server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    except OSError as exc:
        log(f"не удалось занять порт {args.port}: {exc}")
        print(f"порт {args.port} занят: {exc}")
        return 1

    log(f"мост слушает 127.0.0.1:{args.port}")
    print(f"мост слушает http://127.0.0.1:{args.port}/")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log("остановлен вручную")
    except BaseException as exc:  # noqa: BLE001
        log(f"падение: {exc!r}")
        raise
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
