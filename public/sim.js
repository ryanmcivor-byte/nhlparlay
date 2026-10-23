// Game Simulations: list the day's games, then animate the chosen simulation.
(function () {
  "use strict";

  const $ = (s) => document.querySelector(s);
  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const logo = (abbr) => `https://assets.nhle.com/logos/nhl/svg/${abbr}_dark.svg`;
  const COLORS = {
    ANA: "#F47A38", BOS: "#FFB81C", BUF: "#2E5CC2", CAR: "#CE1126", CBJ: "#3D5FA8", CGY: "#C8102E",
    CHI: "#CF0A2C", COL: "#8E3A5B", DAL: "#00A06B", DET: "#E31937", EDM: "#FF4C00", FLA: "#C8102E",
    LAK: "#A2AAAD", MIN: "#2E8B57", MTL: "#AF1E2D", NJD: "#CE1126", NSH: "#FFB81C", NYI: "#F47D30",
    NYR: "#2D5BC4", OTT: "#DA1A32", PHI: "#F74902", PIT: "#FCB514", SEA: "#68A2B9", SJS: "#00A3B4",
    STL: "#2E5FC2", TBL: "#3A6FD8", TOR: "#3A6FD8", UTA: "#6CACE4", VAN: "#2E7D5B", VGK: "#B4975A",
    WPG: "#5B8ED1", WSH: "#C8102E",
  };
  const PERIOD_NAMES = ["1st", "2nd", "3rd", "OT", "SO"];

  // timing: 60 game minutes in ~29 seconds, plus short goal and intermission pauses,
  // so a typical game runs about 35 seconds and a high-scoring one about 40
  const MS_PER_MIN = 480;
  const INTERMISSION = 1000;
  const GOAL_PAUSE = 1200;

  let data = null, current = null, anim = null;

  function todayET() {
    try {
      return new Intl.DateTimeFormat("en-CA", { timeZone: "America/New_York" }).format(new Date());
    } catch (e) { return new Date(Date.now() - 4 * 3600e3).toISOString().slice(0, 10); }
  }

  // --------------------------------------------------------------- list
  async function load(date) {
    $("#simStatus").textContent = "Simulating tonight's games…";
    $("#simStatus").hidden = false;
    $("#simGames").innerHTML = "";
    showList();
    try {
      data = await (await fetch("/api/sims?date=" + date)).json();
    } catch (e) {
      $("#simStatus").textContent = "Couldn't reach the server.";
      return;
    }
    if ($("#date").value !== date) return;
    if (data.error) { $("#simStatus").textContent = "The simulator tripped: " + data.error; return; }
    renderRecord(data.record);
    if (!data.games.length) { $("#simStatus").textContent = "No NHL games on this date. Try another day."; return; }
    $("#simStatus").textContent = `${data.games.length} game${data.games.length > 1 ? "s" : ""}. Pick one and play it out.`;
    $("#simGames").innerHTML = data.games.map((g) => {
      const t = g.start ? new Date(g.start).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" }) : "";
      return `<button class="sim-card card" data-game="${g.id}">
        <span class="sim-team"><img src="${logo(g.away)}" alt="" width="56" height="40">${esc(g.away)}</span>
        <span class="sim-at">@<small>${esc(t)}</small></span>
        <span class="sim-team"><img src="${logo(g.home)}" alt="" width="56" height="40">${esc(g.home)}</span>
        <span class="sim-cta">▶ Play it out</span>
      </button>`;
    }).join("");
  }

  // the simulator's own record: did the team that won most of the 10 sims win for real?
  function renderRecord(r) {
    if (!r) return;
    const graded = r.total.W + r.total.L;
    const pct = (o) => (o.W + o.L ? Math.round((o.W / (o.W + o.L)) * 100) : 0) + "%";
    const fmtDay = (d) => new Date(d + "T12:00:00").toLocaleDateString([], { weekday: "short", month: "short", day: "numeric" });
    $("#srTotal").textContent = `${r.total.W}-${r.total.L}`;
    $("#srSub").textContent = graded ? `${pct(r.total)} of winners picked · since ${fmtDay(r.start)}`
      : `Starts with ${fmtDay(r.start)}'s games · first results the next morning`;
    $("#srChips").innerHTML = `
      <span class="rec-chip">💪 Confident (${r.confidentAt}+ of 10) <b>${r.confident.W}-${r.confident.L}</b> ${pct(r.confident)}</span>
      <span class="rec-chip">🤏 Close calls <b>${r.close.W}-${r.close.L}</b> ${pct(r.close)}</span>
      <span class="rec-chip">🎯 Exact final score <b>${r.exact}</b></span>`;
    $("#srDays").innerHTML = !r.days.length ? `<p class="muted">No games graded yet.</p>` : r.days.map((d) => `
      <div class="rec-day">
        <div class="rec-date">${fmtDay(d.date)}</div>
        <div class="rec-parlays">${d.games.map((g) => {
          const ot = (e) => (e && e !== "REG" ? ` ${e}` : "");
          return `<div class="rec-parlay ${g.status === "W" ? "win" : "loss"}"><span class="rec-res">${g.status}</span>
            <span class="rec-vibe">${esc(g.away)} @ ${esc(g.home)} · picked ${esc(g.pick)} (${Math.max(g.wins.home, g.wins.away)}/10)</span>
            <span class="rec-legs">Sim ${g.shown[0]}-${g.shown[1]}${ot(g.shown[2])} · Real ${g.real[0]}-${g.real[1]}${ot(g.real[2])}${g.exact ? " 🎯" : ""}</span></div>`;
        }).join("")}</div>
      </div>`).join("");

    $("#simRecord").hidden = false;
  }

  function showList() {
    stop();
    $("#simGame").hidden = true;
    $("#simGames").hidden = false;
  }

  // ------------------------------------------------------------ one game
  function openGame(id) {
    current = data.games.find((g) => g.id == id);
    if (!current) return;
    stop();
    $("#simGames").hidden = true;
    $("#simStatus").hidden = true;
    $("#simGame").hidden = false;
    $("#simTitle").textContent = `${current.awayName} at ${current.homeName}`;
    for (const side of ["away", "home"]) {
      const el = $(side === "away" ? "#sbAway" : "#sbHome");
      el.querySelector(".sb-abbr").textContent = current[side];
      el.querySelector(".sb-score").textContent = "0";
      el.querySelector(".sb-shots").textContent = "SOG 0";
      el.style.setProperty("--team", COLORS[current[side]] || "#ccc");
    }
    $("#sbPeriod").textContent = "1st";
    $("#sbClock").textContent = "20:00";
    $("#simFeed").innerHTML = "";
    $("#simResult").hidden = true;
    $("#playBtn").hidden = false;
    $("#skipBtn").hidden = $("#replayBtn").hidden = true;
    $("#simInputs").innerHTML = inputsHtml(current);
    drawStatic();
    window.scrollTo({ top: $("#simGame").offsetTop - 10 });
  }

  function inputsHtml(g) {
    const gl = (s) => g.goalies[s] ? `${esc(g.goalies[s].name)} (${(g.goalies[s].svPct * 100).toFixed(1)} sv%${
      g.goalies[s].status && g.goalies[s].status.toLowerCase() !== "confirmed" ? ", expected" : ""})` : "unknown";
    const h2h = g.h2h.length ? g.h2h.map((m) => `<li>${esc(m.date)}: ${esc(m.away)} ${m.as} - ${m.hs} ${esc(m.home)}${m.ot ? " (OT/SO)" : ""}</li>`).join("")
      : "<li>No recent meetings.</li>";
    return `<h3>What the computer looked at</h3>
      <table class="inputs-table">
        <tr><th></th><th>${esc(g.away)}</th><th>${esc(g.home)}</th></tr>
        <tr><td>Record</td><td>${esc(g.teams.away.record)}</td><td>${esc(g.teams.home.record)}</td></tr>
        <tr><td>Last 10</td><td>${esc(g.teams.away.last10 || "-")}</td><td>${esc(g.teams.home.last10 || "-")}</td></tr>
        <tr><td>Goals for / game</td><td>${g.teams.away.gfPerGame}</td><td>${g.teams.home.gfPerGame}</td></tr>
        <tr><td>Goals against / game</td><td>${g.teams.away.gaPerGame}</td><td>${g.teams.home.gaPerGame}</td></tr>
        <tr><td>Starting goalie</td><td>${gl("away")}</td><td>${gl("home")}</td></tr>
        <tr><td>Expected goals tonight</td><td><b>${g.expectedGoals.away.toFixed(2)}</b></td><td><b>${g.expectedGoals.home.toFixed(2)}</b></td></tr>
      </table>
      <h4>Recent head-to-head</h4><ul class="h2h">${h2h}</ul>`;
  }

  // ------------------------------------------------------------- the rink
  const W = 1000, H = 440;
  const X0 = 20, X1 = 980, Y0 = 20, Y1 = 420;          // boards
  const FT = (X1 - X0) / 200;                          // 200 ft long
  const GOAL_L = X0 + 11 * FT, GOAL_R = X1 - 11 * FT;
  const BLUE_L = X0 + 75 * FT, BLUE_R = X1 - 75 * FT;
  const CY = (Y0 + Y1) / 2;
  let rinkImg = null;

  function rinkCanvas() {
    if (rinkImg) return rinkImg;
    const c = document.createElement("canvas");
    c.width = W; c.height = H;
    const x = c.getContext("2d");
    x.fillStyle = "#0b0507"; x.fillRect(0, 0, W, H);
    const r = 28 * FT;
    x.beginPath();
    x.moveTo(X0 + r, Y0); x.lineTo(X1 - r, Y0); x.arcTo(X1, Y0, X1, Y0 + r, r);
    x.lineTo(X1, Y1 - r); x.arcTo(X1, Y1, X1 - r, Y1, r); x.lineTo(X0 + r, Y1);
    x.arcTo(X0, Y1, X0, Y1 - r, r); x.lineTo(X0, Y0 + r); x.arcTo(X0, Y0, X0 + r, Y0, r); x.closePath();
    x.fillStyle = "#eef3f8"; x.fill();
    x.lineWidth = 4; x.strokeStyle = "#c9d3dd"; x.stroke();
    x.save(); x.clip();
    const line = (xx, color, w) => { x.fillStyle = color; x.fillRect(xx - w / 2, Y0, w, Y1 - Y0); };
    line((X0 + X1) / 2, "#d6232f", 5);
    line(BLUE_L, "#2b55c7", 6); line(BLUE_R, "#2b55c7", 6);
    line(GOAL_L, "#d6232f", 2); line(GOAL_R, "#d6232f", 2);
    x.strokeStyle = "#2b55c7"; x.lineWidth = 2;
    x.beginPath(); x.arc((X0 + X1) / 2, CY, 15 * FT, 0, Math.PI * 2); x.stroke();
    x.strokeStyle = "#d6232f";
    for (const fx of [X0 + 31 * FT, X1 - 31 * FT]) for (const fy of [CY - 22 * FT, CY + 22 * FT]) {
      x.beginPath(); x.arc(fx, fy, 15 * FT, 0, Math.PI * 2); x.stroke();
    }
    x.fillStyle = "rgba(80,150,230,0.35)";
    for (const [gx, dir] of [[GOAL_L, 1], [GOAL_R, -1]]) {
      x.beginPath(); x.arc(gx, CY, 6 * FT, dir > 0 ? -Math.PI / 2 : Math.PI / 2, dir > 0 ? Math.PI / 2 : Math.PI * 1.5); x.fill();
    }
    x.restore();
    // nets
    x.strokeStyle = "#d6232f"; x.lineWidth = 3;
    x.strokeRect(GOAL_L - 4 * FT, CY - 3 * FT, 4 * FT, 6 * FT);
    x.strokeRect(GOAL_R, CY - 3 * FT, 4 * FT, 6 * FT);
    rinkImg = c;
    return c;
  }

  function drawStatic() {
    const ctx = $("#rink").getContext("2d");
    ctx.drawImage(rinkCanvas(), 0, 0);
    if (!current) return;
    for (const side of ["away", "home"]) {
      ctx.fillStyle = COLORS[current[side]] || "#999";
      ctx.globalAlpha = 0.8;
      ctx.font = "bold 64px system-ui, sans-serif";
      ctx.textAlign = "center";
      ctx.fillText(current[side], side === "away" ? W * 0.3 : W * 0.7, CY + 22);
      ctx.globalAlpha = 1;
    }
  }

  // ------------------------------------------------------------ the replay
  // seeded random so a replay looks the same every time
  function rng(seed) {
    let s = 0;
    for (const ch of String(seed)) s = (s * 31 + ch.charCodeAt(0)) >>> 0;
    return () => { s = (s * 1664525 + 1013904223) >>> 0; return s / 4294967296; };
  }

  // away defends the left net (it's on the left of the scoreboard), home the right:
  // so away attacks the right net and home attacks the left
  const NET = { away: GOAL_R, home: GOAL_L };
  const DIR = { away: 1, home: -1 };
  const ROLES = [
    { lane: -0.55, atk: 70, def: -20 }, { lane: 0, atk: 40, def: -40 }, { lane: 0.55, atk: 70, def: -20 },
    { lane: -0.35, atk: -110, def: -140 }, { lane: 0.35, atk: -110, def: -140 },
  ];

  function buildTimeline(g) {
    const r = rng(`${g.id}|timeline`);
    const shown = g.shown;
    const ev = [];
    for (const e of shown.events) {
      if (e.shootout) continue;
      ev.push({ t: e.t, side: e.side, kind: "goal", scorer: e.scorer });
    }
    for (const side of ["home", "away"]) {
      const goals = ev.filter((e) => e.side === side).length;
      const saves = Math.max(0, shown.shots[side] - goals);
      for (let i = 0; i < saves; i++) ev.push({ t: r() * 60, side, kind: "save" });
    }
    ev.sort((a, b) => a.t - b.t);
    // keep events at least 0.35 min apart so each one can be seen
    for (let i = 1; i < ev.length; i++) if (ev[i].t - ev[i - 1].t < 0.35 && ev[i].kind === "save") ev[i].t = ev[i - 1].t + 0.35;
    return ev.filter((e) => e.t < (shown.end === "OT" ? 65 : 60) || e.kind === "goal");
  }

  function makePlayers(r) {
    const ps = [];
    for (const side of ["home", "away"]) {
      ROLES.forEach((role, i) => ps.push({ side, role, i, x: (X0 + X1) / 2 - DIR[side] * (30 + i * 12), y: CY + role.lane * 150,
        vx: 0, vy: 0, phase: r() * 6.28 }));
      ps.push({ side, goalie: true, x: NET[side === "home" ? "away" : "home"] + DIR[side] * 10, y: CY, phase: 0 });
    }
    return ps;
  }

  function play() {
    if (!current) return;
    stop();
    const g = current;
    const r = rng(`${g.id}|motion`);
    const timeline = buildTimeline(g);
    const regEnd = 60, otEnd = g.shown.end === "REG" ? 60 : 65;
    const state = {
      gameMin: 0, realMs: 0, pause: 0, pausedText: "", idx: 0, period: 1,
      score: { home: 0, away: 0 }, shots: { home: 0, away: 0 },
      players: makePlayers(r), possession: r() < 0.5 ? "home" : "away", carrier: null,
      puck: { x: (X0 + X1) / 2, y: CY, from: null, to: null, tFly: 0, durFly: 0 },
      nextPass: 0.4, nextTurnover: 1.5, done: false, finalShown: false, lastPeriodShown: 1,
    };
    state.carrier = pickCarrier(state, state.possession, r);
    $("#playBtn").hidden = true;
    $("#skipBtn").hidden = false;
    $("#replayBtn").hidden = true;
    $("#simResult").hidden = true;
    $("#simFeed").innerHTML = "";
    updateBoard(state, g);

    let last = performance.now();
    const ctx = $("#rink").getContext("2d");

    function frame(now) {
      let dt = now - last;
      last = now;
      if (dt > 100) dt = 16;                      // tab was hidden: don't jump ahead
      if (state.pause > 0) {
        state.pause -= dt;
        if (state.pause <= 0) { $("#goalFlash").hidden = true; afterPause(state, g, r); }
      } else if (!state.done) {
        advance(state, g, timeline, dt / MS_PER_MIN, regEnd, otEnd, r);
      }
      moveDots(state, dt / 1000, r);
      render(ctx, state, g);
      if (!state.done || state.pause > 0) anim = requestAnimationFrame(frame);
      else finish(state, g);
    }
    anim = requestAnimationFrame(frame);
  }

  function stop() {
    if (anim) cancelAnimationFrame(anim);
    anim = null;
    $("#goalFlash").hidden = true;
  }

  function pickCarrier(state, side, r) {
    const sk = state.players.filter((p) => p.side === side && !p.goalie);
    return sk[Math.floor(r() * sk.length)];
  }

  function advance(state, g, tl, dMin, regEnd, otEnd, r) {
    const before = state.gameMin;
    state.gameMin += dMin;
    // period breaks
    const p = state.gameMin < 20 ? 1 : state.gameMin < 40 ? 2 : state.gameMin < 60 ? 3 : 4;
    if (p > state.period && state.gameMin <= otEnd + 0.01) {
      if (p === 4 && otEnd === 60) { /* no overtime */ } else {
        state.gameMin = (p - 1) * 20;
        state.period = p;
        state.pause = INTERMISSION;
        state.pausedText = p === 4 ? "End of regulation: tied!" : `End of the ${PERIOD_NAMES[p - 2]}`;
        flash(state.pausedText, "#c9d3dd");
        faceoff(state, r);
        updateBoard(state, g);
        return;
      }
    }
    // the next scripted event: set up the possession just before it
    const ev = tl[state.idx];
    if (ev && ev.t - state.gameMin < 0.5 && state.possession !== ev.side) {
      state.possession = ev.side;
      state.carrier = pickCarrier(state, ev.side, r);
      launch(state, state.carrier.x, state.carrier.y, 0.18);
    }
    if (ev && state.gameMin >= ev.t) {
      state.idx++;
      state.shots[ev.side]++;
      const net = NET[ev.side];
      const target = { x: ev.kind === "goal" ? net + DIR[ev.side] * 2 * FT : net - DIR[ev.side] * 3 * FT,
                       y: CY + (r() - 0.5) * (ev.kind === "goal" ? 18 : 30) };
      launch(state, target.x, target.y, 0.22);
      state.carrier = null;
      if (ev.kind === "goal") {
        state.score[ev.side]++;
        state.pause = GOAL_PAUSE;
        flash(`🚨 GOAL! ${ev.scorer} (${g[ev.side]})`, COLORS[g[ev.side]] || "#e0263f");
        feed(state, g, ev);
        state.afterGoal = true;
        if (state.gameMin >= 60) state.done = true;   // overtime winner
      } else {
        state.afterSave = 0.25;   // goalie freezes it, then the other team breaks out
      }
      updateBoard(state, g);
      return;
    }
    if (state.afterSave !== undefined) {
      state.afterSave -= dMin;
      if (state.afterSave <= 0) {
        delete state.afterSave;
        state.possession = state.possession === "home" ? "away" : "home";
        state.carrier = pickCarrier(state, state.possession, r);
        launch(state, state.carrier.x, state.carrier.y, 0.25);
      }
    } else if (state.puck.durFly <= 0 && state.carrier) {
      // passing and the odd turnover between scripted events
      state.nextPass -= dMin;
      state.nextTurnover -= dMin;
      if (state.nextTurnover <= 0) {
        state.nextTurnover = 0.8 + r() * 2.2;
        state.possession = state.possession === "home" ? "away" : "home";
        state.carrier = pickCarrier(state, state.possession, r);
        launch(state, state.carrier.x, state.carrier.y, 0.2);
      } else if (state.nextPass <= 0) {
        state.nextPass = 0.25 + r() * 0.5;
        const mates = state.players.filter((q) => q.side === state.possession && !q.goalie && q !== state.carrier);
        state.carrier = mates[Math.floor(r() * mates.length)];
        launch(state, state.carrier.x, state.carrier.y, 0.2);
      }
    }
    // end of regulation (no OT needed) / end of OT / shootout
    if (state.gameMin >= regEnd && (g.shown.end === "REG" || before >= 65) && !state.pause) state.done = true;
    if (g.shown.end === "SO" && state.gameMin >= 65 && !state.shootout) {
      state.shootout = true;
      state.period = 5;
      const w = g.shown.events.find((e) => e.shootout);
      state.score[w.side]++;
      state.pause = 2600;
      flash(`🥅 Shootout: ${g[w.side]} wins it!`, COLORS[g[w.side]] || "#e0263f");
      feed(state, g, { t: 65, side: w.side, scorer: "Shootout", kind: "so" });
      state.done = true;
      updateBoard(state, g);
    }
    if (g.shown.end === "OT" && state.gameMin >= 65) state.done = true;
    updateBoard(state, g);
  }

  function afterPause(state, g, r) {
    if (state.afterGoal) {
      state.afterGoal = false;
      faceoff(state, r);
    }
  }

  function faceoff(state, r) {
    state.puck = { x: (X0 + X1) / 2, y: CY, from: null, to: null, tFly: 0, durFly: 0 };
    for (const p of state.players) {
      if (p.goalie) continue;
      p.x = (X0 + X1) / 2 - DIR[p.side] * (20 + p.i * 14);
      p.y = CY + p.role.lane * 140;
    }
    state.possession = r() < 0.5 ? "home" : "away";
    state.carrier = pickCarrier(state, state.possession, r);
    state.nextPass = 0.3;
  }

  function launch(state, x, y, seconds) {
    const pk = state.puck;
    pk.from = { x: pk.x, y: pk.y };
    pk.to = { x, y };
    pk.tFly = 0;
    pk.durFly = seconds;
  }

  function moveDots(state, dt, r) {
    const pk = state.puck;
    if (pk.durFly > 0) {
      pk.tFly += dt;
      const k = Math.min(1, pk.tFly / pk.durFly);
      pk.x = pk.from.x + (pk.to.x - pk.from.x) * k;
      pk.y = pk.from.y + (pk.to.y - pk.from.y) * k;
      if (k >= 1) pk.durFly = 0;
    } else if (state.carrier) {
      pk.x = state.carrier.x + DIR[state.carrier.side] * 9;
      pk.y = state.carrier.y + 3;
    }
    const t = performance.now() / 1000;
    for (const p of state.players) {
      let tx, ty, speed = 260;
      if (p.goalie) {
        const own = NET[p.side === "home" ? "away" : "home"];
        tx = own + (own === GOAL_L ? 1 : -1) * 2.5 * FT;
        ty = CY + Math.max(-3, Math.min(3, (pk.y - CY) / 30)) * FT;
        speed = 160;
      } else if (p === state.carrier && !state.pause) {
        const net = NET[p.side];
        tx = net - DIR[p.side] * 70;
        ty = CY + Math.sin(t * 1.3 + p.phase) * 110;
        speed = 150;
      } else {
        const attacking = state.possession === p.side;
        const off = attacking ? p.role.atk : p.role.def;
        tx = pk.x + DIR[p.side] * off + Math.sin(t * 0.9 + p.phase) * 18;
        ty = CY + p.role.lane * 160 + (pk.y - CY) * 0.35 + Math.cos(t * 1.1 + p.phase) * 16;
      }
      tx = Math.max(X0 + 18, Math.min(X1 - 18, tx));
      ty = Math.max(Y0 + 16, Math.min(Y1 - 16, ty));
      const dx = tx - p.x, dy = ty - p.y, d = Math.hypot(dx, dy);
      const step = Math.min(d, speed * dt);
      if (d > 0.5) { p.x += (dx / d) * step; p.y += (dy / d) * step; }
    }
  }

  function render(ctx, state, g) {
    ctx.drawImage(rinkCanvas(), 0, 0);
    // on a phone the rink is a third of its drawn size: grow the dots so they stay visible
    const k = Math.max(1, Math.min(2.2, 700 / (ctx.canvas.clientWidth || W)));
    for (const p of state.players) {
      ctx.beginPath();
      ctx.arc(p.x, p.y, (p.goalie ? 13 : 10) * k, 0, Math.PI * 2);
      // like the real NHL: the home team wears its colour, the away team wears white
      const team = COLORS[g[p.side]] || (p.side === "home" ? "#d33" : "#36c");
      ctx.fillStyle = p.side === "home" ? team : "#ffffff";
      ctx.fill();
      ctx.lineWidth = (p === state.carrier ? 5 : 3) * Math.min(k, 1.6);
      ctx.strokeStyle = p.side === "home" ? (p.goalie ? "#111" : "#ffffff") : team;
      ctx.stroke();
    }
    ctx.beginPath();
    ctx.arc(state.puck.x, state.puck.y, 4.5 * k, 0, Math.PI * 2);
    ctx.fillStyle = "#050505";
    ctx.fill();
  }

  function clockText(state) {
    if (state.period >= 5) return "SO";
    const base = (state.period - 1) * 20;
    const len = state.period === 4 ? 5 : 20;
    const left = Math.max(0, len - (state.gameMin - base));
    const m = Math.floor(left), s = Math.floor((left - m) * 60);
    return `${m}:${String(s).padStart(2, "0")}`;
  }

  function updateBoard(state, g) {
    $("#sbAway .sb-score").textContent = state.score.away;
    $("#sbHome .sb-score").textContent = state.score.home;
    $("#sbAway .sb-shots").textContent = `SOG ${state.shots.away}`;
    $("#sbHome .sb-shots").textContent = `SOG ${state.shots.home}`;
    $("#sbPeriod").textContent = PERIOD_NAMES[Math.min(state.period, 5) - 1];
    $("#sbClock").textContent = state.done ? "FINAL" : clockText(state);
  }

  function flash(text, color) {
    const el = $("#goalFlash");
    el.textContent = text;
    el.style.setProperty("--team", color);
    el.hidden = false;
  }

  function feed(state, g, ev) {
    const per = ev.t >= 65 ? "SO" : ev.t >= 60 ? "OT" : PERIOD_NAMES[Math.floor(ev.t / 20)];
    const inPer = ev.t >= 60 ? ev.t - 60 : ev.t % 20;
    const m = Math.floor(inPer), s = Math.floor((inPer - m) * 60);
    const li = document.createElement("li");
    li.innerHTML = `<span class="feed-time">${per} ${ev.kind === "so" ? "" : `${m}:${String(s).padStart(2, "0")}`}</span>
      <img src="${logo(g[ev.side])}" alt="" width="28" height="20"> <b>${esc(g[ev.side])}</b> ${esc(ev.scorer)}
      <span class="feed-score">${state.score.away}-${state.score.home}</span>`;
    $("#simFeed").appendChild(li);
  }

  function finish(state, g) {
    anim = null;
    state.done = true;
    // make sure the board shows the simulated final exactly
    state.score = { ...g.shown.score };
    state.shots = { ...g.shown.shots };
    updateBoard(state, g);
    $("#sbPeriod").textContent = g.shown.end === "REG" ? "" : g.shown.end;
    $("#skipBtn").hidden = true;
    $("#replayBtn").hidden = false;
    showResult(g);
  }

  function showResult(g) {
    const w = g.wins.home >= g.wins.away ? "home" : "away";
    const l = w === "home" ? "away" : "home";
    const runs = g.runs.map((x) => {
      const win = x.home > x.away ? "home" : "away";
      return `<span class="run ${win === w ? "w" : "l"}">${esc(g.away)} ${x.away}-${x.home} ${esc(g.home)}${x.end === "REG" ? "" : " " + x.end}</span>`;
    }).join("");
    $("#simResult").innerHTML = `<h3>${esc(g[w])} wins ${g.wins[w]} of 10 simulations</h3>
      <p class="muted">You just watched the most common result for ${esc(g[w])}:
        <b>${esc(g.away)} ${g.shown.score.away} - ${g.shown.score.home} ${esc(g.home)}${g.shown.end === "REG" ? "" : ` (${g.shown.end})`}</b>.
        ${esc(g[l])} won the other ${g.wins[l]}.</p>
      <div class="runs">${runs}</div>`;
    $("#simResult").hidden = false;
  }

  function skip() {
    if (!current) return;
    stop();
    const g = current;
    const state = { score: { away: 0, home: 0 }, shots: g.shown.shots, period: 3, done: true };
    $("#simFeed").innerHTML = "";
    for (const e of g.shown.events) {
      state.score[e.side]++;
      feed(state, g, e.shootout ? { t: 65, side: e.side, scorer: "Shootout", kind: "so" } : { ...e, kind: "goal" });
    }
    finish({ ...state }, g);
    drawStatic();
  }

  // -------------------------------------------------------------- events
  $("#simGames").addEventListener("click", (e) => {
    const b = e.target.closest("[data-game]");
    if (b) openGame(b.dataset.game);
  });
  $("#simBack").addEventListener("click", () => { showList(); $("#simStatus").hidden = false; });
  $("#playBtn").addEventListener("click", play);
  $("#replayBtn").addEventListener("click", play);
  $("#skipBtn").addEventListener("click", skip);
  $("#date").addEventListener("change", (e) => load(e.target.value));

  $("#date").value = todayET();
  load($("#date").value);
})();
