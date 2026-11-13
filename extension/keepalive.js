// Живёт во вкладке моста. Вкладку не усыпляют, поэтому опрос идёт постоянно,
// а служебный воркер расширения просыпается только на время команды.

const BASE = "http://127.0.0.1:8765";

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function status(text, ok) {
  const el = document.getElementById("st");
  if (el) {
    el.textContent = text;
    el.style.color = ok ? "#4ade80" : "#f87171";
  }
}

async function loop() {
  let errors = 0;
  for (;;) {
    let cmd = null;
    try {
      const r = await fetch(BASE + "/poll?wait=25", { cache: "no-store" });
      cmd = await r.json();
      errors = 0;
      status("на связи", true);
      // Метка для страницы: скрипт расширения жив. Страница читает ее из DOM,
      // потому что контексты у нас разные, а DOM общий.
      document.documentElement.dataset.bdAlive = "1";
    } catch (e) {
      errors += 1;
      status("мост не отвечает, попыток: " + errors, false);
      delete document.documentElement.dataset.bdAlive;
      await sleep(errors > 5 ? 5000 : 1000);
      continue;
    }
    if (!cmd || !cmd.id) continue;

    let payload;
    try {
      const res = await chrome.runtime.sendMessage({ type: "bu-cmd", cmd });
      payload = res && typeof res === "object"
        ? res
        : { ok: false, error: "воркер вернул пустой ответ" };
    } catch (e) {
      const msg = String(e && e.message ? e.message : e);
      payload = { ok: false, error: "sendMessage: " + msg };
      // расширение перезагрузили, и наш контекст осиротел: перезагружаем страницу,
      // чтобы Chrome внедрил свежий скрипт
      if (/context invalidated/i.test(msg)) {
        try {
          await fetch(BASE + "/result", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(Object.assign({ id: cmd.id }, payload)),
          });
        } catch (e2) { /* уже неважно */ }
        location.reload();
        return;
      }
    }

    try {
      await fetch(BASE + "/result", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(Object.assign({ id: cmd.id }, payload)),
      });
    } catch (e) { /* мост закрылся, не страшно */ }
  }
}

loop();
