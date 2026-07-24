"""Small dependency-free usage and billing page."""

from __future__ import annotations

from fastapi.responses import HTMLResponse

PAGE = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>CleanPDF usage</title>
  <style>
    :root { color-scheme: dark; font-family: Inter, system-ui, sans-serif; }
    body { margin: 0; background: #07111f; color: #e8f1ff; }
    main { max-width: 760px; margin: 5rem auto; padding: 0 1.25rem; }
    section { background: #0f2035; border: 1px solid #23415f; border-radius: 16px; padding: 1.25rem; }
    input, button { padding: .8rem; border-radius: 8px; border: 1px solid #315778; }
    input { width: min(480px, 90%); background: #081728; color: white; }
    button { background: #30d6c7; color: #04201e; font-weight: 700; cursor: pointer; }
    .plans { display: flex; flex-wrap: wrap; gap: .6rem; margin-top: 1rem; }
    pre { white-space: pre-wrap; background: #081728; padding: 1rem; border-radius: 10px; min-height: 5rem; }
    small { color: #9fb5c9; }
  </style>
</head>
<body>
<main>
  <h1>CleanPDF usage</h1>
  <p>Check credits, upgrade, buy an overage pack, or open the customer portal.</p>
  <section>
    <label for="key">API key</label><br><br>
    <input id="key" type="password" autocomplete="off" placeholder="cpdf_...">
    <button id="load">Load usage</button>
    <div class="plans">
      <button data-purchase="starter">Starter $12</button>
      <button data-purchase="pro">Pro $39</button>
      <button data-purchase="overage_500">500 credits $4</button>
      <button id="portal">Billing portal</button>
    </div>
    <p><small>The key stays in this browser tab and is never stored.</small></p>
    <pre id="result">Enter your API key to begin.</pre>
  </section>
</main>
<script>
const key = () => document.querySelector("#key").value.trim();
const output = document.querySelector("#result");
async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: {Authorization: `Bearer ${key()}`, "Content-Type": "application/json"}
  });
  const body = await response.json();
  if (!response.ok) throw new Error(body.detail || `Request failed: ${response.status}`);
  return body;
}
document.querySelector("#load").onclick = async () => {
  try { output.textContent = JSON.stringify(await api("/v1/usage"), null, 2); }
  catch (error) { output.textContent = error.message; }
};
for (const button of document.querySelectorAll("[data-purchase]")) {
  button.onclick = async () => {
    try {
      const body = await api("/v1/billing/checkout", {
        method: "POST",
        body: JSON.stringify({purchase: button.dataset.purchase})
      });
      window.location.assign(body.url);
    } catch (error) { output.textContent = error.message; }
  };
}
document.querySelector("#portal").onclick = async () => {
  try {
    const body = await api("/v1/billing/portal", {method: "POST"});
    window.location.assign(body.url);
  } catch (error) { output.textContent = error.message; }
};
</script>
</body>
</html>"""


def response() -> HTMLResponse:
    return HTMLResponse(
        PAGE,
        headers={
            "Cache-Control": "no-store",
            "Content-Security-Policy": (
                "default-src 'self'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; "
                "connect-src 'self'; frame-ancestors 'none'"
            ),
            "X-Content-Type-Options": "nosniff",
        },
    )
