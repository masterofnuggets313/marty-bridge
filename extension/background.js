// Мост между локальным сервером и браузером.
// Работает через chrome.debugger, поэтому порт отладки не нужен вообще.
//
// Опрос моста живёт в сторожевой вкладке (keepalive.js): служебный воркер
// расширения Chrome усыпляет через 30 секунд, вкладку не усыпляет.
// Воркер просыпается на сообщение от вкладки, выполняет команду и отвечает.

const BASE = "http://127.0.0.1:8765";
const BRIDGE_URL = BASE + "/";
const attached = new Set();

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function withTimeout(promise, ms, label) {
  return Promise.race([
    promise,
    new Promise((_, rej) => setTimeout(() => rej(new Error("таймаут: " + label)), ms)),
  ]);
}

function errText(e) {
  return String(e && e.message ? e.message : e);
}

async function activeTabId() {
  const [tab] = await chrome.tabs.query({ active: true, lastFocusedWindow: true });
  if (!tab) throw new Error("нет активной вкладки");
  return tab.id;
}

function waitForLoad(tabId, ms) {
  return new Promise((resolve) => {
    let done = false;
    const finish = () => {
      if (done) return;
      done = true;
      chrome.tabs.onUpdated.removeListener(onUpd);
      resolve();
    };
    const onUpd = (id, info) => {
      if (id === tabId && info.status === "complete") finish();
    };
    chrome.tabs.onUpdated.addListener(onUpd);
    setTimeout(finish, ms);
  });
}

async function ensureAttach(tabId) {
  if (attached.has(tabId)) return;

  // Chrome выгружает фоновые вкладки ради памяти. У выгруженной вкладки нет
  // страницы, поэтому отладчик к ней не подключается и команда висит.
  // Будим ее и заодно запрещаем выгружать дальше.
  try {
    const tab = await chrome.tabs.get(tabId);
    if (tab.discarded) {
      const loaded = waitForLoad(tabId, 8000);
      await chrome.tabs.reload(tabId);
      await loaded;
    }
    if (tab.autoDiscardable) {
      chrome.tabs.update(tabId, { autoDiscardable: false }).catch(() => {});
    }
  } catch (e) {
    throw new Error("нет такой вкладки: " + tabId);
  }

  try {
    await withTimeout(chrome.debugger.attach({ tabId }, "1.3"), 8000, "attach");
  } catch (e) {
    const msg = errText(e);
    if (/Already attached/i.test(msg)) {
      // скорее всего это наша же сессия, оставшаяся от прошлого запуска воркера
      try {
        await withTimeout(chrome.debugger.detach({ tabId }), 5000, "detach");
        await withTimeout(chrome.debugger.attach({ tabId }, "1.3"), 8000, "attach2");
      } catch (e2) {
        throw new Error("вкладка занята другим отладчиком: " + tabId + " (" + errText(e2) + ")");
      }
    } else {
      throw new Error("attach: " + msg);
    }
  }
  attached.add(tabId);
}

chrome.debugger.onDetach.addListener((source) => {
  if (source && source.tabId) attached.delete(source.tabId);
});

// Вкладка-сторож: без неё команды никто не заберёт с моста.
//
// Правила, из-за которых раньше плодились дубли:
// 1. Сравниваем адрес без хвостового слеша и без параметров: 8765 и 8765/
//    это одна и та же вкладка.
// 2. Если браузер стартовал в фоновом режиме и окон еще нет, вкладку не
//    создаем. Иначе на входе в систему вылезает лишнее окно.
// 3. Найденную вкладку не пересоздаем, а будим: Яндекс и Chrome выгружают
//    фоновые вкладки, и у выгруженной страницы опрос не идет.
// 4. Лишние незакрепленные дубли закрываем, закрепленную не трогаем.
const BRIDGE_KEY = (() => {
  try {
    const u = new URL(BRIDGE_URL);
    return u.origin + u.pathname.replace(/\/+$/, "");
  } catch (e) {
    return BRIDGE_URL;
  }
})();

function sameBridge(url) {
  if (!url) return false;
  try {
    const u = new URL(url);
    return u.origin + u.pathname.replace(/\/+$/, "") === BRIDGE_KEY;
  } catch (e) {
    return false;
  }
}

let ensuring = null;

async function ensureBridgeTab() {
  if (ensuring) return ensuring;
  ensuring = (async () => {
    try {
      const windows = await chrome.windows.getAll();
      // фоновый старт браузера: окна нет, создавать его мы не имеем права
      if (!windows.length) return null;

      const all = await chrome.tabs.query({});
      const mine = all.filter((t) => sameBridge(t.url) || sameBridge(t.pendingUrl));

      if (mine.length) {
        const keep = mine.find((t) => t.pinned) || mine[0];
        try {
          if (keep.discarded) await chrome.tabs.reload(keep.id);
          chrome.tabs.update(keep.id, { autoDiscardable: false }).catch(() => {});
        } catch (e) { /* вкладку закрыли между запросами */ }

        for (const t of mine) {
          if (t.id === keep.id || t.pinned || t.active) continue;
          chrome.tabs.remove(t.id).catch(() => {});
        }
        return keep.id;
      }

      const t = await chrome.tabs.create({
        url: BRIDGE_URL,
        active: false,
        pinned: true,
      });
      chrome.tabs.update(t.id, { autoDiscardable: false }).catch(() => {});
      return t.id;
    } catch (e) {
      return null;
    } finally {
      ensuring = null;
    }
  })();
  return ensuring;
}

async function run(cmd) {
  const kind = cmd.kind;

  if (kind === "ping") {
    return { pong: true, version: chrome.runtime.getManifest().version };
  }

  // Перечитать файлы расширения с диска. Ответ уходит до перезагрузки,
  // иначе мост не узнает, что команда дошла.
  if (kind === "reload") {
    setTimeout(() => chrome.runtime.reload(), 150);
    return { reloading: true };
  }

  if (kind === "diag") {
    const tabs = await chrome.tabs.query({});
    const out = {
      tabs: tabs.length,
      bridgeTab: null,
      attach: null,
      targets: null,
    };
    const mine = tabs.find((t) => (t.url || "").startsWith(BASE));
    out.bridgeTab = mine ? "есть, вкладка " + mine.id : "нет";
    try {
      const r = await withTimeout(chrome.debugger.getTargets(), 5000, "getTargets");
      out.targets = r.length;
    } catch (e) {
      out.targets = "ошибка: " + errText(e);
    }
    const tab = tabs.find((t) => /^https?:/.test(t.url || "") && !(t.url || "").startsWith(BASE));
    if (tab) {
      if (attached.has(tab.id)) {
        out.attach = "уже подключены, вкладка " + tab.id;
      } else {
        try {
          await withTimeout(chrome.debugger.attach({ tabId: tab.id }, "1.3"), 8000, "attach");
          out.attach = "ок, вкладка " + tab.id;
          try {
            await withTimeout(chrome.debugger.detach({ tabId: tab.id }), 5000, "detach");
          } catch (e) { /* уже отцеплено */ }
        } catch (e) {
          out.attach = "ошибка: " + errText(e);
        }
      }
    }
    return out;
  }

  if (kind === "tabs") {
    const tabs = await chrome.tabs.query({});
    return tabs.map((t) => ({
      id: t.id, title: t.title, url: t.url,
      active: t.active, windowId: t.windowId,
    }));
  }

  if (kind === "open") {
    const t = await chrome.tabs.create({ url: cmd.url, active: cmd.active !== false });
    chrome.tabs.update(t.id, { autoDiscardable: false }).catch(() => {});
    return { id: t.id, url: t.url };
  }

  if (kind === "close") {
    await chrome.tabs.remove(cmd.tabId);
    return { closed: cmd.tabId };
  }

  if (kind === "activate") {
    const t = await chrome.tabs.update(cmd.tabId, { active: true });
    if (cmd.windowId) await chrome.windows.update(cmd.windowId, { focused: true });
    return { id: t.id, url: t.url };
  }

  if (kind === "eval" || kind === "cdp") {
    const tabId = cmd.tabId || (await activeTabId());
    await ensureAttach(tabId);

    if (kind === "eval") {
      const doEval = () => withTimeout(
        chrome.debugger.sendCommand({ tabId }, "Runtime.evaluate", {
          expression: cmd.code,
          returnByValue: true,
          awaitPromise: true,
          userGesture: true,
        }),
        15000, "Runtime.evaluate");

      let r;
      try {
        r = await doEval();
      } catch (e) {
        if (!/таймаут/.test(errText(e))) throw e;
        // страница могла уснуть: перезагружаем и пробуем еще раз
        attached.delete(tabId);
        try { await withTimeout(chrome.debugger.detach({ tabId }), 5000, "detach"); }
        catch (e2) { /* уже отцеплено */ }
        await chrome.tabs.reload(tabId);
        await sleep(1500);
        await ensureAttach(tabId);
        r = await doEval();
      }

      if (r.exceptionDetails) {
        const d = r.exceptionDetails;
        throw new Error((d.exception && d.exception.description) || d.text || "ошибка JS");
      }
      return r.result ? r.result.value : null;
    }

    return await withTimeout(
      chrome.debugger.sendCommand({ tabId }, cmd.method, cmd.params || {}),
      15000, cmd.method);
  }

  throw new Error("неизвестная команда: " + kind);
}

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (!msg || msg.type !== "bu-cmd" || !msg.cmd) return false;
  withTimeout(run(msg.cmd), 25000, "команда")
    .then((data) => sendResponse({ ok: true, data }))
    .catch((e) => sendResponse({ ok: false, error: errText(e) }));
  return true; // ответ будет асинхронным
});

function boot() {
  chrome.alarms.create("keep", { periodInMinutes: 1 });
  // сессию браузер восстанавливает уже после старта воркера, поэтому сразу
  // вкладку не создаем: иначе на входе в систему появляется лишняя
  setTimeout(() => ensureBridgeTab(), 4000);
}

chrome.runtime.onInstalled.addListener(boot);
chrome.runtime.onStartup.addListener(boot);
chrome.alarms.onAlarm.addListener(() => ensureBridgeTab());
// окно появилось, в том числе после фонового старта: самое время навести порядок
chrome.windows.onCreated.addListener(() => setTimeout(() => ensureBridgeTab(), 2500));
boot();
