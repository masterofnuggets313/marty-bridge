"""Транспорт до моста: POST /cmd, GET /health.

Одно соединение на процесс: сценарий из 20 шагов не должен каждый раз
здороваться заново, это 20-40 мс на шаг.
"""

from __future__ import annotations

import http.client
import json
import os
import socket

DEFAULT_PORT = 8765
CMD_TIMEOUT = 60

_conn: http.client.HTTPConnection | None = None


def port() -> int:
    raw = os.environ.get("MB_PORT") or os.environ.get("BRIDGE_PORT") or DEFAULT_PORT
    try:
        return int(raw)
    except ValueError:
        return DEFAULT_PORT


def host() -> str:
    return os.environ.get("MB_HOST") or "127.0.0.1"


def base() -> str:
    return f"http://{host()}:{port()}"


def reset() -> None:
    """Закрыть живое соединение. Нужно после смены порта и в тестах."""
    global _conn
    try:
        if _conn is not None:
            _conn.close()
    except Exception:  # noqa: BLE001
        pass
    _conn = None


def _post(payload: dict, timeout: float = CMD_TIMEOUT) -> dict:
    global _conn
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    last = None
    for _ in range(2):
        try:
            if _conn is None:
                _conn = http.client.HTTPConnection(host(), port(), timeout=timeout)
            _conn.request(
                "POST",
                "/cmd",
                body,
                {"Content-Type": "application/json", "Content-Length": str(len(body))},
            )
            response = _conn.getresponse()
            text = response.read().decode("utf-8")
            if response.status != 200:
                last = f"HTTP {response.status}: {text[:200]}"
                reset()
                continue
            return json.loads(text)
        except (OSError, http.client.HTTPException, ValueError) as exc:
            last = str(exc)
            reset()
    return {"ok": False, "error": last or "мост не ответил"}


def send(payload: dict, timeout: float = CMD_TIMEOUT):
    """Отправить команду расширению и вернуть поле data.

    Бросает RuntimeError с человеческим текстом, если связи нет.
    """
    packet = dict(payload)
    packet["_timeout"] = timeout
    result = _post(packet, timeout + 10)
    if not result.get("ok"):
        message = str(result.get("error") or "ошибка")
        if "504" in message or "не ответило" in message:
            raise RuntimeError(
                "расширение не отвечает. Проверьте вкладку "
                f"{base()}/ и что Marty Bridge включен на странице расширений"
            )
        raise RuntimeError(message)
    return result.get("data")


def alive(timeout: float = 2.0) -> bool:
    try:
        conn = http.client.HTTPConnection(host(), port(), timeout=timeout)
        conn.request("GET", "/health")
        ok = conn.getresponse().status == 200
        conn.close()
        return ok
    except (OSError, http.client.HTTPException, socket.timeout):
        return False


def health(timeout: float = 2.0) -> dict:
    try:
        conn = http.client.HTTPConnection(host(), port(), timeout=timeout)
        conn.request("GET", "/health")
        raw = conn.getresponse().read().decode("utf-8")
        conn.close()
        return json.loads(raw)
    except (OSError, http.client.HTTPException, ValueError):
        return {}
