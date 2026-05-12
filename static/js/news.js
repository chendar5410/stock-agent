/* Morning Brief — pre-market news slide-deck frontend. */
(() => {
  "use strict";

  const $ = (id) => document.getElementById(id);

  // ── state ──────────────────────────────────────────────────────────────
  let currentDeck = null;
  let deckInstance = null;
  let watchlistSet = new Set();

  // ── helpers ────────────────────────────────────────────────────────────
  const todayISO = () => new Date().toISOString().slice(0, 10);

  const esc = (s) =>
    String(s ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");

  const status = (el, msg, kind = "info") => {
    const node = $(el);
    if (!node) return;
    node.textContent = msg || "";
    node.dataset.kind = kind;
    node.style.color =
      kind === "error" ? "var(--red)" :
      kind === "ok"    ? "var(--green)" :
                         "var(--text-muted)";
  };

  const tickerHTML = (t) => {
    if (!t) return "";
    const up = String(t).toUpperCase();
    const cls = watchlistSet.has(up) ? "ticker wl-hit" : "ticker";
    return `<span class="${cls}">${esc(up)}</span>`;
  };

  const changeHTML = (direction, change) => {
    if (!change && !direction) return "";
    const cls = direction === "down" ? "change-down" : "change-up";
    const arrow = direction === "down" ? "▼" : "▲";
    return `<span class="${cls}">${arrow} ${esc(change || "")}</span>`;
  };

  // ── nav tabs ───────────────────────────────────────────────────────────
  document.querySelectorAll("nav button[data-panel]").forEach((btn) => {
    btn.addEventListener("click", () => {
      document.querySelectorAll("nav button[data-panel]").forEach((b) =>
        b.classList.remove("active"),
      );
      btn.classList.add("active");
      document.querySelectorAll(".panel").forEach((p) =>
        p.classList.remove("active"),
      );
      $(btn.dataset.panel)?.classList.add("active");
      if (btn.dataset.panel === "panel-archive") loadArchive();
    });
  });

  // ── watchlist (for highlighting tickers) ───────────────────────────────
  async function fetchWatchlist() {
    try {
      const r = await fetch("/api/watchlist");
      if (!r.ok) return;
      const data = await r.json();
      watchlistSet = new Set((data.tickers || []).map((t) => t.toUpperCase()));
    } catch (_) {
      // non-fatal
    }
  }

  // ── slide rendering ────────────────────────────────────────────────────
  function renderSlides(deck) {
    const slides = [];

    // 1. Cover
    const toneCls = `tone-${deck.market_tone || "mixed"}`;
    slides.push(`
      <section>
        <h1>${esc(deck.title)}</h1>
        <div class="slide-subtitle">${esc(deck.date)} · <span class="tone-pill ${toneCls}">${esc(deck.market_tone || "mixed")}</span></div>
        <p style="font-size:0.9em;line-height:1.55">${esc(deck.overview || "")}</p>
      </section>
    `);

    // 2. Watchlist hits (only if any)
    if ((deck.watchlist_hits || []).length) {
      const rows = deck.watchlist_hits.map((h) => `
        <div class="wl-row">
          ${tickerHTML(h.ticker)}
          <span style="flex:1">${esc(h.headline || "")}</span>
          <span class="section-tag">${esc(h.section || "")}</span>
        </div>
      `).join("");
      slides.push(`
        <section>
          <h2>★ Your Watchlist in Today's News</h2>
          <div class="slide-subtitle">Tickers from your watchlist mentioned this morning</div>
          ${rows}
        </section>
      `);
    }

    // 3. Key levels
    if ((deck.key_levels || []).length) {
      const cards = deck.key_levels.map((k) => `
        <div class="level-card">
          <div class="lbl">${esc(k.label || "")}</div>
          <div class="val">${esc(k.value || "")}</div>
          ${k.note ? `<div class="note">${esc(k.note)}</div>` : ""}
        </div>
      `).join("");
      slides.push(`
        <section>
          <h2>Key Levels</h2>
          <div class="levels-grid">${cards}</div>
        </section>
      `);
    }

    // 4. Macro
    slides.push(renderNewsSection("Macro", deck.macro, false));

    // 5. Earnings
    slides.push(renderNewsSection("Earnings", deck.earnings, true));

    // 6. Movers
    if ((deck.movers || []).length) {
      const rows = deck.movers.map((m) => `
        <div class="news-card">
          <div class="news-headline">
            ${tickerHTML(m.ticker)} ${changeHTML(m.direction, m.change)} ${esc(m.headline || "")}
          </div>
          ${m.why_it_matters ? `<span class="why">Why it matters: ${esc(m.why_it_matters)}</span>` : ""}
        </div>
      `).join("");
      slides.push(`
        <section>
          <h2>Pre-Market Movers</h2>
          ${rows}
        </section>
      `);
    }

    // 7. Sectors
    if ((deck.sectors || []).length) {
      const rows = deck.sectors.map((s) => {
        const dir = s.direction === "down" ? "change-down" : s.direction === "up" ? "change-up" : "";
        const arrow = s.direction === "down" ? "▼" : s.direction === "up" ? "▲" : "·";
        return `
          <li><b>${esc(s.name || "")}</b>
            <span class="${dir}">${arrow}</span>
            <span style="color:var(--text-muted)"> — ${esc(s.note || "")}</span>
          </li>
        `;
      }).join("");
      slides.push(`<section><h2>Sectors</h2><ul>${rows}</ul></section>`);
    }

    // 8. Watch today
    if ((deck.watch_today || []).length) {
      const rows = deck.watch_today.map((w) => `<li>${esc(w)}</li>`).join("");
      slides.push(`
        <section>
          <h2>What to Watch Today</h2>
          <ul>${rows}</ul>
        </section>
      `);
    }

    // 9. Glossary (learning layer)
    if ((deck.glossary || []).length) {
      const rows = deck.glossary.map((g) => `
        <dt>${esc(g.term || "")}</dt>
        <dd>${esc(g.definition || "")}</dd>
      `).join("");
      slides.push(`
        <section>
          <h2>Glossary — Today's Jargon</h2>
          <div class="slide-subtitle">Plain-English definitions, so you don't gloss past them tomorrow</div>
          <dl class="glossary">${rows}</dl>
        </section>
      `);
    }

    return slides.join("\n");
  }

  function renderNewsSection(title, items, withTicker) {
    items = items || [];
    if (!items.length) {
      return `<section><h2>${esc(title)}</h2><p class="empty-note">Nothing notable.</p></section>`;
    }
    const rows = items.map((m) => `
      <div class="news-card">
        <div class="news-headline">
          ${withTicker ? tickerHTML(m.ticker) : ""}${esc(m.headline || "")}
        </div>
        ${m.detail ? `<div class="news-detail">${esc(m.detail)}</div>` : ""}
        ${m.why_it_matters ? `<span class="why">Why it matters: ${esc(m.why_it_matters)}</span>` : ""}
      </div>
    `).join("");
    return `<section><h2>${esc(title)}</h2>${rows}</section>`;
  }

  function mountDeck(deck) {
    currentDeck = deck;
    watchlistSet = new Set((deck.watchlist || []).map((t) => t.toUpperCase()));
    $("slides-root").innerHTML = renderSlides(deck);

    const toneCls = `tone-${deck.market_tone || "mixed"}`;
    $("deck-meta").innerHTML =
      `<b>${esc(deck.date)}</b> · ${esc(deck.title)} ` +
      `<span class="tone-pill ${toneCls}">${esc(deck.market_tone || "mixed")}</span>`;

    $("deck-shell").style.display = "block";

    if (deckInstance) {
      try { deckInstance.destroy(); } catch (_) {}
      deckInstance = null;
    }
    deckInstance = new Reveal($("reveal-root"), {
      embedded: true,
      hash: false,
      controls: true,
      progress: true,
      slideNumber: "c/t",
      transition: "slide",
      width: 1280,
      height: 760,
      margin: 0.05,
    });
    deckInstance.initialize();
  }

  // ── compose flow ───────────────────────────────────────────────────────
  $("deck-date").value = todayISO();

  $("compose-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const raw = $("raw-text").value.trim();
    const date = $("deck-date").value || todayISO();
    const overwrite = $("overwrite-toggle").checked;

    if (!raw) {
      status("compose-status", "Paste some morning summary text first.", "error");
      return;
    }

    status("compose-status", "Calling Claude to structure your deck…");
    $("generate-btn").disabled = true;
    try {
      const r = await fetch("/api/news/generate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ raw_text: raw, date, overwrite }),
      });
      const data = await r.json();
      if (!r.ok) {
        const detail = data.detail || `HTTP ${r.status}`;
        status("compose-status", `Error: ${detail}`, "error");
        if (r.status === 409) {
          status("compose-status",
            `A deck for ${date} already exists. Tick "Overwrite if exists" to replace it.`,
            "error");
        }
        return;
      }
      status("compose-status", `Deck saved for ${data.date}.`, "ok");
      mountDeck(data);
    } catch (err) {
      status("compose-status", `Network error: ${err}`, "error");
    } finally {
      $("generate-btn").disabled = false;
    }
  });

  $("present-btn").addEventListener("click", () => {
    const reveal = $("reveal-root");
    reveal.classList.toggle("fullscreen");
    if (deckInstance) deckInstance.layout();
  });

  $("reload-btn").addEventListener("click", async () => {
    if (!currentDeck) return;
    status("compose-status", "Reloading…");
    const r = await fetch(`/api/news/decks/${currentDeck.date}`);
    if (r.ok) {
      const data = await r.json();
      mountDeck(data);
      status("compose-status", "Reloaded.", "ok");
    } else {
      status("compose-status", "Reload failed.", "error");
    }
  });

  $("delete-btn").addEventListener("click", async () => {
    if (!currentDeck) return;
    if (!confirm(`Delete deck for ${currentDeck.date}? This cannot be undone.`)) return;
    const r = await fetch(`/api/news/decks/${currentDeck.date}`, { method: "DELETE" });
    if (r.ok) {
      status("compose-status", `Deleted ${currentDeck.date}.`, "ok");
      $("deck-shell").style.display = "none";
      currentDeck = null;
    } else {
      const data = await r.json().catch(() => ({}));
      status("compose-status", `Delete failed: ${data.detail || r.status}`, "error");
    }
  });

  // ── archive ────────────────────────────────────────────────────────────
  async function loadArchive() {
    status("archive-status", "Loading…");
    try {
      const r = await fetch("/api/news/decks");
      const data = await r.json();
      const decks = data.decks || [];
      if (!decks.length) {
        $("archive-list").innerHTML = "";
        status("archive-status", "No decks saved yet. Generate one from the Compose tab.");
        return;
      }
      status("archive-status", `${decks.length} deck${decks.length === 1 ? "" : "s"} archived.`);
      $("archive-list").innerHTML = decks.map((d) => `
        <div class="archive-card" data-date="${esc(d.date)}">
          <div class="date">${esc(d.date)}</div>
          <div class="title">${esc(d.title || "(untitled)")}</div>
          <div class="counts">
            <span><b>${d.movers}</b> movers</span>
            <span><b>${d.earnings}</b> earnings</span>
            <span><b>${d.watchlist_hits}</b> watchlist</span>
          </div>
        </div>
      `).join("");
      $("archive-list").querySelectorAll(".archive-card").forEach((el) => {
        el.addEventListener("click", () => openArchived(el.dataset.date));
      });
    } catch (err) {
      status("archive-status", `Error: ${err}`, "error");
    }
  }

  async function openArchived(date) {
    const r = await fetch(`/api/news/decks/${date}`);
    if (!r.ok) {
      status("archive-status", `Failed to load ${date}`, "error");
      return;
    }
    const data = await r.json();
    // jump to compose panel and mount
    document.querySelectorAll("nav button[data-panel]").forEach((b) =>
      b.classList.remove("active"),
    );
    document.querySelector('nav button[data-panel="panel-compose"]').classList.add("active");
    document.querySelectorAll(".panel").forEach((p) => p.classList.remove("active"));
    $("panel-compose").classList.add("active");
    mountDeck(data);
    status("compose-status", `Loaded archived deck for ${date}.`, "ok");
  }

  // ── boot ───────────────────────────────────────────────────────────────
  fetchWatchlist();
})();
