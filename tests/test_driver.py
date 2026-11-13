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
