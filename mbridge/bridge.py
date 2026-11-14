"""Локальный мост между командами и расширением.

Расширение само опрашивает мост, поэтому браузеру не нужен порт отладки
и флаги запуска. Слушаем только 127.0.0.1.

Кроме транспорта мост отдает дашборд на http://127.0.0.1:8765/: состояние
связи, версию расширения и живой поток команд с длительностью и итогом.

Запуск:
    python -m mbridge.bridge [--port 8765]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
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
EXT = {"version": "", "at": 0.0}
FEED: deque = deque(maxlen=40)
STATS = {"total": 0, "ok": 0, "fail": 0, "ms_sum": 0.0}
STARTED = time.time()

DASHBOARD_PAGE = """<!doctype html>
<html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Marty Bridge</title>
<style>
  :root{
    --bg:#0f1115;--panel:#161a22;--panel2:#1c212b;--line:#242b37;
    --text:#e6e9ef;--dim:#8b93a7;--ok:#4ade80;--bad:#f87171;--warn:#fbbf24;
  }
  *{box-sizing:border-box}
  body{margin:0;padding:22px;background:var(--bg);color:var(--text);
       font:14px/1.5 "Segoe UI",system-ui,Arial}
  .wrap{max-width:960px;margin:0 auto}
  header{display:flex;align-items:center;gap:12px;flex-wrap:wrap;margin-bottom:18px}
  h1{font-size:17px;font-weight:600;margin:0}
  .dot{width:10px;height:10px;border-radius:50%;background:var(--dim)}
  .dot.ok{background:var(--ok)}.dot.bad{background:var(--bad)}.dot.warn{background:var(--warn)}
  .pill{font-size:12px;color:var(--dim);border:1px solid var(--line);border-radius:999px;padding:3px 10px}
  .grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px;margin-bottom:16px}
  .card{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:12px 14px}
  .card .k{font-size:11px;text-transform:uppercase;letter-spacing:.06em;color:var(--dim)}
  .card .v{font-size:20px;font-weight:600;margin-top:4px}
  .card .n{font-size:12px;color:var(--dim);margin-top:2px}
  .bar{display:flex;gap:8px;margin-bottom:14px}
  button{background:var(--panel2);color:var(--text);border:1px solid var(--line);
         border-radius:8px;padding:7px 14px;font:inherit;cursor:pointer}
  button:hover{border-color:#3a4456}
  table{width:100%;border-collapse:collapse;font-size:13px}
  th{text-align:left;color:var(--dim);font-weight:500;font-size:11px;
     text-transform:uppercase;letter-spacing:.06em;padding:8px 10px;
     border-bottom:1px solid var(--line)}
  td{padding:7px 10px;border-bottom:1px solid #1b202a;vertical-align:top}
  tr:last-child td{border-bottom:none}
  .mono{font-family:ui-monospace,Consolas,monospace;font-size:12px}
  .ok{color:var(--ok)}.bad{color:var(--bad)}
  .empty{color:var(--dim);padding:18px 10px;font-size:13px}
  #hint{margin-top:14px;font-size:12px;color:var(--dim);line-height:1.7}
  #shot{margin-top:14px}
  #shot img{max-width:100%;border:1px solid var(--line);border-radius:10px}
</style></head>
<body><div class="wrap">
<header>
  <span class="dot" id="dot"></span>
  <h1>Marty Bridge</h1>
  <span class="pill" id="state">подключаюсь</span>
  <span class="pill" id="ver">расширение ?</span>
  <span class="pill" id="up">аптайм ?</span>
</header>

<div class="grid">
  <div class="card"><div class="k">связь</div><div class="v" id="age">?</div>
    <div class="n" id="ageN">последний опрос</div></div>
  <div class="card"><div class="k">в очереди</div><div class="v" id="queue">0</div>
    <div class="n">команд ждет расширение</div></div>
  <div class="card"><div class="k">команд всего</div><div class="v" id="total">0</div>
    <div class="n" id="avg">среднее 0 мс</div></div>
  <div class="card"><div class="k">ошибок</div><div class="v" id="fail">0</div>
    <div class="n" id="okc">успешных 0</div></div>
</div>

<div class="bar">
  <button id="ping">проверить связь</button>
  <button id="shotBtn">снимок экрана</button>
</div>

<table>
  <thead><tr><th style="width:80px">время</th><th>команда</th>
    <th style="width:74px">мс</th><th style="width:80px">итог</th></tr></thead>
  <tbody id="feed"><tr><td colspan="4" class="empty">пока пусто</td></tr></tbody>
</table>

<div id="shot"></div>
<div id="hint">Обновляется само раз в секунду. Вкладку не закрывайте: она держит связь с расширением.</div>
</div>
<script>
const $ = (id) => document.getElementById(id);

function setDot(cls, text) {
  $("dot").className = "dot " + cls;
  $("state").textContent = text;
}

function msText(v) {
  return v >= 1000 ? (v / 1000).toFixed(1) + " с" : Math.round(v) + " мс";
}

let feedSig = null;
function renderFeed(items) {
  const list = items || [];
  // перерисовываем только при изменении, иначе выделение текста слетает каждую секунду
  const sig = list.map((i) => i.at + ":" + i.ms + ":" + (i.ok ? 1 : 0)).join("|");
  if (sig === feedSig) return;
  feedSig = sig;
  const body = $("feed");
  if (!list.length) {
    body.innerHTML = '<tr><td colspan="4" class="empty">пока пусто</td></tr>';
    return;
  }
  body.replaceChildren();
  for (const it of list) {
    const tr = document.createElement("tr");
    const t = document.createElement("td");
    t.className = "mono"; t.textContent = it.at;
    const s = document.createElement("td");
    s.className = "mono";
    s.textContent = it.summary;
    if (!it.ok && it.error) {
      const e = document.createElement("div");
      e.className = "bad"; e.style.fontSize = "12px"; e.textContent = it.error;
      s.appendChild(e);
    }
    const m = document.createElement("td");
    m.className = "mono"; m.textContent = it.ms;
    const r = document.createElement("td");
    r.className = it.ok ? "ok" : "bad";
    r.textContent = it.ok ? "ок" : "ошибка";
    tr.append(t, s, m, r);
    body.appendChild(tr);
  }
}

async function tick() {
  try {
    const r = await fetch("/api/state?t=" + Date.now(), { cache: "no-store" });
    const s = await r.json();
    const age = s.age;
    if (age === null || age === undefined) {
      setDot("warn", "ждем расширение");
      $("age").textContent = "нет";
      $("ageN").textContent = "опросов еще не было";
    } else if (age < 45) {
      setDot("ok", "на связи");
      $("age").textContent = Math.round(age) + " с";
      $("ageN").textContent = "последний опрос";
    } else {
      setDot("bad", "расширение молчит");
      $("age").textContent = Math.round(age) + " с";
      $("ageN").textContent = "нет опроса";
    }
    $("ver").textContent = "расширение " + (s.extension || "?");
    $("up").textContent = "аптайм " + s.uptime_text;
    $("queue").textContent = s.pending;
    $("total").textContent = s.stats.total;
    $("fail").textContent = s.stats.fail;
    $("okc").textContent = "успешных " + s.stats.ok;
    $("avg").textContent = "среднее " + Math.round(s.stats.avg_ms) + " мс";
    renderFeed(s.feed);

    const hint = $("hint");
    if ((age === null || age > 60) && s.hint) {
      hint.textContent = "Расширение не подключено. Загрузите папку как распакованное " +
        "расширение: " + s.hint;
    } else {
      hint.textContent = "Обновляется само раз в секунду. Вкладку не закрывайте: " +
        "она держит связь с расширением.";
    }
  } catch (e) {
    setDot("bad", "мост не отвечает");
  }
}

$("ping").onclick = async () => {
  $("ping").textContent = "проверяю";
  try {
    const r = await fetch("/api/ping", { method: "POST" });
    const d = await r.json();
    $("ping").textContent = d.ok ? "связь есть" : "нет связи";
  } catch (e) {
    $("ping").textContent = "нет связи";
  }
  setTimeout(() => { $("ping").textContent = "проверить связь"; }, 1500);
  tick();
};

$("shotBtn").onclick = async () => {
  $("shotBtn").textContent = "снимаю";
  try {
    const r = await fetch("/api/shot", { method: "POST" });
    const d = await r.json();
    const box = $("shot");
    if (d.ok && d.image) {
      const img = new Image();
      img.src = d.image;
      img.alt = "снимок экрана";
      box.replaceChildren(img);
    } else {
      box.textContent = "не вышло: " + (d.error || "нет ответа");
    }
  } catch (e) {
    $("shot").textContent = "не вышло: мост не отвечает";
  }
  $("shotBtn").textContent = "снимок экрана";
  tick();
};

tick();
setInterval(tick, 1000);
</script>
</body></html>
"""


def extension_hint() -> str:
    """Где лежит папка расширения. Показываем, если оно не подключено."""
    return state.extension_dir()


def uptime_text() -> str:
    total = int(time.time() - STARTED)
    hours, rest = divmod(total, 3600)
    minutes, seconds = divmod(rest, 60)
    if hours:
        return f"{hours} ч {minutes} мин"
    if minutes:
        return f"{minutes} мин {seconds} с"
    return f"{seconds} с"


def summary_of(data: dict) -> str:
    """Короткая строка для ленты: что именно попросили."""
    kind = str(data.get("kind") or "?")
    for key in ("url", "sel", "text", "path", "method"):
        if data.get(key):
            return f"{kind}: {str(data[key])[:70]}"
    if data.get("code"):
        return f"{kind}: {str(data['code'])[:70]}"
    if data.get("steps"):
        return f"{kind}: шагов {len(data['steps'])}"
    return kind


def record(kind: str, summary: str, ms: float, ok: bool, error: str = "") -> None:
    STATS["total"] += 1
    STATS["ms_sum"] += ms
    if ok:
        STATS["ok"] += 1
    else:
        STATS["fail"] += 1
    FEED.appendleft(
        {
            "at": time.strftime("%H:%M:%S"),
            "kind": kind,
            "summary": summary,
            "ms": round(ms),
            "ok": ok,
            "error": error[:160],
        }
    )


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


def run_command(command: dict, timeout: float):
    """Ставит команду в очередь, ждет ответ и пишет строку в ленту."""
    cmd_id = uuid.uuid4().hex[:12]
    # служебные поля с подчеркиванием остаются на стороне моста
    command = {k: v for k, v in command.items() if not k.startswith("_")}
    command["id"] = cmd_id
    summary = summary_of(command)
    started = time.time()
    with LOCK:
        PENDING.append(command)
        LOCK.notify_all()
    result = wait_result(cmd_id, timeout)
    elapsed = (time.time() - started) * 1000

    if result is None:
        record(command.get("kind", "?"), summary, elapsed, False, "расширение не ответило")
        return None

    ok = bool(result.get("ok"))
    record(
        command.get("kind", "?"),
        summary,
        elapsed,
        ok,
        "" if ok else str(result.get("error") or ""),
    )
    return result


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
            body = DASHBOARD_PAGE.encode("utf-8")
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

        if url.path == "/api/state":
            seen = LAST_SEEN["ts"]
            total = STATS["total"]
            self._json(
                {
                    "ok": True,
                    "age": round(time.time() - seen, 1) if seen else None,
                    "pending": len(PENDING),
                    "extension": EXT["version"],
                    "uptime_text": uptime_text(),
                    "stats": {
                        "total": total,
                        "ok": STATS["ok"],
                        "fail": STATS["fail"],
                        "avg_ms": (STATS["ms_sum"] / total) if total else 0,
                    },
                    "feed": list(FEED),
                    "hint": extension_hint(),
                }
            )
            return

        if url.path == "/poll":
            query = parse_qs(url.query)
            try:
                wait = float((query.get("wait") or ["20"])[0])
            except ValueError:
                wait = 20.0
            version = (query.get("v") or [""])[0]
            if version:
                EXT["version"] = version
                EXT["at"] = time.time()
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
            result = run_command(data, float(data.get("_timeout") or 60))
            if result is None:
                self._json({"ok": False, "error": "расширение не ответило"}, 504)
                return
            self._json(result)
            return

        if url.path == "/api/ping":
            result = run_command({"kind": "ping"}, 10)
            ok = bool(result and result.get("ok"))
            if ok:
                version = (result.get("data") or {}).get("version")
                if version:
                    EXT["version"] = str(version)
            self._json({"ok": ok, "data": result})
            return

        if url.path == "/api/shot":
            command = {
                "kind": "cdp",
                "method": "Page.captureScreenshot",
                "params": {"format": "png"},
            }
            tab = state.tab()
            if tab:
                command["tabId"] = tab
            result = run_command(command, 25)
            if not result or not result.get("ok"):
                error = (result or {}).get("error") or "нет ответа"
                self._json({"ok": False, "error": error})
                return
            raw = (result.get("data") or {}).get("data") or ""
            if not raw:
                self._json({"ok": False, "error": "пустой снимок"})
                return
            self._json({"ok": True, "image": "data:image/png;base64," + raw})
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
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError, OSError):
            pass

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
