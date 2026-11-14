"""Драйвер браузера: шаги, которые выполняются пачкой за один вызов.

Смысл: один шаг за один запуск процесса это 20 запусков на сценарий из 20
действий. Здесь весь сценарий идет подряд в одном процессе и возвращает отчет
по каждому шагу. Чтение или заполнение стоит 15-40 мс.

Шаги (op):
  open     url, active=true            открыть вкладку, дождаться загрузки
  use      tab                         сделать вкладку рабочей
  close    tab                         закрыть вкладку
  goto     url                         перейти в текущей вкладке
  reload                               перезагрузить страницу
  back                                 назад по истории
  wait     text|sel|url|gone|value     ждать условие, timeout=15
  click    text|sel, index=0, expect   клик мышью по центру элемента
  check    text|sel                    поставить чекбокс или радио
  fill     label|sel, value            заполнить поле
  select   label|sel, value            выбрать значение в списке
  type     value                       ввод текста через CDP, для полей с маской
  press    key                         Enter, Tab, Escape, стрелки
  text     sel                         вернуть innerText
  attr     sel, name                   вернуть атрибут
  extract  sel                         вернуть список текстов
  js       code                        выполнить выражение, вернуть значение
  probe                                карта страницы одним вызовом
  scroll   to=top|bottom|sel           прокрутить
  shot     path                        скриншот в файл
  sleep    ms                          пауза

Проверки: expect это строка или объект вида {"text": "Спасибо"}.
Если проверка не прошла, шаг помечается ошибкой, а клик повторяется один раз.
"""

from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
import time

from . import client, state

CMD_TIMEOUT = 60


# ---------------------------------------------------------------- транспорт


def js(code: str, tab_id: int | None = None, timeout: float = CMD_TIMEOUT):
    return client.send(
        {"kind": "eval", "code": code, "tabId": tab_id or state.tab()}, timeout
    )


def cdp(method: str, params: dict | None = None, tab_id: int | None = None):
    return client.send(
        {"kind": "cdp", "method": method, "params": params or {}, "tabId": tab_id or state.tab()}
    )


def send(payload: dict, timeout: float = CMD_TIMEOUT):
    return client.send(payload, timeout)


# ---------------------------------------------------------------- самоподъем


def ensure(verbose: bool = True) -> bool:
    """Поднять мост и вкладку-сторож, если их нет. True значит связь есть."""
    if client.alive():
        return True
    if state.off_flag().exists():
        if verbose:
            print(
                json.dumps(
                    {"ok": False, "error": "стоит bridge.off, автозапуск выключен"},
                    ensure_ascii=False,
                )
            )
        return False

    base = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(
        subprocess, "CREATE_NEW_PROCESS_GROUP", 0
    )
    breakaway = base | getattr(subprocess, "CREATE_BREAKAWAY_FROM_JOB", 0)
    cwd = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    # Отрыв от job-объекта запрещен, если процесс запущен под чужой песочницей:
    # Windows отвечает «Отказано в доступе». Тогда поднимаем без отрыва, иначе
    # `mb up` вообще не работает из ограниченных оболочек.
    last = None
    for flags in (breakaway, base):
        try:
            subprocess.Popen(
                [sys.executable, "-m", "mbridge.supervisor"],
                cwd=cwd,
                creationflags=flags,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                close_fds=True,
            )
            last = None
            break
        except OSError as exc:
            last = exc
    if last is not None:
        if verbose:
            print(
                json.dumps(
                    {"ok": False, "error": f"не смог поднять наставника: {last}"},
                    ensure_ascii=False,
                )
            )
        return False

    started = time.time()
    while time.time() - started < 20:
        if client.alive():
            time.sleep(1.5)  # дать вкладке-сторожу подключиться
            if verbose:
                print(json.dumps({"поднял": "мост и вкладку-сторож"}, ensure_ascii=False))
            return True
        time.sleep(0.5)
    return client.alive()


# ---------------------------------------------------------------- помощники JS

LIB = r"""
const BD = (() => {
  const norm = s => (s || '').replace(/\s+/g, ' ').trim().toLowerCase();
  const vis = el => {
    if (!el) return false;
    const r = el.getBoundingClientRect();
    if (r.width < 1 || r.height < 1) return false;
    const st = getComputedStyle(el);
    return st.visibility !== 'hidden' && st.display !== 'none' && st.opacity !== '0';
  };
  const area = el => { const r = el.getBoundingClientRect(); return r.width * r.height; };
  const center = el => {
    const r = el.getBoundingClientRect();
    return {x: Math.round(r.left + r.width / 2), y: Math.round(r.top + r.height / 2),
            w: Math.round(r.width), h: Math.round(r.height)};
  };
  const SEL = 'button,a,[role=button],label,span,div,li,td,th,h1,h2,h3,h4,p,option,summary';
  function byText(txt, opts) {
    opts = opts || {};
    const t = norm(txt);
    let list = [...document.querySelectorAll(opts.sel || SEL)].filter(vis).filter(e => {
      const s = norm(e.innerText || e.textContent);
      return opts.exact ? s === t : s.indexOf(t) >= 0;
    });
    if (opts.exact === false) {
      const exact = list.filter(e => norm(e.innerText) === t);
      if (exact.length) list = exact;
    }
    list.sort((a, b) => area(a) - area(b));
    return list[opts.index || 0] || null;
  }
  function field(spec, index) {
    index = index || 0;
    const t = norm(spec);
    const out = [];
    document.querySelectorAll('label').forEach(l => {
      if (!norm(l.innerText).includes(t)) return;
      const c = l.querySelector('input,textarea,select');
      if (c && vis(c)) out.push(c);
    });
    if (!out.length) {
      document.querySelectorAll('input,textarea,select').forEach(e => {
        if (!vis(e)) return;
        const bag = [e.getAttribute('aria-label'), e.placeholder, e.name, e.id,
                     e.getAttribute('title')].map(norm).join(' | ');
        if (bag.indexOf(t) >= 0) out.push(e);
      });
    }
    if (!out.length) {
      // последняя попытка: поднимаемся от поля и смотрим текст вокруг
      document.querySelectorAll('input,textarea,select').forEach(e => {
        if (!vis(e)) return;
        let n = e.parentElement, hops = 0;
        while (n && hops < 4) {
          if (norm(n.innerText).includes(t)) { out.push(e); return; }
          n = n.parentElement; hops++;
        }
      });
    }
    return out[index] || null;
  }
  function setVal(el, v) {
    const proto = el.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype
                : el.tagName === 'SELECT' ? HTMLSelectElement.prototype
                : HTMLInputElement.prototype;
    const d = Object.getOwnPropertyDescriptor(proto, 'value');
    el.focus();
    if (d && d.set) d.set.call(el, v); else el.value = v;
    el.dispatchEvent(new Event('input', {bubbles: true}));
    el.dispatchEvent(new Event('change', {bubbles: true}));
    el.dispatchEvent(new Event('blur', {bubbles: true}));
    return el.value;
  }
  function cond(spec) {
    if (typeof spec === 'string') spec = {text: spec};
    if (spec.text != null) {
      const t = norm(spec.text);
      const hit = [...document.querySelectorAll(SEL)].some(e => vis(e) && norm(e.innerText).includes(t));
      if (!hit) return false;
    }
    if (spec.gone != null) {
      const t = norm(spec.gone);
      const hit = [...document.querySelectorAll(SEL)].some(e => vis(e) && norm(e.innerText).includes(t));
      if (hit) return false;
    }
    if (spec.sel) {
      const el = document.querySelector(spec.sel);
      if (!el || (spec.visible !== false && !vis(el))) return false;
    }
    if (spec.url) {
      if (location.href.indexOf(spec.url) < 0) return false;
    }
    if (spec.value != null) {
      const el = field(spec.field || '', 0);
      if (!el || el.value !== spec.value) return false;
    }
    return true;
  }
  return {norm, vis, center, byText, field, setVal, cond,
          body: () => (document.body ? document.body.innerText : '')};
})();
"""


def eval_with_lib(expr: str, tab_id: int | None = None):
    """Выполнить выражение с загруженной библиотекой BD."""
    return js("(function(){%s\nreturn (%s);})()" % (LIB, expr), tab_id)


def wait_cond(spec, timeout: float = 15.0, tab_id: int | None = None, poll: float = 0.15):
    """Ждать выполнения условия. Возвращает время ожидания или None."""
    started = time.time()
    spec_js = json.dumps(spec if isinstance(spec, dict) else {"text": spec}, ensure_ascii=False)
    while True:
        try:
            ok = eval_with_lib("BD.cond(%s)" % spec_js, tab_id)
        except Exception:  # noqa: BLE001
            ok = False
        if ok:
            return time.time() - started
        if time.time() - started > timeout:
            return None
        time.sleep(poll)


def click_xy(x: int, y: int, tab_id: int | None = None):
    """Настоящий клик мышью через CDP. Работает там, где JS-клик не проходит."""
    base = {"x": x, "y": y}
    cdp("Input.dispatchMouseEvent", dict(base, type="mouseMoved", buttons=0), tab_id)
    cdp(
        "Input.dispatchMouseEvent",
        dict(base, type="mousePressed", button="left", buttons=1, clickCount=1),
        tab_id,
    )
    cdp(
        "Input.dispatchMouseEvent",
        dict(base, type="mouseReleased", button="left", buttons=0, clickCount=1),
        tab_id,
    )


# ---------------------------------------------------------------- шаги


def do_open(step: dict):
    active = step.get("active", True)
    result = send({"kind": "open", "url": step["url"], "active": active})
    tab_id = result.get("id") if isinstance(result, dict) else None
    if tab_id:
        state.set_tab(tab_id)
        if active:
            try:
                cdp("Page.enable", {}, tab_id)
            except Exception:  # noqa: BLE001
                pass
        started = time.time()
        while time.time() - started < float(step.get("timeout") or 20):
            try:
                ready = js("document.readyState", tab_id, 10)
            except Exception:  # noqa: BLE001
                ready = None
            if ready == "complete":
                break
            time.sleep(0.2)
    return {"tab": tab_id, "url": step["url"]}


def do_click(step: dict, tab_id: int | None):
    selector = step.get("sel")
    if selector:
        point = eval_with_lib(
            "(function(){const e=document.querySelector(%s);"
            "if(!e)return null;e.scrollIntoView({block:'center'});"
            "return BD.vis(e)?BD.center(e):null;})()" % json.dumps(selector),
            tab_id,
        )
    else:
        text = step.get("text")
        if text is None:
            raise RuntimeError("нужен text или sel")
        point = eval_with_lib(
            "(function(){const e=BD.byText(%s,%s);"
            "if(!e)return null;e.scrollIntoView({block:'center'});"
            "return BD.center(e);})()"
            % (
                json.dumps(text, ensure_ascii=False),
                json.dumps(
                    {
                        "exact": step.get("exact"),
                        "index": step.get("index", 0),
                        "sel": step.get("scope"),
                    },
                    ensure_ascii=False,
                ),
            ),
            tab_id,
        )
    if not point:
        raise RuntimeError("не нашел элемент: %s" % (step.get("text") or step.get("sel")))

    time.sleep(float(step.get("settle", 150)) / 1000.0)
    # координаты могли сдвинуться после прокрутки, пересчитываем
    if selector:
        point = eval_with_lib(
            "BD.center(document.querySelector(%s))" % json.dumps(selector), tab_id
        )
    else:
        point = eval_with_lib(
            "(function(){const e=BD.byText(%s,%s);return e?BD.center(e):null;})()"
            % (
                json.dumps(step.get("text"), ensure_ascii=False),
                json.dumps(
                    {
                        "exact": step.get("exact"),
                        "index": step.get("index", 0),
                        "sel": step.get("scope"),
                    },
                    ensure_ascii=False,
                ),
            ),
            tab_id,
        )
    if not point:
        raise RuntimeError("элемент пропал после прокрутки")
    click_xy(point["x"], point["y"], tab_id)
    return {"x": point["x"], "y": point["y"]}


def do_check(step: dict, tab_id: int | None):
    selector = step.get("sel")
    if selector:
        info = eval_with_lib(
            "(function(){const e=document.querySelector(%s);if(!e)return null;"
            "e.scrollIntoView({block:'center'});return {c:BD.center(e),on:e.checked};})()"
            % json.dumps(selector),
            tab_id,
        )
    else:
        info = eval_with_lib(
            "(function(){const e=BD.field(%s,%d);if(!e)return null;"
            "e.scrollIntoView({block:'center'});return {c:BD.center(e),on:e.checked};})()"
            % (
                json.dumps(step.get("text") or step.get("label"), ensure_ascii=False),
                step.get("index", 0),
            ),
            tab_id,
        )
    if not info:
        raise RuntimeError(
            "не нашел переключатель: %s" % (step.get("text") or step.get("label"))
        )
    if not info["on"]:
        click_xy(info["c"]["x"], info["c"]["y"], tab_id)
    return {"checked": True}


def do_fill(step: dict, tab_id: int | None):
    selector = step.get("sel")
    label = step.get("label") or step.get("placeholder")
    if selector:
        find = "document.querySelector(%s)" % json.dumps(selector)
    else:
        if label is None:
            raise RuntimeError("нужен label или sel")
        find = "BD.field(%s,%d)" % (json.dumps(label, ensure_ascii=False), step.get("index", 0))
    value = step.get("value", "")
    code = (
        "(function(){const e=%s;if(!e)return {err:'не нашел поле'};"
        "e.scrollIntoView({block:'center'});e.focus();"
        "if(%s)e.value='';const v=BD.setVal(e,%s);return {value:v};})()"
        % (
            find,
            "true" if step.get("clear", True) else "false",
            json.dumps(value, ensure_ascii=False),
        )
    )
    result = eval_with_lib(code, tab_id)
    if not result or result.get("err"):
        raise RuntimeError("поле не найдено: %s" % (label or selector))
    return result


def do_press(step: dict, tab_id: int | None):
    key = step.get("key", "Enter")
    code = {"Enter": 13, "Tab": 9, "Escape": 27, "ArrowDown": 40, "ArrowUp": 38}.get(key, 0)
    for kind in ("keyDown", "keyUp"):
        cdp(
            "Input.dispatchKeyEvent",
            {
                "type": kind,
                "key": key,
                "windowsVirtualKeyCode": code,
                "nativeVirtualKeyCode": code,
            },
            tab_id,
        )
    return {"key": key}


def do_type(step: dict, tab_id: int | None):
    text = step.get("value", "")
    cdp("Input.insertText", {"text": text}, tab_id)
    return {"typed": len(text)}


def ensure_sel(selector: str, tab_id: int | None, timeout: float = 5.0):
    """Дождаться появления элемента. Не мешает, если его нет: просто выходим."""
    if not selector or selector == "body":
        return
    started = time.time()
    while time.time() - started < timeout:
        try:
            if eval_with_lib("!!document.querySelector(%s)" % json.dumps(selector), tab_id):
                return
        except Exception:  # noqa: BLE001
            pass
        time.sleep(0.12)


PROBE = r"""
(() => {
  const norm = s => (s || '').replace(/\s+/g, ' ').trim();
  const vis = el => {
    if (!el) return false;
    const r = el.getBoundingClientRect();
    if (r.width < 1 || r.height < 1) return false;
    const st = getComputedStyle(el);
    return st.visibility !== 'hidden' && st.display !== 'none' && st.opacity !== '0';
  };
  const cut = (s, n) => { s = norm(s); return s.length > n ? s.slice(0, n) + '...' : s; };
  const fields = [];
  document.querySelectorAll('input,textarea,select').forEach(e => {
    if (!vis(e)) return;
    const t = (e.type || e.tagName).toLowerCase();
    if (t === 'hidden') return;
    let name = '';
    if (e.id) {
      const l = document.querySelector('label[for="' + e.id + '"]');
      if (l) name = l.innerText;
    }
    if (!name) { const l = e.closest('label'); if (l) name = l.innerText; }
    if (!name) name = e.getAttribute('aria-label') || e.placeholder || e.name || e.id || '';
    fields.push({t, name: cut(name, 60), val: cut(e.value, 40), req: !!e.required});
  });
  const click = [];
  const seen = new Set();
  document.querySelectorAll('button,[role=button],a,input[type=submit],summary').forEach(e => {
    if (!vis(e)) return;
    const s = cut(e.innerText || e.value, 50);
    if (!s || seen.has(s)) return;
    seen.add(s);
    click.push(s);
  });
  const heads = [...document.querySelectorAll('h1,h2')].filter(vis).map(e => cut(e.innerText, 70));
  const alerts = [...document.querySelectorAll('[role=alert],[class*=error],[class*=warning],[class*=success]')]
    .filter(vis).map(e => cut(e.innerText, 80)).filter(Boolean).slice(0, 6);
  return {url: location.href, title: document.title, heads: heads.slice(0, 12),
          fields: fields.slice(0, 25), click: click.slice(0, 30), alerts};
})()
"""


def do_probe(tab_id: int | None):
    """Карта страницы одним вызовом: адрес, заголовки, поля, кнопки, сообщения."""
    return eval_with_lib(PROBE, tab_id)


def run_step(step: dict, tab_id: int | None):
    op = step.get("op")
    if op == "open":
        return do_open(step)
    if op == "use":
        return {"tab": state.set_tab(int(step["tab"]))}
    if op == "close":
        # вкладку-сторож закрывать нельзя: на ней держится связь с расширением
        try:
            tabs = send({"kind": "tabs"}, 20) or []
            url = next(
                (t.get("url") or "" for t in tabs if t.get("id") == int(step["tab"])), ""
            )
            if client.host() + ":" + str(client.port()) in url:
                raise RuntimeError("это вкладка-сторож, ее закрывать нельзя")
        except RuntimeError:
            raise
        except Exception:  # noqa: BLE001
            pass
        send({"kind": "close", "tabId": int(step["tab"])})
        return {"closed": step["tab"]}
    if op == "goto":
        send({"kind": "open", "url": step["url"], "active": True})
        return do_open({"url": step["url"], "timeout": step.get("timeout")})
    if op == "reload":
        cdp("Page.reload", {}, tab_id)
        time.sleep(0.8)
        return {"reloaded": True}
    if op == "back":
        cdp("Runtime.evaluate", {"expression": "history.back()"}, tab_id)
        time.sleep(0.6)
        return {"back": True}
    if op == "wait":
        spec = {
            k: step[k] for k in ("text", "sel", "url", "gone", "value", "field") if k in step
        }
        got = wait_cond(spec, float(step.get("timeout", 15)), tab_id)
        if got is None:
            raise RuntimeError("не дождался: %s" % json.dumps(spec, ensure_ascii=False))
        return {"waited": round(got, 2)}
    if op == "click":
        return do_click(step, tab_id)
    if op == "check":
        return do_check(step, tab_id)
    if op == "fill":
        return do_fill(step, tab_id)
    if op == "select":
        result = eval_with_lib(
            "(function(){const e=BD.field(%s,%d)||document.querySelector(%s);"
            "if(!e)return {err:'нет select'};const v=BD.setVal(e,%s);return {value:v};})()"
            % (
                json.dumps(step.get("label", ""), ensure_ascii=False),
                step.get("index", 0),
                json.dumps(step.get("sel", "select")),
                json.dumps(step.get("value", ""), ensure_ascii=False),
            ),
            tab_id,
        )
        if result.get("err"):
            raise RuntimeError(result["err"])
        return result
    if op == "type":
        return do_type(step, tab_id)
    if op == "press":
        return do_press(step, tab_id)
    if op == "text":
        selector = step.get("sel", "body")
        ensure_sel(selector, tab_id, float(step.get("timeout", 5)))
        return {
            "text": eval_with_lib(
                "(document.querySelector(%s)||{}).innerText || ''" % json.dumps(selector), tab_id
            )
        }
    if op == "attr":
        ensure_sel(step["sel"], tab_id, float(step.get("timeout", 5)))
        return {
            "value": eval_with_lib(
                "(function(){const e=document.querySelector(%s);"
                "return e?e.getAttribute(%s):null;})()"
                % (json.dumps(step["sel"]), json.dumps(step["name"])),
                tab_id,
            )
        }
    if op == "extract":
        ensure_sel(step["sel"], tab_id, float(step.get("timeout", 5)))
        return {
            "items": eval_with_lib(
                "[...document.querySelectorAll(%s)]"
                ".map(e=>e.innerText.trim()).filter(Boolean)" % json.dumps(step["sel"]),
                tab_id,
            )
        }
    if op == "js":
        return {"value": eval_with_lib(step["code"], tab_id)}
    if op == "scroll":
        to = step.get("to", "bottom")
        if to == "top":
            eval_with_lib("(window.scrollTo(0,0),true)", tab_id)
        elif to == "bottom":
            eval_with_lib("(window.scrollTo(0,document.body.scrollHeight),true)", tab_id)
        else:
            eval_with_lib(
                "(function(){const e=document.querySelector(%s);"
                "if(e)e.scrollIntoView({block:'center'});return true;})()"
                % json.dumps(step.get("sel", to)),
                tab_id,
            )
        return {"scrolled": to}
    if op == "shot":
        result = cdp("Page.captureScreenshot", {"format": "png"}, tab_id)
        path = step.get("path", "shot.png")
        with open(path, "wb") as fh:
            fh.write(base64.b64decode(result.get("data") or ""))
        return {"path": path, "bytes": os.path.getsize(path)}
    if op == "probe":
        return {"map": do_probe(tab_id)}
    if op == "sleep":
        ms = int(step.get("ms", 500))
        time.sleep(ms / 1000.0)
        return {"slept": ms}
    raise RuntimeError("неизвестный шаг: %s" % op)


def run_steps(steps: list, verbose: bool = True) -> list:
    results = []
    started_all = time.time()
    for index, step in enumerate(steps):
        started = time.time()
        op = step.get("op")
        record = {"i": index, "op": op}
        if step.get("as"):
            record["as"] = step["as"]
        try:
            info = run_step(step, state.tab())
            expect = step.get("expect")
            if expect:
                got = wait_cond(expect, float(step.get("expect_timeout", 8)), state.tab())
                if got is None:
                    if op == "click":
                        # один повтор: часто мешает анимация или поздний рендер
                        run_step(step, state.tab())
                        got = wait_cond(expect, float(step.get("expect_timeout", 8)), state.tab())
                    if got is None:
                        raise RuntimeError(
                            "не дождался после шага: %s" % json.dumps(expect, ensure_ascii=False)
                        )
                    info["retried"] = True
                info["expect"] = round(got, 2) if got else 0
            record.update(ok=True, info=info)
        except Exception as exc:  # noqa: BLE001
            record.update(ok=False, err=str(exc))
            results.append(record)
            if verbose:
                print(json.dumps(record, ensure_ascii=False))
            print(
                json.dumps(
                    {"итог": "остановлен на шаге %d" % index,
                     "секунд": round(time.time() - started_all, 2)},
                    ensure_ascii=False,
                )
            )
            return results
        record["ms"] = int((time.time() - started) * 1000)
        results.append(record)
        if verbose:
            print(json.dumps(record, ensure_ascii=False))
    print(
        json.dumps(
            {"итог": "готово", "шагов": len(steps), "секунд": round(time.time() - started_all, 2)},
            ensure_ascii=False,
        )
    )
    return results
