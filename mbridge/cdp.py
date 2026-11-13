"""Прямой доступ к целям браузера через порт отладки 9222.

Мосту это не нужно: расширение работает без порта. Модуль нужен для двух
вещей, которые снаружи расширения не сделать: посмотреть, кто держит отладчик
на вкладке, и перезагрузить само расширение после правок.

Требует `pip install "marty-bridge[cdp]"` и запуск браузера с
`--remote-debugging-port=9222`.

Команды:
    python -m mbridge.cdp list
    python -m mbridge.cdp attached
    python -m mbridge.cdp eval <подстрока-url> "выражение"
    python -m mbridge.cdp reload-ext [id расширения]
"""

from __future__ import annotations

import json
import os
import sys
import urllib.request

os.environ.setdefault("NO_PROXY", "127.0.0.1,localhost")
os.environ.setdefault("no_proxy", "127.0.0.1,localhost")

PORT = int(os.environ.get("CDP_PORT") or 9222)
BASE = f"http://127.0.0.1:{PORT}"


def _websocket():
    try:
        import websocket  # websocket-client
    except ImportError as exc:
        raise RuntimeError(
            'нет пакета websocket-client, установите: pip install "marty-bridge[cdp]"'
        ) from exc
    return websocket


def _get(path: str):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(BASE + path, timeout=8) as response:
        return json.loads(response.read().decode("utf-8"))


def targets() -> list:
    return _get("/json/list")


def find(needle: str) -> list:
    return [t for t in targets() if needle in (t.get("url") or "")]


def connect(ws_url: str):
    websocket = _websocket()
    return websocket.create_connection(
        ws_url,
        timeout=15,
        http_proxy_host=None,
        http_proxy_port=None,
        suppress_origin=True,
    )


def evaluate(ws_url: str, expression: str, timeout: int = 15):
    websocket = _websocket()
    ws = connect(ws_url)
    try:
        ws.send(
            json.dumps(
                {
                    "id": 1,
                    "method": "Runtime.evaluate",
                    "params": {
                        "expression": expression,
                        "awaitPromise": True,
                        "returnByValue": True,
                        "userGesture": True,
                    },
                }
            )
        )
        while True:
            message = json.loads(ws.recv())
            if message.get("id") != 1:
                continue
            if "error" in message:
                return {"ok": False, "error": message["error"]}
            result = message.get("result", {})
            if result.get("exceptionDetails"):
                return {"ok": False, "error": result["exceptionDetails"].get("text")}
            return {"ok": True, "value": result.get("result", {}).get("value")}
    finally:
        ws.close()


def browser_ws() -> str:
    return _get("/json/version")["webSocketDebuggerUrl"]


def browser_call(method: str, params: dict | None = None, timeout: int = 10):
    ws = connect(browser_ws())
    try:
        ws.send(json.dumps({"id": 1, "method": method, "params": params or {}}))
        while True:
            message = json.loads(ws.recv())
            if message.get("id") == 1:
                return message
    finally:
        ws.close()


def cmd_list() -> int:
    for target in targets():
        title = (target.get("title") or "")[:34]
        url = (target.get("url") or "")[:70]
        print(f"{target.get('type'):16} | {title:34} | {url}")
    return 0


def cmd_attached() -> int:
    result = browser_call("Target.getTargets")
    for target in result.get("result", {}).get("targetInfos", []):
        if target.get("type") not in ("page", "service_worker", "other", "worker"):
            continue
        mark = "ЗАНЯТА" if target.get("attached") else "      "
        print(f"{mark} {target.get('type'):15} | {(target.get('url') or '')[:70]}")
    return 0


def cmd_eval(needle: str, expression: str) -> int:
    hits = find(needle)
    if not hits:
        print(json.dumps({"ok": False, "error": f"нет цели с {needle!r}"}, ensure_ascii=False))
        return 1
    ws_url = hits[0].get("webSocketDebuggerUrl")
    if not ws_url:
        print(json.dumps({"ok": False, "error": "у цели нет webSocketDebuggerUrl"},
                         ensure_ascii=False))
        return 1
    out = evaluate(ws_url, expression)
    out["target"] = (hits[0].get("url") or "")[:80]
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0 if out.get("ok") else 1


def cmd_reload_ext(ext_id: str | None = None) -> int:
    hits = find("chrome-extension://")
    if ext_id:
        hits = [h for h in hits if ext_id in h["url"]]
    if not hits:
        print(
            json.dumps(
                {"ok": False, "error": "служебный воркер расширения не найден"},
                ensure_ascii=False,
            )
        )
        return 1
    print("расширение:", hits[0]["url"].split("/")[2])
    try:
        print(
            json.dumps(
                evaluate(hits[0]["webSocketDebuggerUrl"], "chrome.runtime.reload(), 'reload sent'"),
                ensure_ascii=False,
            )
        )
    except Exception as exc:  # noqa: BLE001
        # воркер умирает сразу после reload, это нормально
        print(json.dumps({"note": "соединение закрылось при перезагрузке", "detail": str(exc)},
                         ensure_ascii=False))
    return 0


def main(argv: list[str] | None = None) -> int:
    args = list(argv if argv is not None else sys.argv[1:])
    if not args:
        print(__doc__)
        return 2
    action = args[0]
    try:
        if action == "list":
            return cmd_list()
        if action == "attached":
            return cmd_attached()
        if action == "eval" and len(args) >= 3:
            return cmd_eval(args[1], args[2])
        if action == "reload-ext":
            return cmd_reload_ext(args[1] if len(args) > 1 else None)
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 1
    print(__doc__)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
