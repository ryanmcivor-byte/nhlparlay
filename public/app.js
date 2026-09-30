// Chayka's Evil Parlays: front end (vanilla JS, no build step)
(function () {
  "use strict";

  const $ = (s) => document.querySelector(s);
  const state = { data: null, game: "all", slip: [], expanded: new Set(), lineupOnly: true };
  const PLAYING = new Set(["confirmed", "projected"]);     // certain (or projected) to dress
  const playing = (p) => PLAYING.has((p.lineup || {}).status);
  const VIBES = {
    chaos: () => true,
    gossip: (f) => f.kind.startsWith("news-"),
    grudge: (f) => ["revenge", "hometown", "draft", "newteam", "names", "twins"].includes(f.kind),
    any: () => true,
  };
  const LINEUP_ICON = { confirmed: "✅", projected: "📋", gtd: "⚠️", out: "🚫", unknown: "❔" };

  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const pct = (p) => (p * 100).toFixed(p < 0.1 ? 1 : 0) + "%";
  function american(p) {
    if (!p || p <= 0 || p >= 1) return "—";
    return p >= 0.5 ? "-" + Math.round((p / (1 - p)) * 100) : "+" + Math.round(((1 - p) / p) * 100);
  }
  function todayET() {
    const d = new Date(Date.now() - 4 * 3600 * 1000);
    return d.toISOString().slice(0, 10);
  }
  function load(key, fallback) {
    try { return JSON.parse(localStorage.getItem(key)) ?? fallback; } catch (e) { return fallback; }
  }
  function save(key, v) {
    try { localStorage.setItem(key, JSON.stringify(v)); } catch (e) { /* private mode */ }
  }

  // ---------------------------------------------------------------- data
  let pollTimer = null;
  async function fetchSlate(date) {
    clearTimeout(pollTimer);
    let res;
    try {
      res = await (await fetch("/api/slate?date=" + date)).json();
    } catch (e) {
      return showStatus("Couldn't reach the server. Is <code>python3 server.py</code> running?");
    }
    if ($("#date").value !== date) return;
    if (res.status === "building") {
      showStatus(`${esc(res.step)}…<div class="bar"><i style="width:${Math.round(res.progress * 100)}%"></i></div>`);
      pollTimer = setTimeout(() => fetchSlate(date), 1200);
      return;
    }
    if (res.status === "error") return showStatus("The goblins tripped: " + esc(res.error));
    state.data = res;
    state.slip = load("slip:" + date, []).filter((id) => res.players.some((p) => p.id === id));
    const why = load("why:" + date, {});       // the reason each leg was summoned
    res.players.forEach((p) => { if (why[p.id]) p.pickedFact = why[p.id]; });
    render();
  }

  function showStatus(html) {
    $("#status").innerHTML = html;
    $("#status").hidden = false;
  }

  // -------------------------------------------------------------- render
  function render() {
    const d = state.data;
    if (!d.games.length) {
      showStatus("No NHL games on this date. Even evil takes a night off. Try another date.");
      $("#summon").hidden = $("#slip").hidden = true;
      $("#gameChips").innerHTML = $("#players").innerHTML = "";
      return;
    }
    const inLineup = d.players.filter(playing).length;
    const withDirt = d.players.filter((p) => p.facts.length).length;
    showStatus(`${d.games.length} game${d.games.length > 1 ? "s" : ""} · ${inLineup} skaters in tonight's lineups · ` +
      `${withDirt} with dirt on them · ${d.newsCount} real headlines matched`);
    $("#summon").hidden = false;
    renderChips();
    renderPlayers();
    renderSlip();
  }

  function renderChips() {
    const d = state.data;
    const chips = [`<button class="chip ${state.game === "all" ? "on" : ""}" data-game="all">All games</button>`]
      .concat(d.games.map((g) => {
        const t = g.start ? new Date(g.start).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" }) : "";
        return `<button class="chip ${state.game == g.id ? "on" : ""}" data-game="${g.id}">${esc(g.away)} @ ${esc(g.home)} <span class="muted">${t}</span></button>`;
      }));
    $("#gameChips").innerHTML = chips.join("");
  }

  function factHtml(f) {
    const text = f.link
      ? `<a href="${esc(f.link)}" target="_blank" rel="noopener noreferrer">${esc(f.text)}</a>`
      : esc(f.text);
    return `<li class="fact"><span class="em">${f.emoji}</span><div><b>${esc(f.title)}</b><p>${text}</p></div></li>`;
  }

  function renderPlayers() {
    const list = state.data.players.filter((p) => (state.game === "all" || p.gameId == state.game)
      && (!state.lineupOnly || playing(p) || state.slip.includes(p.id)));
    $("#players").innerHTML = list.map((p) => {
      const open = state.expanded.has(p.id);
      const shown = open ? p.facts : p.facts.slice(0, 3);
      const inSlip = state.slip.includes(p.id);
      const lu = p.lineup || { status: "unknown", label: "Lineup unknown" };
      return `<article class="player card ${playing(p) ? "" : "benched"}" data-id="${p.id}">
        <div class="p-head">
          <img src="${esc(p.headshot || "")}" alt="" loading="lazy">
          <div>
            <div class="p-name">${esc(p.name)} ${p.num ? `<span class="muted">#${p.num}</span>` : ""}</div>
            <div class="p-sub">${esc(p.pos)} · ${esc(p.team)} ${p.home ? "vs" : "@"} ${esc(p.opp)}</div>
          </div>
          <div class="p-prob"><b>${pct(p.prob)}</b><span>${american(p.prob)}</span></div>
        </div>
        <div class="meter"><i style="width:${Math.min(100, p.chaos * 3.4)}%"></i></div>
        <span class="lineup ${lu.status}" title="${esc(lu.detail || "")}">${LINEUP_ICON[lu.status] || ""} ${esc(lu.label)}</span>
        ${shown.length ? `<ul class="facts">${shown.map(factHtml).join("")}</ul>` : `<p class="nodirt">No dirt on him tonight. Just the numbers.</p>`}
        ${p.facts.length > 3 ? `<button class="more" data-more="${p.id}">${open ? "Show less" : `+${p.facts.length - 3} more`}</button>` : ""}
        <div class="p-foot">
          <span class="chaos">Chaos ${p.chaos}</span>
          <button class="btn-add ${inSlip ? "on" : ""}" data-add="${p.id}">${inSlip ? "✕ Remove" : "+ Add leg"}</button>
        </div>
      </article>`;
    }).join("");
  }

  function renderSlip() {
    const legs = state.slip.map((id) => state.data.players.find((p) => p.id === id)).filter(Boolean);
    $("#slip").hidden = !legs.length;
    save("slip:" + state.data.date, state.slip);
    const why = {};
    state.data.players.forEach((p) => { if (p.pickedFact && state.slip.includes(p.id)) why[p.id] = p.pickedFact; });
    save("why:" + state.data.date, why);
    if (!legs.length) return;
    $("#slipLegs").innerHTML = legs.map((p) => {
      const f = (p.pickedFact === "stat" && { emoji: "📈", title: `Statistical favourite · ${(p.lineup || {}).label || ""}` })
        || (p.pickedFact && p.facts.find((x) => x.title === p.pickedFact)) || p.facts[0]
        || { emoji: "📈", title: `${pct(p.prob)} goal chance` };
      return `<li class="leg">
        <img src="${esc(p.headshot || "")}" alt="">
        <div><b>${esc(p.name)}</b> <span class="muted">to score · ${esc(p.team)} ${p.home ? "vs" : "@"} ${esc(p.opp)} · ${pct(p.prob)}</span>
          <div class="why">${f.emoji} ${esc(f.title)}</div></div>
        <button class="x" data-add="${p.id}" aria-label="Remove ${esc(p.name)}">✕</button>
      </li>`;
    }).join("");
    $("#slipNote").textContent = state.note || "";
    $("#slipNote").hidden = !state.note;
    const prob = legs.reduce((a, p) => a * p.prob, 1);
    $("#slipProb").textContent = pct(prob);
    $("#slipOdds").textContent = american(prob);
  }

  // ------------------------------------------------------------- summon
  function summon() {
    const d = state.data;
    const n = +$("#legs").value;
    const vibe = $("#vibe").value;
    const keep = VIBES[vibe];
    if (vibe === "favourites") return summonFavourites(n);
    // one leg per game, so draw from every game (the chips only filter the cards below)
    let pool = d.players
      .filter(playing)
      .map((p) => {
        const fs = p.facts.filter(keep);
        return fs.length ? { p, f: fs[Math.floor(Math.random() * fs.length)], w: fs.reduce((a, f) => a + f.chaos, 0) } : null;
      })
      .filter(Boolean);
    if (vibe === "chaos") pool.forEach((x) => (x.w = x.w * x.w));      // lean hard into the wild ones
    if (vibe === "any") pool.forEach((x) => (x.w = 1));
    if (!pool.length) return showStatus("No skaters in tonight's lineups match that vibe. Try another one.");
    const picked = [];
    while (picked.length < n && pool.length) {
      const total = pool.reduce((a, x) => a + x.w, 0);
      let r = Math.random() * total, i = 0;
      while (r > pool[i].w && i < pool.length - 1) r -= pool[i++].w;
      const pick = pool[i];
      picked.push(pick);
      pool = pool.filter((x) => x.p.gameId !== pick.p.gameId);
    }
    picked.forEach((x) => (x.p.pickedFact = x.f.title));
    finishSummon(picked.map((x) => x.p), n, "a skater matching that vibe");
  }

  // Statistical Favourites: in each game, one of its 3 likeliest scorers (leaning hard
  // toward #1), then the games whose pick is likeliest to score.
  function summonFavourites(n) {
    const byGame = {};
    state.data.players.filter(playing).forEach((p) => (byGame[p.gameId] = byGame[p.gameId] || []).push(p));
    const picks = Object.values(byGame).map((ps) => {
      const top = ps.sort((a, b) => b.prob - a.prob).slice(0, 3);
      const w = top.map((p) => Math.pow(p.prob, 6));
      let r = Math.random() * w.reduce((a, b) => a + b, 0), i = 0;
      while (r > w[i] && i < top.length - 1) r -= w[i++];
      return top[i];
    }).sort((a, b) => b.prob - a.prob).slice(0, n);
    if (!picks.length) return showStatus("No lineups are posted or projected for this slate yet. Check back closer to puck drop.");
    picks.forEach((p) => (p.pickedFact = "stat"));
    finishSummon(picks, n, "a skater in its lineup");
  }

  function finishSummon(players, n, what) {
    const d = state.data;
    const picked = players;
    state.slip = picked.map((p) => p.id);
    const legs = `${picked.length} leg${picked.length > 1 ? "s" : ""}`;
    state.note = picked.length >= n ? ""
      : picked.length === d.games.length
        ? `There ${d.games.length > 1 ? "are" : "is"} only ${d.games.length} game${d.games.length > 1 ? "s" : ""} on this slate, so this parlay has ${legs} (one per game).`
        : `Only ${picked.length} games have ${what}, so this parlay has ${legs} (one per game).`;
    renderPlayers();
    renderSlip();
    $("#slip").scrollIntoView({ behavior: "smooth", block: "nearest" });
  }

  function toggleLeg(id) {
    const i = state.slip.indexOf(id);
    state.note = "";
    if (i >= 0) state.slip.splice(i, 1);
    else {
      // one leg per game: adding a skater swaps out any leg from the same game
      const p = state.data.players.find((x) => x.id === id);
      const clash = state.data.players.find((x) => x.gameId === p.gameId && x.id !== id && state.slip.includes(x.id));
      if (clash) {
        state.slip = state.slip.filter((x) => x !== clash.id);
        state.note = `Swapped out ${clash.name}: one leg per game.`;
      }
      state.slip.push(id);
    }
    renderPlayers();
    renderSlip();
  }

  // -------------------------------------------------------------- events
  document.addEventListener("click", (e) => {
    const t = e.target.closest("[data-add],[data-more],[data-game]");
    if (!t) return;
    if (t.dataset.add) toggleLeg(+t.dataset.add);
    else if (t.dataset.more) {
      const id = +t.dataset.more;
      state.expanded.has(id) ? state.expanded.delete(id) : state.expanded.add(id);
      renderPlayers();
    } else if (t.dataset.game) {
      state.game = t.dataset.game;
      renderChips();
      renderPlayers();
    }
  });
  $("#summonBtn").addEventListener("click", summon);
  $("#lineupOnly").addEventListener("change", (e) => { state.lineupOnly = e.target.checked; renderPlayers(); });
  $("#clearSlip").addEventListener("click", () => { state.slip = []; state.note = ""; renderPlayers(); renderSlip(); });
  $("#date").addEventListener("change", (e) => {
    state.game = "all";
    showStatus("Loading…");
    fetchSlate(e.target.value);
  });

  $("#date").value = todayET();
  showStatus("Waking the goblins…");
  fetchSlate($("#date").value);
})();
