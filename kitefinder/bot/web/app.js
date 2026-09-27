// Kite Finder Mini App: talks to the phone's JSON API with Telegram's signed initData.
const tg = window.Telegram && window.Telegram.WebApp;
if (tg) { tg.ready(); tg.expand(); }
const initData = tg ? tg.initData : "";
const $ = (sel) => document.querySelector(sel);
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

async function call(path, body) {
  const opts = { headers: { "X-Telegram-Init-Data": initData } };
  if (body !== undefined) {
    opts.method = "POST";
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(body);
  }
  const res = await fetch(path, opts);
  const data = await res.json();
  if (!res.ok) throw new Error(data.error || res.statusText);
  return data;
}

function status(text) { $("#status").textContent = text || ""; }

function card(l) {
  const photos = l.photos.map((u) => `<img src="${esc(u)}" loading="lazy" alt="">`).join("");
  const fav = l.mark === "favorite";
  return `<article class="card" dir="auto">
    ${photos ? `<div class="photos">${photos}</div>` : ""}
    <h4>${esc(l.title)}</h4>
    <p>💰 ${esc(l.price)}</p>
    <p>📍 ${esc(l.location)}</p>
    <p>📝 ${esc(l.description)}</p>
    <p>🔍 ${esc(l.condition)}</p>
    <p class="hint">${esc(l.source)} · ${esc(l.why)}</p>
    <div class="actions">
      <button data-mark="${fav ? "clear" : "favorite"}" data-id="${l.id}">${fav ? "⭐ Favorite" : "✅ Favorite"}</button>
      <button data-mark="dismissed" data-id="${l.id}">❌ Dismiss</button>
      ${l.url ? `<button data-open="${esc(l.url)}">🔗 Open</button>` : ""}
    </div></article>`;
}

function render(s) {
  $("#cards").innerHTML = s.listings.length ? s.listings.map(card).join("")
    : `<p class="hint">No matching listings yet. The phone keeps searching.</p>`;
  $("#recs").innerHTML = s.recommendations.map((r) =>
    `<pre class="box">${esc(r.text)}${r.id === s.active_id ? "\n\n✔ searches use this set" : ""}</pre>`).join("");
  $("#profile-text").textContent = s.profile_text || "No profile yet — fill in the form.";
  $("#owned").innerHTML = s.owned.map((o) => `<li>${esc(o)}</li>`).join("") || "<li>none</li>";
  renderSpots(s);
}

// The spot list: one group per region, a tick box per spot (your current spots ticked).
function renderSpots(s) {
  const mine = new Set((s.profile && s.profile.spots) || []);
  $("#spot-list").innerHTML = "<legend>Where you ride — tick as many spots as you like</legend>" +
    (s.spot_catalog || []).map((r) => `<details${r.spots.some((n) => mine.has(n)) ? " open" : ""}>
      <summary>${esc(r.label)}</summary>
      ${r.spots.map((n) => `<label class="tick"><input type="checkbox" name="spot" value="${esc(n)}"${mine.has(n) ? " checked" : ""}> ${esc(n)}</label>`).join("")}
    </details>`).join("");
}

async function refresh() {
  try { render(await call("/api/state")); status(""); } catch (e) { status("⚠ " + e.message); }
}

document.addEventListener("click", async (ev) => {
  const t = ev.target.closest("button");
  if (!t) return;
  if (t.dataset.tab) {
    document.querySelectorAll(".tabs button, .tab").forEach((x) => x.classList.remove("active"));
    t.classList.add("active");
    $("#" + t.dataset.tab).classList.add("active");
  } else if (t.dataset.mark) {
    render(await call("/api/mark", { listing_id: Number(t.dataset.id), status: t.dataset.mark }));
  } else if (t.dataset.open) {
    tg ? tg.openLink(t.dataset.open) : window.open(t.dataset.open);
  } else if (t.id === "assemble") {
    status("Building…");
    try {
      const r = await call("/api/assemble?brands=" + $("#brands").value);
      $("#assembled").hidden = false;
      $("#assembled").textContent = r.text;
      status("");
    } catch (e) { status("⚠ " + e.message); }
  } else if (t.id === "best" || t.id === "all") {
    try {
      const r = await call("/api/recommend", t.id === "best" ? { under: $("#under").value } : {});
      render(r.state);
      $("#recs").insertAdjacentHTML("afterbegin", `<pre class="box">${esc(r.text)}</pre>`);
    } catch (e) { status("⚠ " + e.message); }
  }
});

$("#profile-form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const form = new FormData(ev.target);
  const body = Object.fromEntries([...form.entries()].filter(([k]) => k !== "spot"));
  const ticked = form.getAll("spot");
  const typed = (body.areas || "").trim();
  if (ticked.length || typed) body.areas = [...ticked, typed].filter(Boolean).join(", ");
  try { render((await call("/api/profile", body)).state); status("✔ Saved"); }
  catch (e) { status("⚠ " + e.message); }
});

refresh();
