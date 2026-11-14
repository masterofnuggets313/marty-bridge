import json

import pytest

from mbridge import driver, state


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("MB_HOME", str(tmp_path))


def test_state_keeps_working_tab():
    assert state.tab() is None
    assert state.set_tab(123456) == 123456
    assert state.tab() == 123456
    saved = json.loads((state.home() / "state.json").read_text(encoding="utf-8"))
    assert saved["tab"] == 123456


def test_state_survives_broken_file():
    (state.home() / "state.json").write_text("{не json", encoding="utf-8")
    assert state.tab() is None
    state.set_tab(7)
    assert state.tab() == 7


def test_unknown_step_raises():
    with pytest.raises(RuntimeError) as err:
        driver.run_step({"op": "телепорт"}, 1)
    assert "неизвестный шаг" in str(err.value)


def test_failed_step_stops_scenario(capsys):
    results = driver.run_steps(
        [{"op": "sleep", "ms": 1}, {"op": "телепорт"}, {"op": "sleep", "ms": 1}]
    )
    assert [item.get("ok") for item in results] == [True, False]
    assert "остановлен на шаге 1" in capsys.readouterr().out


def test_probe_returns_page_map_keys():
    assert "url" in driver.PROBE
    assert "fields" in driver.PROBE
    assert "click" in driver.PROBE


def test_library_has_helpers():
    for name in ("byText", "field", "setVal", "cond", "center"):
        assert name in driver.LIB


def test_ensure_falls_back_without_breakaway(monkeypatch, tmp_path):
    """Если отрыв от job-объекта запрещен, наставник поднимается без него."""
    flags = []

    class FakeProc:
        pid = 1

    def fake_popen(cmd, **kwargs):
        flags.append(kwargs.get("creationflags"))
        if len(flags) == 1:
            raise OSError(5, "Отказано в доступе")
        return FakeProc()

    monkeypatch.setattr(driver.subprocess, "Popen", fake_popen)
    # первый опрос до запуска говорит «моста нет», дальше он уже есть
    seen = []

    def fake_alive(*args, **kwargs):
        seen.append(1)
        return len(seen) > 1

    monkeypatch.setattr(driver.client, "alive", fake_alive)
    monkeypatch.setattr(driver.state, "off_flag", lambda: tmp_path / "bridge.off")

    assert driver.ensure(verbose=False) is True
    assert len(flags) == 2
    assert flags[1] != flags[0]
    assert flags[1] & getattr(driver.subprocess, "DETACHED_PROCESS", 0)


def test_ensure_reports_when_both_spawns_fail(monkeypatch, tmp_path, capsys):
    def fake_popen(cmd, **kwargs):
        raise OSError(5, "Отказано в доступе")

    monkeypatch.setattr(driver.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(driver.client, "alive", lambda *a, **k: False)
    monkeypatch.setattr(driver.state, "off_flag", lambda: tmp_path / "bridge.off")

    assert driver.ensure() is False
    assert "Отказано в доступе" in capsys.readouterr().out
