import http.client
import json
import threading
import time
from collections import deque
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from mbridge import bridge as bridge_mod
from mbridge import client, state
from mbridge.bridge import Handler


@pytest.fixture()
def bridge(monkeypatch, tmp_path):
    """Живой мост на свободном порту, без расширения и с чистым состоянием."""
    monkeypatch.setenv("MB_HOME", str(tmp_path))
    monkeypatch.setattr(bridge_mod, "PENDING", deque())
    monkeypatch.setattr(bridge_mod, "RESULTS", {})
    monkeypatch.setattr(bridge_mod, "FEED", deque(maxlen=40))
    monkeypatch.setattr(
        bridge_mod, "STATS", {"total": 0, "ok": 0, "fail": 0, "ms_sum": 0.0}
    )
    monkeypatch.setattr(bridge_mod, "EXT", {"version": "", "at": 0.0})
    monkeypatch.setattr(bridge_mod, "LAST_SEEN", {"ts": 0.0})
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    monkeypatch.setenv("MB_PORT", str(port))
    client.reset()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield port
    server.shutdown()
    server.server_close()
    thread.join(timeout=5)
    client.reset()


def get(port, path):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    conn.request("GET", path)
    raw = conn.getresponse().read().decode("utf-8")
    conn.close()
    return json.loads(raw)


def test_health_without_extension(bridge):
    health = client.health()
    assert health["ok"] is True
    assert health["age"] is None
    assert health["waiting"] == 0


def test_poll_returns_empty_on_timeout(bridge):
    conn = http.client.HTTPConnection("127.0.0.1", bridge, timeout=5)
    conn.request("GET", "/poll?wait=0.2")
    body = json.loads(conn.getresponse().read().decode("utf-8"))
    conn.close()
    assert body == {}


def test_command_without_extension_times_out(bridge):
    with pytest.raises(RuntimeError) as err:
        client.send({"kind": "ping"}, 0.2)
    assert "не отвечает" in str(err.value)


def test_quit_stops_bridge(bridge):
    conn = http.client.HTTPConnection("127.0.0.1", bridge, timeout=5)
    conn.request("POST", "/quit", b"{}", {"Content-Type": "application/json"})
    body = json.loads(conn.getresponse().read().decode("utf-8"))
    conn.close()
    assert body["stopped"] is True
    for _ in range(30):
        if not client.alive():
            break
        time.sleep(0.1)
    assert client.alive() is False


def test_home_from_env(tmp_path, monkeypatch):
    monkeypatch.setenv("MB_HOME", str(tmp_path / "custom"))
    assert state.home() == tmp_path / "custom"
    assert state.home().exists()


def test_port_from_env(monkeypatch):
    monkeypatch.setenv("MB_PORT", "9999")
    assert client.port() == 9999
    monkeypatch.setenv("MB_PORT", "не число")
    assert client.port() == client.DEFAULT_PORT
    monkeypatch.delenv("MB_PORT")
    assert client.port() == client.DEFAULT_PORT


# --- дашборд ---------------------------------------------------------------


def test_dashboard_page_is_served(bridge):
    conn = http.client.HTTPConnection("127.0.0.1", bridge, timeout=5)
    conn.request("GET", "/")
    response = conn.getresponse()
    html = response.read().decode("utf-8")
    conn.close()
    assert response.status == 200
    assert "Marty Bridge" in html
    # страница обязана сама опрашивать состояние, иначе это не дашборд
    assert "/api/state" in html


def test_api_state_shape(bridge):
    data = get(bridge, "/api/state")
    assert data["ok"] is True
    assert data["age"] is None
    assert data["pending"] == 0
    assert set(data["stats"]) == {"total", "ok", "fail", "avg_ms"}
    assert data["stats"]["total"] == 0
    assert data["feed"] == []
    assert isinstance(data["uptime_text"], str)


def test_poll_reports_extension_version(bridge):
    conn = http.client.HTTPConnection("127.0.0.1", bridge, timeout=5)
    conn.request("GET", "/poll?wait=0.1&v=9.9")
    conn.getresponse().read()
    conn.close()
    data = get(bridge, "/api/state")
    assert data["extension"] == "9.9"
    assert data["age"] is not None


def test_command_lands_in_feed_without_service_fields(bridge):
    seen = {}

    def fake_extension():
        conn = http.client.HTTPConnection("127.0.0.1", bridge, timeout=10)
        conn.request("GET", "/poll?wait=5")
        cmd = json.loads(conn.getresponse().read().decode("utf-8"))
        conn.close()
        seen.update(cmd)
        payload = json.dumps(
            {"id": cmd.get("id"), "ok": True, "data": {"pong": True}}
        ).encode("utf-8")
        conn2 = http.client.HTTPConnection("127.0.0.1", bridge, timeout=5)
        conn2.request(
            "POST", "/result", payload, {"Content-Type": "application/json"}
        )
        conn2.getresponse().read()
        conn2.close()

    thread = threading.Thread(target=fake_extension, daemon=True)
    thread.start()
    time.sleep(0.2)
    assert client.send({"kind": "ping"}, 5) == {"pong": True}
    thread.join(timeout=5)

    # служебный _timeout остается на стороне моста
    assert "_timeout" not in seen
    assert seen["kind"] == "ping"

    data = get(bridge, "/api/state")
    assert data["stats"]["total"] == 1
    assert data["stats"]["ok"] == 1
    assert data["feed"][0]["summary"] == "ping"
    assert data["feed"][0]["ok"] is True


def test_api_state_counts_failures(bridge):
    conn = http.client.HTTPConnection("127.0.0.1", bridge, timeout=20)
    conn.request("POST", "/api/ping")
    conn.getresponse().read()
    conn.close()
    data = get(bridge, "/api/state")
    assert data["stats"]["total"] == 1
    assert data["stats"]["fail"] == 1
    assert data["feed"][0]["ok"] is False
    assert "не ответило" in data["feed"][0]["error"]


def test_api_state_hint_is_computed_not_hardcoded(bridge, monkeypatch, tmp_path):
    """Путь к папке расширения считается на месте, а не зашит в код."""
    monkeypatch.setenv("MB_EXTENSION_DIR", str(tmp_path))
    assert get(bridge, "/api/state")["hint"] == str(tmp_path)


def test_dashboard_page_has_no_local_paths():
    """В саму страницу не должен попасть ни один путь с диска."""
    page = bridge_mod.DASHBOARD_PAGE
    for marker in ("C:\\\\", "C:/", "/home/", "/Users/", "Geroin", "AppData"):
        assert marker not in page, f"в странице оказался путь: {marker}"


def test_summary_of_variants():
    assert bridge_mod.summary_of({"kind": "open", "url": "https://a"}) == "open: https://a"
    assert bridge_mod.summary_of({"kind": "eval", "code": "1+1"}) == "eval: 1+1"
    assert bridge_mod.summary_of({"kind": "steps", "steps": [1, 2, 3]}) == "steps: шагов 3"
    assert bridge_mod.summary_of({"kind": "ping"}) == "ping"
    assert bridge_mod.summary_of({}) == "?"


def test_uptime_text_formats(monkeypatch):
    now = time.time()
    monkeypatch.setattr(bridge_mod, "STARTED", now - 5)
    assert bridge_mod.uptime_text() == "5 с"
    monkeypatch.setattr(bridge_mod, "STARTED", now - 125)
    assert bridge_mod.uptime_text() == "2 мин 5 с"
    monkeypatch.setattr(bridge_mod, "STARTED", now - 3661)
    assert bridge_mod.uptime_text() == "1 ч 1 мин"


def test_extension_dir_from_env(monkeypatch, tmp_path):
    monkeypatch.setenv("MB_EXTENSION_DIR", str(tmp_path))
    assert state.extension_dir() == str(tmp_path)


def test_extension_dir_found_in_repo(monkeypatch):
    monkeypatch.delenv("MB_EXTENSION_DIR", raising=False)
    folder = state.extension_dir()
    assert folder.endswith("extension")
    assert (Path(folder) / "manifest.json").exists()


def test_loadable_rejects_underscore_entries(tmp_path):
    """Chrome не грузит папку, где рядом с манифестом лежит __pycache__."""
    bad = tmp_path / "bad"
    bad.mkdir()
    (bad / "manifest.json").write_text("{}", encoding="utf-8")
    assert state._loadable(bad) is True
    (bad / "__pycache__").mkdir()
    assert state._loadable(bad) is False


def test_loadable_rejects_folder_without_manifest(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    assert state._loadable(empty) is False
    assert state._loadable(tmp_path / "нет такой") is False
