"""Команды `mb`: браузер, Telegram и диагностика.

Примеры:
    mb status                              все ли на месте
    mb up                                  поднять мост и вкладку-сторож
    mb down                                остановить мост и выключить автозапуск
    mb tabs                                список вкладок
    mb use 123456                          выбрать рабочую вкладку
    mb open https://ya.ru                  открыть вкладку
    mb probe                               карта открытой страницы
    mb text "h1"                           текст элемента
    mb js "document.title"                 выполнить JS
    mb shot page.png                       скриншот
    mb do '[{"op":"open","url":"https://ya.ru"},{"op":"text","sel":"h1"}]'
    mb run steps.json                      сценарий из файла
    mb tg send "Избранное" "привет"        написать в Telegram
    mb tg look "Избранное" tg.png          открыть чат и снять скриншот
    mb cdp attached                        кто держит отладчик на вкладках
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time

from . import __version__, client, driver, state


# ---------------------------------------------------------------- помощники


def _json(obj) -> None:
    print(json.dumps(obj, ensure_ascii=False))


def _need_bridge() -> bool:
    """Поднять мост, если его нет. False значит связи не будет."""
    return driver.ensure()


def _state_tab() -> int | None:
    return state.tab()


def _die(text: str, code: int = 1) -> int:
    _json({"ok": False, "error": text})
    return code


# ---------------------------------------------------------------- команды


def cmd_up(args) -> int:
    if client.alive():
        _json({"мост": "уже работает", "адрес": client.base()})
        return 0
    if state.off_flag().exists():
        state.off_flag().unlink()
        print("снял флаг bridge.off")
    if not driver.ensure():
        return _die("не удалось поднять мост, смотрите " + str(state.log_path("supervisor.log")))
    _json({"мост": "поднят", "адрес": client.base()})
    return 0


def cmd_down(args) -> int:
    stopped = False
    if client.alive():
        try:
            import http.client

            conn = http.client.HTTPConnection(client.host(), client.port(), timeout=3)
            conn.request("POST", "/quit", b"{}", {"Content-Type": "application/json"})
            conn.getresponse().read()
            conn.close()
            stopped = True
        except Exception:  # noqa: BLE001
            pass
    try:
        state.off_flag().write_text("выключено через mb down\n", encoding="utf-8")
    except OSError:
        pass
    _json(
        {
            "мост": "остановлен" if stopped else "не работал",
            "автозапуск": "выключен, файл " + str(state.off_flag()),
            "важно": "наставник завершится сам в течение 30 секунд",
        }
    )
    return 0


def cmd_status(args) -> int:
    ok = True
    print(f"marty-bridge {__version__}")
    print(f"адрес моста: {client.base()}")
    print(f"каталог данных: {state.home()}")

    if not client.alive():
        print("мост: не отвечает. Запустите: mb up")
        ok = False
    else:
        health = client.health()
        age = health.get("age")
        print(f"мост: работает, опрос расширения: "
              f"{'нет' if age is None else str(round(float(age))) + ' сек назад'}")
        try:
            pong = client.send({"kind": "ping"}, 10)
            print(f"расширение: на связи, версия {pong.get('version')}")
        except RuntimeError as exc:
            print(f"расширение: НЕТ ({exc})")
            ok = False

    try:
        tabs = client.send({"kind": "tabs"}, 15) or []
        keepalive = [
            t for t in tabs if f"{client.host()}:{client.port()}" in (t.get("url") or "")
        ]
        print(f"вкладок: {len(tabs)}, вкладка-сторож: "
              f"{'есть, ' + str(keepalive[0]['id']) if keepalive else 'НЕТ'}")
        if not keepalive:
            ok = False
    except RuntimeError:
        pass

    try:
        from . import telegram

        try:
            window = telegram.find_window()
            print(f"telegram: окно найдено, заголовок {telegram.title(window)!r}")
        except telegram.TelegramUnavailable as exc:
            print(f"telegram: недоступен ({exc})")
    except Exception as exc:  # noqa: BLE001
        print(f"telegram: ошибка проверки ({exc})")

    print(f"рабочая вкладка: {_state_tab()}")
    return 0 if ok else 1


def cmd_ping(args) -> int:
    _json(client.send({"kind": "ping"}, 15))
    return 0


def cmd_diag(args) -> int:
    _json(client.send({"kind": "diag"}, 30))
    return 0


def cmd_tabs(args) -> int:
    current = _state_tab()
    for tab in client.send({"kind": "tabs"}, 20) or []:
        mark = "*" if tab["id"] == current else " "
        title = (tab.get("title") or "")[:45]
        url = (tab.get("url") or "")[:70]
        print(f"{mark}{tab['id']:12d} | {title:45} | {url}")
    return 0


def cmd_use(args) -> int:
    print("рабочая вкладка:", state.set_tab(int(args.tab)))
    return 0


def cmd_open(args) -> int:
    _json(driver.do_open({"url": args.url}))
    return 0


def cmd_close(args) -> int:
    _json(driver.run_step({"op": "close", "tab": int(args.tab)}, _state_tab()))
    return 0


def cmd_probe(args) -> int:
    print(json.dumps(driver.do_probe(_state_tab()), ensure_ascii=False, indent=1))
    return 0


def cmd_text(args) -> int:
    print(driver.run_step({"op": "text", "sel": args.sel}, _state_tab())["text"])
    return 0


def cmd_js(args) -> int:
    _json(driver.eval_with_lib(args.code, _state_tab()))
    return 0


def cmd_jsf(args) -> int:
    code = open(args.path, encoding="utf-8").read()
    _json(driver.eval_with_lib(code, _state_tab()))
    return 0


def cmd_find(args) -> int:
    _json(
        driver.eval_with_lib(
            "(function(){const e=BD.byText(%s,{});if(!e)return null;"
            "const c=BD.center(e);return {tag:e.tagName,cls:String(e.className).slice(0,60),"
            "text:(e.innerText||'').slice(0,80),x:c.x,y:c.y};})()"
            % json.dumps(args.text, ensure_ascii=False),
            _state_tab(),
        )
    )
    return 0


def cmd_shot(args) -> int:
    _json(driver.run_step({"op": "shot", "path": args.path}, _state_tab()))
    return 0


def cmd_do(args) -> int:
    data = json.loads(args.steps)
    steps = data.get("steps") if isinstance(data, dict) else data
    results = driver.run_steps(steps)
    return 0 if all(r.get("ok") for r in results) else 1


def cmd_run(args) -> int:
    if os.path.exists(args.path):
        data = json.load(open(args.path, encoding="utf-8"))
    else:
        data = json.loads(args.path)
    steps = data.get("steps") if isinstance(data, dict) else data
    results = driver.run_steps(steps)
    return 0 if all(r.get("ok") for r in results) else 1


def cmd_tg(args) -> int:
    from . import telegram

    command = args.tg_command
    rest = args.rest or []
    try:
        _json(telegram.run(command, rest))
        return 0
    except telegram.TelegramUnavailable as exc:
        return _die(str(exc))
    except IndexError:
        return _die(f"команде tg {command} не хватает аргументов")
    except Exception as exc:  # noqa: BLE001
        return _die(str(exc))


def cmd_cdp(args) -> int:
    from . import cdp

    return cdp.main([args.cdp_command, *args.rest])


def cmd_autostart(args) -> int:
    """Прописать запуск наставника при входе в систему."""
    if sys.platform != "win32":
        return _die("автозапуск настраивается вручную: добавьте mbridge.supervisor "
                    "в автозагрузку вашей системы")
    pythonw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    if not os.path.exists(pythonw):
        pythonw = sys.executable
    command = f'"{pythonw}" -m mbridge.supervisor'
    try:
        subprocess.run(
            ["reg", "add", r"HKCU\Software\Microsoft\Windows\CurrentVersion\Run",
             "/v", "Marty Bridge", "/t", "REG_SZ", "/d", command, "/f"],
            check=True,
            capture_output=True,
        )
    except subprocess.CalledProcessError as exc:
        return _die("не смог записать ключ автозапуска: %s" % exc)
    _json({"автозапуск": "включен", "команда": command})
    return 0


def cmd_autostart_off(args) -> int:
    if sys.platform != "win32":
        return _die("уберите mbridge.supervisor из автозагрузки вручную")
    subprocess.run(
        ["reg", "delete", r"HKCU\Software\Microsoft\Windows\CurrentVersion\Run",
         "/v", "Marty Bridge", "/f"],
        capture_output=True,
    )
    _json({"автозапуск": "выключен"})
    return 0


# ---------------------------------------------------------------- разбор


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mb",
        description="Marty Bridge: руки агента в браузере и в Telegram",
    )
    parser.add_argument("--version", action="version", version=f"marty-bridge {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    for name, handler, help_text in (
        ("up", cmd_up, "поднять мост и вкладку-сторож"),
        ("down", cmd_down, "остановить мост и выключить автозапуск"),
        ("status", cmd_status, "проверить, все ли на месте"),
        ("ping", cmd_ping, "проверить связь с расширением"),
        ("diag", cmd_diag, "диагностика расширения и вкладки-сторож"),
        ("tabs", cmd_tabs, "список вкладок"),
        ("probe", cmd_probe, "карта открытой страницы"),
    ):
        sub.add_parser(name, help=help_text).set_defaults(func=handler)

    p = sub.add_parser("use", help="выбрать рабочую вкладку")
    p.add_argument("tab", type=int)
    p.set_defaults(func=cmd_use)

    p = sub.add_parser("open", help="открыть вкладку")
    p.add_argument("url")
    p.set_defaults(func=cmd_open)

    p = sub.add_parser("close", help="закрыть вкладку")
    p.add_argument("tab", type=int)
    p.set_defaults(func=cmd_close)

    p = sub.add_parser("text", help="текст элемента")
    p.add_argument("sel", nargs="?", default="body")
    p.set_defaults(func=cmd_text)

    p = sub.add_parser("js", help="выполнить JS в рабочей вкладке")
    p.add_argument("code")
    p.set_defaults(func=cmd_js)

    p = sub.add_parser("jsf", help="выполнить JS из файла")
    p.add_argument("path")
    p.set_defaults(func=cmd_jsf)

    p = sub.add_parser("find", help="найти элемент по тексту и вернуть координаты")
    p.add_argument("text")
    p.set_defaults(func=cmd_find)

    p = sub.add_parser("shot", help="скриншот страницы")
    p.add_argument("path", nargs="?", default="shot.png")
    p.set_defaults(func=cmd_shot)

    p = sub.add_parser("do", help="выполнить сценарий из JSON строкой")
    p.add_argument("steps")
    p.set_defaults(func=cmd_do)

    p = sub.add_parser("run", help="выполнить сценарий из файла")
    p.add_argument("path")
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("tg", help="Telegram Desktop")
    p.add_argument("tg_command",
                   choices=["title", "shot", "open", "send", "sendfile", "look",
                            "esc", "click", "paste", "key"])
    p.add_argument("rest", nargs="*")
    p.set_defaults(func=cmd_tg)

    p = sub.add_parser("cdp", help="порт отладки 9222")
    p.add_argument("cdp_command", choices=["list", "attached", "eval", "reload-ext"])
    p.add_argument("rest", nargs="*")
    p.set_defaults(func=cmd_cdp)

    sub.add_parser("autostart", help="включить автозапуск при входе в систему").set_defaults(
        func=cmd_autostart
    )
    sub.add_parser("autostart-off", help="выключить автозапуск").set_defaults(
        func=cmd_autostart_off
    )
    return parser


BROWSER_COMMANDS = {
    "ping", "diag", "tabs", "use", "open", "close", "probe", "text", "js",
    "jsf", "find", "shot", "do", "run",
}


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command in BROWSER_COMMANDS:
        if not _need_bridge():
            return 1
    try:
        return args.func(args)
    except RuntimeError as exc:
        return _die(str(exc))
    except ValueError as exc:
        return _die(f"плохие данные в аргументах: {exc}")
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
