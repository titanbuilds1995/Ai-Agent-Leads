/* Titan SMS opt-in pop-up for the company website.
   Paste ONE line on titanrealtyinvestments.com (any page, before </body>):
     <script src="https://YOUR-VERCEL-DOMAIN/sms-consent-embed.js" data-form="https://YOUR-VERCEL-DOMAIN/sms-consent" defer></script>
   It adds a small "OK to text me" button bottom-right that opens the opt-in form in a pop-up (an iframe of the Vercel page).
   Optional attributes: data-label="Text me instead" (button text), data-auto="1" (open the pop-up on page load),
   data-hide-button="1" (no button; open it yourself with window.TitanSmsConsent.open()).
   Links on the site can also open it: <a href="#" onclick="TitanSmsConsent.open();return false;">OK to text me</a>. */
(() => {
  const me = document.currentScript;
  const formUrl = (me && me.dataset.form) || (me && me.src.replace(/sms-consent-embed\.js.*$/, "sms-consent")) || "/sms-consent";
  const label = (me && me.dataset.label) || "OK to text me";
  const css = `
    .tsc-btn{position:fixed;right:18px;bottom:18px;z-index:99998;background:#0C1C35;color:#fff;border:2px solid #B99455;border-radius:999px;padding:11px 18px;font:600 13px/1 system-ui,sans-serif;letter-spacing:.04em;cursor:pointer;box-shadow:0 10px 30px -12px rgba(0,0,0,.5)}
    .tsc-btn:hover{background:#132844}
    .tsc-wrap{position:fixed;inset:0;z-index:99999;background:rgba(12,28,53,.55);display:flex;align-items:center;justify-content:center;padding:16px}
    .tsc-box{position:relative;width:min(600px,100%);height:min(720px,100%);background:#F4F2EC;border-radius:12px;overflow:hidden;box-shadow:0 30px 80px -20px rgba(0,0,0,.6)}
    .tsc-box iframe{width:100%;height:100%;border:0;display:block}
    .tsc-x{position:absolute;top:8px;right:8px;width:34px;height:34px;border:0;border-radius:50%;background:#fff;color:#0C1C35;font:20px/34px system-ui;cursor:pointer}
  `;
  const style = document.createElement("style"); style.textContent = css; document.head.appendChild(style);
  let wrap = null;
  function open() {
    if (wrap) return;
    wrap = document.createElement("div"); wrap.className = "tsc-wrap";
    wrap.innerHTML = `<div class="tsc-box"><button class="tsc-x" aria-label="Close">×</button><iframe title="Text message opt-in" src="${formUrl}"></iframe></div>`;
    wrap.addEventListener("click", (e) => { if (e.target === wrap || e.target.classList.contains("tsc-x")) close(); });
    document.body.appendChild(wrap);
    document.addEventListener("keydown", esc);
  }
  function esc(e) { if (e.key === "Escape") close(); }
  function close() { if (!wrap) return; wrap.remove(); wrap = null; document.removeEventListener("keydown", esc); }
  window.TitanSmsConsent = { open, close };
  if (!(me && me.dataset.hideButton)) {
    const b = document.createElement("button"); b.className = "tsc-btn"; b.type = "button"; b.textContent = label; b.addEventListener("click", open);
    document.body.appendChild(b);
  }
  if (me && me.dataset.auto) open();
})();
