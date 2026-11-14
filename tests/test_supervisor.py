"""Решения наставника: когда открывать вкладку-сторож.

Здесь раньше появлялись дубли вкладок: наставник решал, что вкладки нет,
через минуту после старта моста, пока браузер еще загружал расширение.
"""

from mbridge import supervisor


def test_silence_short_means_no_tab():
    ok, why = supervisor.should_open_tab(
        now=1000, seen=990, started_at=0, last_open=0, opens=0,
        ever_seen=True, browser_up=True,
    )
    assert ok is False
    assert why == "на связи"


def test_extension_never_seen_means_no_tab():
    """Расширение выключено: вкладка не поможет, ее надо включать руками."""
    ok, why = supervisor.should_open_tab(
        now=10000, seen=0, started_at=0, last_open=0, opens=0,
        ever_seen=False, browser_up=True,
    )
    assert ok is False
    assert "не подключалось" in why


def test_recent_open_is_not_repeated():
    ok, why = supervisor.should_open_tab(
        now=10000, seen=0, started_at=0, last_open=9900, opens=1,
        ever_seen=True, browser_up=True,
    )
    assert ok is False
    assert why == "недавно открывали"


def test_open_limit_is_respected():
    ok, why = supervisor.should_open_tab(
        now=100000, seen=0, started_at=0, last_open=0, opens=supervisor.MAX_OPENS,
        ever_seen=True, browser_up=True,
    )
    assert ok is False
    assert "лимит" in why


def test_closed_browser_is_not_launched():
    ok, why = supervisor.should_open_tab(
        now=100000, seen=0, started_at=0, last_open=0, opens=0,
        ever_seen=True, browser_up=False,
    )
    assert ok is False
    assert why == "браузер закрыт"


def test_long_silence_opens_tab():
    ok, why = supervisor.should_open_tab(
        now=100000, seen=100, started_at=0, last_open=0, opens=0,
        ever_seen=True, browser_up=True,
    )
    assert ok is True
    assert "молчит" in why


def test_boot_grace_survives_first_failed_check():
    """Окно милосердия считается от запуска моста, а не от последней проверки."""
    ok, _ = supervisor.should_open_tab(
        now=60, seen=0, started_at=10, last_open=0, opens=0,
        ever_seen=False, browser_up=True,
    )
    assert ok is False
