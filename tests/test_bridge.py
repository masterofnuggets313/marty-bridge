import http.client
import json
import threading
import time
from http.server import ThreadingHTTPServer

import pytest

from mbridge import client, state
from mbridge.bridge import Handler


@pytest.fixture()
def bridge(monkeypatch, tmp_path):
    """Живой мост на свободном порту, без расширения."""
    monkeypatch.setenv("MB_HOME", str(tmp_path))
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
