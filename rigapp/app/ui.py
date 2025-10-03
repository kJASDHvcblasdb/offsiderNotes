from __future__ import annotations

from fastapi.responses import HTMLResponse

TOAST_SNIPPET = """
<script>
(function(){
  // avoid duplicate listeners across navigations in some browsers
  if (window.__pwa_toast_attached) return;
  window.__pwa_toast_attached = true;

  function ensureHost(){
    if (document.getElementById('toast-host')) return;
    const host = document.createElement('div');
    host.id = 'toast-host';
    host.style.position = 'fixed';
    host.style.bottom = '14px';
    host.style.left = '50%';
    host.style.transform = 'translateX(-50%)';
    host.style.maxWidth = '80vw';
    host.style.zIndex = '9999';
    document.body.appendChild(host);
  }

  function toast(msg, timeoutMs){
    ensureHost();
    const el = document.createElement('div');
    el.className = 'toast';
    el.textContent = msg;
    el.style.background = 'rgba(20,20,20,.95)';
    el.style.color = '#fff';
    el.style.padding = '.55rem .8rem';
    el.style.borderRadius = '8px';
    el.style.marginTop = '.4rem';
    el.style.boxShadow = '0 4px 14px rgba(0,0,0,.25)';
    el.style.fontSize = '0.95rem';
    el.style.maxWidth = '100%';
    document.getElementById('toast-host').appendChild(el);
    setTimeout(()=>{ el.style.opacity='0'; el.style.transition='opacity .25s'; }, timeoutMs||2200);
    setTimeout(()=>{ el.remove(); }, (timeoutMs||2200)+300);
  }

  // SW → page messages
  navigator.serviceWorker && navigator.serviceWorker.addEventListener('message', (ev)=>{
    const {type, detail} = ev.data || {};
    if (!type) return;
    if (type === 'queue:enqueued') toast('Saved offline — will sync when online.');
    if (type === 'queue:sync-start') toast('Syncing…');
    if (type === 'queue:sync-complete') toast('Sync complete.');
    if (type === 'queue:conflict') toast('Sync conflict — check Audit/section.');
    if (type === 'bulk-cache:start') toast('Caching for offline…');
    if (type === 'bulk-cache:done') toast('Offline cache complete.');
    if (type === 'bulk-cache:error') toast('Offline cache error.');
  });
})();
</script>
"""

def wrap_page(
    *,
    title: str,
    body_html: str,
    actor: str | None = None,
    rig_title: str | None = None,
) -> HTMLResponse:
    who = []
    if rig_title:
        who.append(f"Rig: <strong>{rig_title}</strong>")
    if actor:
        who.append(f"Crew: <strong>{actor}</strong>")
    who_html = f"<p class='muted'>{' · '.join(who)}</p>" if who else ""

    html = f"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <link rel="stylesheet" href="/static/style.css">
    <title>{title}</title>
  </head>
  <body class="container">
    <h1>{title}</h1>
    {who_html}

    {body_html}

    <footer class="footer">
      <a class="btn" href="/">⬅ Back to Dashboard</a>
      <a class="btn" href="/audit">Audit</a>
    </footer>

    {TOAST_SNIPPET}
  </body>
</html>
"""
    return HTMLResponse(html)

# --- Back-compat shim ---------------------------------------------------------
def page_auto(content_html: str, *, title: str | None = None, actor: str | None = None) -> HTMLResponse:
    return wrap_page(title=title or "Rig App", body_html=content_html, actor=actor)
