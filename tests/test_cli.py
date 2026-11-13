import pytest

from mbridge import cli


def test_parser_requires_command():
    with pytest.raises(SystemExit):
        cli.main([])


def test_parser_knows_browser_commands():
    simple = ("up", "down", "status", "tabs", "probe")
    for name in simple:
        assert cli.build_parser().parse_args([name]).command == name
    for name, extra in (("open", "https://ya.ru"), ("do", "[]"), ("run", "steps.json"),
                        ("tg", "title"), ("cdp", "list")):
        assert cli.build_parser().parse_args([name, extra]).command == name


def test_bad_json_in_do_returns_error(capsys, monkeypatch):
    monkeypatch.setattr(cli, "_need_bridge", lambda: True)
    code = cli.main(["do", "{не json"])
    out = capsys.readouterr().out
    assert code == 1
    assert "плохие данные" in out


def test_tg_without_deps_or_window_reports_error(capsys, monkeypatch):
    from mbridge import telegram

    def boom(*args, **kwargs):
        raise telegram.TelegramUnavailable("нет окна")

    monkeypatch.setattr(telegram, "find_window", boom)
    code = cli.main(["tg", "title"])
    out = capsys.readouterr().out
    assert code == 1
    assert "нет окна" in out


def test_close_refuses_keepalive_tab(monkeypatch):
    """Вкладку-сторож закрывать нельзя: на ней держится связь."""
    from mbridge import driver

    def fake_send(payload, timeout=60):
        assert payload["kind"] == "tabs"
        return [{"id": 5, "url": "http://127.0.0.1:8765/"}]

    monkeypatch.setattr(driver, "send", fake_send)
    monkeypatch.setattr("mbridge.client.host", lambda: "127.0.0.1")
    monkeypatch.setattr("mbridge.client.port", lambda: 8765)
    with pytest.raises(RuntimeError) as err:
        driver.run_step({"op": "close", "tab": 5}, 5)
    assert "вкладка-сторож" in str(err.value)
