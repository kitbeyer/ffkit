(() => {
  const STORAGE_KEY = "ffkit_sleeper_user";
  const THEME_KEY = "ffkit_theme";

  const state = {
    user: null,
    nflState: { season: 2026, week: 11 },
    season: 2026,
    week: 11,
    leagues: [],
    selectedLeagueId: null,
    scoreboard: null,
    selectedMatchupId: null,
    autoRefresh: true,
    refreshTimer: null,
    benchExpanded: true,
    leagueMenuOpen: false,
  };

  // DOM Elements
  const authOverlay = document.getElementById("auth-overlay");
  const appShell = document.getElementById("app-shell");
  const userForm = document.getElementById("user-form");
  const userInput = document.getElementById("user-input");
  const userSubmitBtn = document.getElementById("user-submit-btn");
  const authSeasonSelect = document.getElementById("auth-season");
  const authError = document.getElementById("auth-error");

  const userChip = document.getElementById("user-chip");
  const userAvatar = document.getElementById("user-avatar");
  const userInitials = document.getElementById("user-initials");
  const userDisplayName = document.getElementById("user-display-name");
  const seasonSelect = document.getElementById("season-select");
  const weekSelect = document.getElementById("week-select");
  const autoRefreshBtn = document.getElementById("auto-refresh-btn");
  const refreshBtn = document.getElementById("refresh-btn");
  const themeToggleBtn = document.getElementById("theme-toggle-btn");
  const lastUpdatedEl = document.getElementById("last-updated");

  const leaguesCountEl = document.getElementById("leagues-count");
  const leagueSwitcherWrapEl = document.getElementById("league-switcher-wrap");
  const currentLeagueSlotEl = document.getElementById("current-league-slot");
  const leaguesDropdownEl = document.getElementById("leagues-dropdown");
  const scoreboardHeadingEl = document.getElementById("scoreboard-heading");
  const matchupsCountEl = document.getElementById("matchups-count");
  const activeLeagueTitleEl = document.getElementById("active-league-title");
  const activeLeagueBadgesEl = document.getElementById("active-league-badges");
  const matchupsStripEl = document.getElementById("matchups-strip");
  const matchupDetailEl = document.getElementById("matchup-detail");

  // Helpers
  function esc(str) {
    return String(str ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function initials(name) {
    const parts = String(name || "?").trim().split(/\s+/);
    if (parts.length >= 2) return (parts[0][0] + parts[1][0]).toUpperCase();
    return String(name || "?").slice(0, 2).toUpperCase();
  }

  function fmt(num, digits = 2) {
    return Number(num || 0).toFixed(digits);
  }

  function fmtRecord(w, l, t) {
    return t > 0 ? `${w}-${l}-${t}` : `${w}-${l}`;
  }

  function fmtDelta(delta) {
    const val = Number(delta || 0);
    if (Math.abs(val) < 0.005) return { text: "0.00", cls: "flat", textCls: "delta-flat" };
    if (val > 0) return { text: `+${val.toFixed(2)}`, cls: "pos", textCls: "delta-pos" };
    return { text: val.toFixed(2), cls: "neg", textCls: "delta-neg" };
  }

  function renderAvatar(url, name, sizeClass) {
    const fb = initials(name);
    if (!url) {
      return `<div class="${sizeClass} avatar-fallback">${esc(fb)}</div>`;
    }
    return `<img class="${sizeClass}" src="${esc(url)}" alt="${esc(name)}" onerror="this.outerHTML='<div class=\\'${sizeClass} avatar-fallback\\'>${esc(fb)}</div>'" />`;
  }

  function populateSelects(currentSeason, currentWeek) {
    const seasons = [];
    for (let y = currentSeason + 1; y >= currentSeason - 4; y--) {
      seasons.push(y);
    }
    const seasonOptions = seasons
      .map((y) => `<option value="${y}" ${y === state.season ? "selected" : ""}>${y}</option>`)
      .join("");
    authSeasonSelect.innerHTML = seasonOptions;
    seasonSelect.innerHTML = seasonOptions;

    const weekOptions = [];
    for (let w = 1; w <= 18; w++) {
      weekOptions.push(`<option value="${w}" ${w === state.week ? "selected" : ""}>Week ${w}</option>`);
    }
    weekSelect.innerHTML = weekOptions.join("");
  }

  async function fetchJson(url) {
    const res = await fetch(url);
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      throw new Error(data.error || `Request failed (${res.status})`);
    }
    return data;
  }

  function showAuthOverlay(errorMsg = "") {
    authOverlay.classList.remove("hidden");
    if (errorMsg) {
      authError.textContent = errorMsg;
      authError.classList.remove("hidden");
    } else {
      authError.classList.add("hidden");
    }
    if (state.user) {
      userInput.value = state.user.username || state.user.user_id;
    }
    setTimeout(() => userInput.focus(), 50);
  }

  function hideAuthOverlay() {
    authOverlay.classList.add("hidden");
    appShell.classList.remove("hidden");
  }

  // Load user + leagues
  async function loadUser(query, seasonOverride = null) {
    authError.classList.add("hidden");
    userSubmitBtn.disabled = true;
    userSubmitBtn.textContent = "Loading...";

    try {
      const seasonParam = seasonOverride ? `&season=${encodeURIComponent(seasonOverride)}` : "";
      const data = await fetchJson(`/api/user?q=${encodeURIComponent(query)}${seasonParam}`);
      state.user = data.user;
      state.nflState = data.nfl_state || state.nflState;
      state.season = data.season;
      if (!state.week) state.week = state.nflState.week || 1;
      state.leagues = data.leagues || [];

      localStorage.setItem(STORAGE_KEY, data.user.username || data.user.user_id);

      // Update header user chip
      userDisplayName.textContent = data.user.display_name;
      if (data.user.avatar_url) {
        userAvatar.src = data.user.avatar_url;
        userAvatar.classList.remove("hidden");
        userInitials.classList.add("hidden");
      } else {
        userAvatar.classList.add("hidden");
        userInitials.textContent = initials(data.user.display_name);
        userInitials.classList.remove("hidden");
      }

      populateSelects(state.nflState.season, state.week);
      hideAuthOverlay();
      renderLeagues();

      if (state.leagues.length > 0) {
        const keepCurrent = state.leagues.find((l) => l.league_id === state.selectedLeagueId);
        const target = keepCurrent || state.leagues[0];
        if (!keepCurrent && target.current_leg) {
          state.week = target.current_leg;
          weekSelect.value = String(state.week);
        }
        await selectLeague(target.league_id, false);
      } else {
        state.selectedLeagueId = null;
        state.scoreboard = null;
        activeLeagueTitleEl.textContent = `No NFL leagues found for ${state.season}`;
        activeLeagueBadgesEl.innerHTML = "";
        matchupsStripEl.innerHTML = "";
        matchupDetailEl.innerHTML = `<div class="empty-state">No leagues found for ${esc(data.user.display_name)} in ${state.season}. Try switching the season above.</div>`;
      }
    } catch (err) {
      showAuthOverlay(err.message);
    } finally {
      userSubmitBtn.disabled = false;
      userSubmitBtn.textContent = "Load Leagues";
    }
  }

  function renderLeagueTileHtml(lg, { isCurrentTrigger = false, isSelected = false } = {}) {
    const ut = lg.user_team;
    const recordStr = ut ? fmtRecord(ut.wins, ut.losses, ut.ties) : "—";
    const rankStr = ut && ut.rank ? `#${ut.rank}/${lg.total_rosters}` : `${lg.total_rosters}T`;
    const streakHtml = ut && ut.streak
      ? `<span class="${ut.streak.includes("W") ? "streak-w" : "streak-l"}">${esc(ut.streak)}</span>`
      : "";
    const chevronHtml = isCurrentTrigger && state.leagues.length > 1
      ? `<span class="league-chevron" title="Switch league">${state.leagueMenuOpen ? "▴" : "▾"}</span>`
      : "";

    return `
      <div class="league-tile ${isSelected ? "selected" : ""} ${isCurrentTrigger ? "current-trigger" : ""}" data-league-id="${esc(lg.league_id)}">
        <div class="league-tile-top">
          ${renderAvatar(lg.avatar_url, lg.name, "avatar-md")}
          <div class="league-tile-info">
            <div class="league-tile-title-row">
              <div class="league-tile-name" title="${esc(lg.name)}">${esc(lg.name)}</div>
              ${chevronHtml}
            </div>
            <div class="badge-row">
              ${lg.is_best_ball ? `<span class="pill pill-bestball">Best Ball</span>` : ""}
              <span class="pill">${esc(lg.league_type)}</span>
              <span class="pill">${esc(lg.scoring_label)}</span>
            </div>
          </div>
        </div>
        <div class="league-tile-stats">
          <span><span class="league-record">${esc(recordStr)}</span> · ${esc(rankStr)} ${streakHtml}</span>
          <span>${ut ? `${fmt(ut.fpts, 1)} PF` : ""}</span>
        </div>
      </div>
    `;
  }

  // Render leftmost pane top: Current League Tile + Click-to-Change Dropdown
  function renderLeagues() {
    leaguesCountEl.textContent = String(state.leagues.length);
    if (state.leagues.length === 0) {
      currentLeagueSlotEl.innerHTML = `<div class="empty-state" style="padding:14px;">No leagues for ${state.season}</div>`;
      leaguesDropdownEl.innerHTML = "";
      leaguesDropdownEl.classList.add("hidden");
      return;
    }

    const activeLeague = state.leagues.find((l) => l.league_id === state.selectedLeagueId) || state.leagues[0];
    currentLeagueSlotEl.innerHTML = renderLeagueTileHtml(activeLeague, { isCurrentTrigger: true, isSelected: true });

    const triggerEl = currentLeagueSlotEl.querySelector(".league-tile");
    if (triggerEl && state.leagues.length > 1) {
      triggerEl.addEventListener("click", (e) => {
        e.stopPropagation();
        state.leagueMenuOpen = !state.leagueMenuOpen;
        renderLeagues();
      });
    }

    if (state.leagueMenuOpen && state.leagues.length > 1) {
      leaguesDropdownEl.classList.remove("hidden");
      leaguesDropdownEl.innerHTML = state.leagues
        .map((lg) => renderLeagueTileHtml(lg, { isCurrentTrigger: false, isSelected: lg.league_id === state.selectedLeagueId }))
        .join("");

      leaguesDropdownEl.querySelectorAll(".league-tile").forEach((el) => {
        el.addEventListener("click", (e) => {
          e.stopPropagation();
          const lid = el.getAttribute("data-league-id");
          state.leagueMenuOpen = false;
          if (lid !== state.selectedLeagueId) {
            selectLeague(lid, false);
          } else {
            renderLeagues();
          }
        });
      });
    } else {
      leaguesDropdownEl.classList.add("hidden");
    }
  }

  // Select a league and fetch its live scoreboard
  async function selectLeague(leagueId, forceRefresh = false) {
    const leagueChanged = state.selectedLeagueId !== leagueId;
    state.selectedLeagueId = leagueId;
    renderLeagues();

    if (leagueChanged || !state.scoreboard) {
      matchupsStripEl.innerHTML = `<div class="loading-state" style="padding: 14px;"><div class="spinner"></div></div>`;
      matchupDetailEl.innerHTML = `<div class="loading-state"><div class="spinner"></div><div>Loading live scores &amp; projections...</div></div>`;
    }

    try {
      const uid = state.user ? `&user_id=${encodeURIComponent(state.user.user_id)}` : "";
      const ref = forceRefresh ? "&refresh=1" : "";
      const url = `/api/league/${encodeURIComponent(leagueId)}/scoreboard?season=${state.season}&week=${state.week}${uid}${ref}`;
      const data = await fetchJson(url);

      // Guard against race if user switched leagues while fetching
      if (state.selectedLeagueId !== leagueId) return;

      state.scoreboard = data;
      const now = new Date();
      lastUpdatedEl.textContent = now.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });

      // Pick matchup: preserve selection if same league & still exists, else user's matchup (sorted first)
      const matchups = data.matchups || [];
      const existing = !leagueChanged && matchups.find((m) => m.matchup_id === state.selectedMatchupId);
      state.selectedMatchupId = existing ? existing.matchup_id : (matchups[0]?.matchup_id ?? null);

      renderScoreboardHeader();
      renderMatchupStrip();
      renderSelectedMatchup();
    } catch (err) {
      matchupDetailEl.innerHTML = `<div class="empty-state">Error loading scoreboard: ${esc(err.message)}</div>`;
    }
  }

  function renderScoreboardHeader() {
    const lg = state.scoreboard?.league;
    if (!lg) return;
    activeLeagueTitleEl.textContent = `${lg.name} · Week ${lg.week}`;
    activeLeagueBadgesEl.innerHTML = `
      ${lg.is_best_ball ? `<span class="pill pill-bestball">Best Ball</span>` : ""}
      <span class="pill">${esc(lg.league_type)}</span>
      <span class="pill">${esc(lg.scoring_label)}</span>
      <span class="pill">${lg.total_rosters} Teams</span>
    `;
  }

  // Render left-pane scoreboard: traditional stacked sports-app matchup cards
  function renderMatchupStrip() {
    const matchups = state.scoreboard?.matchups || [];
    scoreboardHeadingEl.textContent = `Week ${state.week} Scoreboard`;
    matchupsCountEl.textContent = String(matchups.length);

    if (matchups.length === 0) {
      matchupsStripEl.innerHTML = `<div class="empty-state" style="padding:16px;">No matchups scheduled for Week ${state.week}.</div>`;
      return;
    }

    matchupsStripEl.innerHTML = matchups
      .map((m) => {
        const selected = m.matchup_id === state.selectedMatchupId ? "selected" : "";
        const [t1, t2] = m.teams;
        const anyLive = m.teams.some((t) => (t.totals?.active_count || 0) > 0);
        const winA = t1 ? t1.win_prob : 50;
        const winB = t2 ? t2.win_prob : 50;

        const renderScoreboardRow = (t) => {
          if (!t) {
            return `<div class="sb-team-row"><div class="sb-team-id"><span class="sb-team-rec">BYE</span></div></div>`;
          }
          return `
            <div class="sb-team-row">
              <div class="sb-team-id">
                ${renderAvatar(t.avatar_url, t.team_name, "avatar-xs")}
                <div class="sb-team-meta">
                  <div class="sb-team-name" title="${esc(t.team_name)}">${esc(t.team_name)}</div>
                  <div class="sb-team-rec">${fmtRecord(t.wins, t.losses, t.ties)}${t.rank ? ` · #${t.rank}` : ""}</div>
                </div>
              </div>
              <div class="sb-team-scores">
                <span class="sb-live" title="Live projection">${fmt(t.totals.live_proj, 1)}</span>
                <span class="sb-actual" title="Actual score">${fmt(t.totals.actual)}</span>
              </div>
            </div>
          `;
        };

        return `
          <div class="scoreboard-card ${selected}" data-matchup-id="${m.matchup_id}">
            ${renderScoreboardRow(t1)}
            ${renderScoreboardRow(t2)}
          </div>
        `;
      })
      .join("");

    matchupsStripEl.querySelectorAll(".scoreboard-card").forEach((el) => {
      el.addEventListener("click", () => {
        state.selectedMatchupId = Number(el.getAttribute("data-matchup-id"));
        renderMatchupStrip();
        renderSelectedMatchup();
        if (window.innerWidth <= 768) {
          matchupDetailEl.scrollIntoView({ behavior: "smooth", block: "start" });
        }
      });
    });
  }

  // Render detailed matchup view in right pane
  function renderSelectedMatchup() {
    const matchups = state.scoreboard?.matchups || [];
    const matchup = matchups.find((m) => m.matchup_id === state.selectedMatchupId);
    if (!matchup) {
      matchupDetailEl.innerHTML = `<div class="empty-state">Select a matchup from the scoreboard to view details.</div>`;
      return;
    }

    const lg = state.scoreboard.league;
    const [t1, t2] = matchup.teams;

    const renderHeroTeam = (t, side) => {
      if (!t) return `<div class="hero-team ${side}"><div class="hero-team-name">BYE</div></div>`;
      const delta = fmtDelta(t.totals.delta_vs_pregame);
      const streakHtml = t.streak
        ? ` · <span class="${t.streak.includes("W") ? "streak-w" : "streak-l"}">${esc(t.streak)}</span>`
        : "";

      return `
        <div class="hero-team ${side}">
          <div class="hero-team-info">
            <div class="hero-team-id">
              ${renderAvatar(t.avatar_url, t.team_name, "avatar-lg")}
              <div style="min-width:0;">
                <div class="hero-team-name">
                  ${esc(t.team_name)}
                </div>
                <div class="hero-team-meta">
                  ${t.rank ? `#${t.rank} · ` : ""}${fmtRecord(t.wins, t.losses, t.ties)}${streakHtml}
                </div>
                <div class="hero-team-owner">@${esc(t.owner_name)}</div>
              </div>
            </div>

            ${t.totals.active_count > 0 ? `
              <div class="hero-status-counts">
                <span class="pill pill-live"><span class="pulse-dot"></span> ${t.totals.active_count} Active</span>
              </div>
            ` : ""}
          </div>

          <div class="hero-score-col">
            <div class="hero-actual" title="Actual score">${fmt(t.totals.actual)}</div>
            <div class="hero-proj-row">
              <span class="delta-chip ${delta.cls}" title="Live projection vs Pre-game">${delta.text}</span>
              <span class="hero-live" title="Live projection">${fmt(t.totals.live_proj)}</span>
            </div>
          </div>
        </div>
      `;
    };

    // Align starters row by row
    const s1 = t1?.starters || [];
    const s2 = t2?.starters || [];
    const starterRowsCount = Math.max(s1.length, s2.length);
    const starterRowsHtml = [];
    for (let i = 0; i < starterRowsCount; i++) {
      const p1 = s1[i] || null;
      const p2 = s2[i] || null;
      const slot = p1?.slot || p2?.slot || lg.starter_slots[i] || "ST";
      const slotKey = slot.toUpperCase().replace(/[^A-Z0-9_]/g, "");
      const slotCls = `slot-${slotKey}`;
      starterRowsHtml.push(`
        <div class="lineup-row">
          ${renderPlayerCard(p1, "left")}
          <div class="slot-center ${slotCls}">${esc(slot.replace("_", " "))}</div>
          ${renderPlayerCard(p2, "right")}
        </div>
      `);
    }

    // Align bench row by row
    const b1 = t1?.bench || [];
    const b2 = t2?.bench || [];
    const benchRowsCount = Math.max(b1.length, b2.length);
    const benchRowsHtml = [];
    for (let i = 0; i < benchRowsCount; i++) {
      benchRowsHtml.push(`
        <div class="lineup-row">
          ${renderPlayerCard(b1[i] || null, "left")}
          <div class="slot-center slot-bn">BN</div>
          ${renderPlayerCard(b2[i] || null, "right")}
        </div>
      `);
    }

    const winA = t1 ? t1.win_prob : 50;
    const winB = t2 ? t2.win_prob : 50;

    matchupDetailEl.innerHTML = `
      <div class="hero-card">
        <div class="hero-teams">
          ${renderHeroTeam(t1, "left")}
          <div class="hero-vs">
            <span class="vs-badge">VS</span>
          </div>
          ${renderHeroTeam(t2, "right")}
        </div>
        ${t1 && t2 ? `
          <div class="hero-win-wrap">
            <div class="hero-win-row">
              <span class="hero-win-pct" style="color:var(--team-a);">${winA.toFixed(1)}%</span>
              <div class="hero-win-bar">
                <div class="win-bar-a" style="width:${winA}%"></div>
                <div class="win-bar-b" style="width:${winB}%"></div>
              </div>
              <span class="hero-win-pct" style="color:var(--team-b);">${winB.toFixed(1)}%</span>
            </div>
          </div>
        ` : ""}
      </div>

      <div class="section-bar">
        <h3>${lg.is_best_ball ? "Optimal Starters (Max Live Projection)" : "Starters"}</h3>
        <span style="font-size:11px;color:var(--text-muted);">Showing Actual · Live Proj · +/- vs Pre-Game</span>
      </div>
      <div class="lineup-grid">
        ${starterRowsHtml.join("")}
      </div>

      ${benchRowsCount > 0 ? `
        <div class="section-bar">
          <h3>Bench (${b1.length} / ${b2.length}) ${lg.is_best_ball ? "· Sorted by Live Projection" : ""}</h3>
          <button id="toggle-bench-btn" class="btn-secondary">${state.benchExpanded ? "Hide Bench" : "Show Bench"}</button>
        </div>
        <div id="bench-grid" class="lineup-grid ${state.benchExpanded ? "" : "hidden"}">
          ${benchRowsHtml.join("")}
        </div>
      ` : ""}
    `;

    const toggleBenchBtn = document.getElementById("toggle-bench-btn");
    if (toggleBenchBtn) {
      toggleBenchBtn.addEventListener("click", () => {
        state.benchExpanded = !state.benchExpanded;
        document.getElementById("bench-grid")?.classList.toggle("hidden", !state.benchExpanded);
        toggleBenchBtn.textContent = state.benchExpanded ? "Hide Bench" : "Show Bench";
      });
    }
  }

  function renderPlayerCard(p, side) {
    if (!p) {
      return `<div class="player-card empty-slot ${side}">—</div>`;
    }

    const isInactive = Boolean(p.inj_status && ["Out", "IR", "PUP", "COV", "Sus", "NA"].includes(p.inj_status));
    let stateCls = "";
    if (p.is_active) {
      stateCls = "active-player";
    } else if (p.game_state === "post" || p.game_state === "bye" || isInactive) {
      stateCls = "post-player";
    }
    const delta = fmtDelta(p.delta_vs_pregame);
    const maxScale = Math.max(p.pregame, p.live_proj, p.actual, 1);
    const actualPct = Math.max(0, Math.min(100, (p.actual / maxScale) * 100));
    const livePct = Math.max(0, Math.min(100, (p.live_proj / maxScale) * 100));

    let statusHtml = "";
    if (p.is_active) {
      const clockText = [p.game_detail, p.game_score && p.opp_abbr ? `${p.raw_team} ${p.game_score} ${p.opp_abbr}` : ""]
        .filter(Boolean)
        .join(" · ");
      statusHtml = `<span class="pill pill-live"><span class="pulse-dot"></span> LIVE</span><span class="game-clock-live">${esc(clockText)}</span>`;
    } else if (isInactive) {
      statusHtml = `<span class="pill pill-danger">${esc(p.inj_status)}</span>`;
    } else if (p.game_state === "post") {
      const scoreText = p.game_score && p.opp_abbr ? `Final (${p.raw_team} ${p.game_score} ${p.opp_abbr})` : "Final";
      statusHtml = `<span class="game-clock-muted">${esc(scoreText)}</span>`;
    } else if (p.game_state === "pre") {
      const preText = p.opp_abbr ? `vs ${p.opp_abbr}${p.game_detail ? ` · ${p.game_detail}` : ""}` : (p.game_detail || "");
      statusHtml = `<span class="game-clock-muted">${esc(preText)}</span>`;
    } else {
      statusHtml = `<span class="game-clock-muted">BYE</span>`;
    }

    const injBadge = p.inj_status && !isInactive
      ? `<span class="pill pill-warn">${esc(p.inj_status)}</span>`
      : "";

    const blowoutBadge = p.b_mult < 1.0
      ? `<span class="pill pill-warn" title="4th quarter blowout dampener">Blowout ×${p.b_mult.toFixed(2)}</span>`
      : "";

    const fb = initials(p.name);

    return `
      <div class="player-card ${side} ${stateCls}">
        <div class="player-main">
          <img
            class="player-headshot"
            src="${esc(p.headshot_url)}"
            alt="${esc(p.name)}"
            loading="lazy"
            onerror="this.outerHTML='<div class=\\'player-headshot avatar-fallback\\'>${esc(fb)}</div>'"
          />
          <div class="player-info">
            <div class="player-name-row">
              <span class="player-name" title="${esc(p.name)}">${esc(p.name)}</span>
              ${injBadge}
              ${blowoutBadge}
            </div>
            <div class="player-sub">
              <span class="pos-tag pos-${esc(p.pos)}">${esc(p.pos || "?")}</span>
              <span>${esc(p.raw_team || "FA")}</span>
              ${statusHtml}
            </div>
          </div>
        </div>

        <div class="player-scores">
          <div class="score-row-top">
            <span class="score-actual" title="Actual points">${fmt(p.actual)}</span>
          </div>
          <div class="score-row-bottom">
            <span class="delta-chip ${delta.cls}" title="Live projection vs Pre-game projection">${delta.text}</span>
            <span class="score-live" title="Current live projection">${fmt(p.live_proj)}</span>
          </div>
          <div class="proj-bar-track" title="Actual (${fmt(p.actual)}) vs Live (${fmt(p.live_proj)})">
            <div class="proj-bar-live" style="width:${livePct.toFixed(1)}%"></div>
            <div class="proj-bar-actual" style="width:${actualPct.toFixed(1)}%"></div>
          </div>
        </div>
      </div>
    `;
  }

  // Polling
  function setupAutoRefresh() {
    if (state.refreshTimer) clearInterval(state.refreshTimer);
    if (state.autoRefresh) {
      state.refreshTimer = setInterval(() => {
        if (state.selectedLeagueId && !document.hidden) {
          selectLeague(state.selectedLeagueId, false);
        }
      }, 30000);
    }
  }

  // Event Listeners
  userForm.addEventListener("submit", (e) => {
    e.preventDefault();
    const q = userInput.value.trim();
    const chosenSeason = Number(authSeasonSelect.value) || state.nflState.season;
    if (q) loadUser(q, chosenSeason);
  });

  userChip.addEventListener("click", () => showAuthOverlay());

  seasonSelect.addEventListener("change", () => {
    state.season = Number(seasonSelect.value);
    if (state.user) {
      loadUser(state.user.username || state.user.user_id, state.season);
    }
  });

  weekSelect.addEventListener("change", () => {
    state.week = Number(weekSelect.value);
    if (state.selectedLeagueId) {
      selectLeague(state.selectedLeagueId, false);
    }
  });

  autoRefreshBtn.addEventListener("click", () => {
    state.autoRefresh = !state.autoRefresh;
    autoRefreshBtn.classList.toggle("active", state.autoRefresh);
    setupAutoRefresh();
  });

  refreshBtn.addEventListener("click", () => {
    if (state.selectedLeagueId) {
      selectLeague(state.selectedLeagueId, true);
    }
  });

  function applyTheme(theme) {
    const next = theme === "light" ? "light" : "dark";
    document.documentElement.setAttribute("data-theme", next);
    localStorage.setItem(THEME_KEY, next);
    if (themeToggleBtn) {
      themeToggleBtn.textContent = next === "light" ? "🌙 Dark" : "☀️ Light";
    }
  }

  if (themeToggleBtn) {
    themeToggleBtn.addEventListener("click", () => {
      const current = document.documentElement.getAttribute("data-theme") || "light";
      applyTheme(current === "dark" ? "light" : "dark");
    });
  }

  document.addEventListener("click", (e) => {
    if (state.leagueMenuOpen && leagueSwitcherWrapEl && !leagueSwitcherWrapEl.contains(e.target)) {
      state.leagueMenuOpen = false;
      renderLeagues();
    }
  });

  // Boot
  async function init() {
    applyTheme(localStorage.getItem(THEME_KEY) || "light");
    try {
      const nflState = await fetchJson("/api/nfl-state");
      state.nflState = nflState;
      state.season = nflState.season;
      state.week = nflState.week;
    } catch {
      // Fallback to defaults if /api/nfl-state fails
    }
    populateSelects(state.season, state.week);
    setupAutoRefresh();

    const savedUser = localStorage.getItem(STORAGE_KEY);
    if (savedUser) {
      userInput.value = savedUser;
      await loadUser(savedUser, state.season);
    } else {
      showAuthOverlay();
    }
  }

  init();
})();
