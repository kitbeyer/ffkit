import json
import logging
import math
import os
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import requests

CACHE_DIR = "./sleeper_cache"
_MEM_CACHE: dict[str, tuple[float, Any]] = {}
TEAM_ABBR_MAP = {"JAC": "JAX", "WAS": "WSH"}
INACTIVE_STATUSES = {"Out", "IR", "PUP", "COV", "Sus", "NA"}
NON_STARTER_SLOTS = {"BN", "IR", "TAXI"}
SLOT_ELIGIBILITY: dict[str, set] = {
    "QB": {"QB"},
    "RB": {"RB"},
    "WR": {"WR"},
    "TE": {"TE"},
    "K": {"K"},
    "DEF": {"DEF"},
    "DL": {"DL"},
    "LB": {"LB"},
    "DB": {"DB"},
    "FLEX": {"RB", "WR", "TE"},
    "SUPER_FLEX": {"QB", "RB", "WR", "TE"},
    "REC_FLEX": {"WR", "TE"},
    "WRRB_FLEX": {"RB", "WR"},
    "IDP_FLEX": {"DL", "LB", "DB"},
}

SLEEPER_URL = 'https://api.sleeper.app/'

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)

def load_or_fetch_cache(filename: str, url: str, params: Any = None, ttl_seconds: int = 86400) -> Any:
    """Caches large or static API responses locally (and in memory) to allow fast live polling."""
    os.makedirs(CACHE_DIR, exist_ok=True)
    filepath = os.path.join(CACHE_DIR, filename)

    if os.path.exists(filepath):
        mtime = os.path.getmtime(filepath)
        age = time.time() - mtime
        if age < ttl_seconds:
            mem = _MEM_CACHE.get(filename)
            if mem and mem[0] == mtime:
                return mem[1]
            with open(filepath, "r", encoding="utf-8") as f:
                data = json.load(f)
            _MEM_CACHE[filename] = (mtime, data)
            return data

    resp = requests.get(url, params=params, timeout=20)
    resp.raise_for_status()
    data = resp.json()

    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f)
    _MEM_CACHE[filename] = (os.path.getmtime(filepath), data)
    return data


def get_nfl_game_clocks(
    season: int | None = None,
    week: int | None = None,
) -> dict[str, dict[str, Any]]:
    """Fetches NFL game states, clocks, and team scores from ESPN's public scoreboard for a given season/week."""
    url = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard"
    params: dict[str, Any] = {}
    if season is not None:
        params["dates"] = season
    if week is not None:
        params["seasontype"] = 2
        params["week"] = week
    resp = requests.get(url, params=params or None, timeout=10)
    resp.raise_for_status()
    data = resp.json()

    clocks = {}
    for event in data.get("events", []):
        status = event.get("status", {})
        status_type = status.get("type", {})
        state = status_type.get("state", "pre")
        period = status.get("period", 1)
        clock_sec = int(status.get("clock", 0))
        display_clock = status.get("displayClock", "")
        short_detail = status_type.get("shortDetail", "")

        for comp in event.get("competitions", []):
            competitors = comp.get("competitors", [])
            if len(competitors) != 2:
                continue

            team_scores = []
            for c in competitors:
                abbr = c.get("team", {}).get("abbreviation")
                score = int(c.get("score") or 0)
                if abbr:
                    team_scores.append((abbr, score))

            if len(team_scores) == 2:
                (abbr1, score1), (abbr2, score2) = team_scores[0], team_scores[1]
                clocks[abbr1] = {
                    "state": state,
                    "period": period,
                    "clock": clock_sec,
                    "display_clock": display_clock,
                    "short_detail": short_detail,
                    "team_score": score1,
                    "opp_score": score2,
                    "opp_abbr": abbr2,
                }
                clocks[abbr2] = {
                    "state": state,
                    "period": period,
                    "clock": clock_sec,
                    "display_clock": display_clock,
                    "short_detail": short_detail,
                    "team_score": score2,
                    "opp_score": score1,
                    "opp_abbr": abbr1,
                }
    return clocks


def get_blowout_multiplier(game_info: dict[str, Any], position: str) -> float:
    """
    Applies a 4th-quarter blowout dampener based on score margin and player position.
    Returns a multiplier between 0.30 and 1.00.
    """
    period = game_info.get("period", 1)
    if period != 4:
        return 1.0

    margin = game_info.get("team_score", 0) - game_info.get("opp_score", 0)
    abs_margin = abs(margin)

    # Competitive game (within 2 scores) -> no dampening
    if abs_margin < 17:
        return 1.0

    is_leading = margin > 0

    # Massive blowout (24+ pts): starters frequently benched on both sides
    if abs_margin >= 24:
        if is_leading:
            return 0.50 if position == "RB" else 0.30
        return 0.45

    # Moderate blowout (17–23 pts): leading team milks clock, trailing team still passes
    if is_leading:
        return 0.85 if position == "RB" else 0.55
    return 0.90


def calculate_custom_points(stats: dict[str, float], scoring: dict[str, float]) -> float:
    """Calculates fantasy points using the league's custom scoring settings."""
    return sum(val * scoring[key] for key, val in stats.items() if key in scoring)


def calculate_live_projection(
    pregame_proj: float,
    actual_pts: float,
    game_info: dict[str, Any],
    injury_status: str,
    position: str,
) -> tuple[float, float, float]:
    """
    Returns (live_projection, remaining_projection, blowout_multiplier).
    """
    if injury_status in INACTIVE_STATUSES:
        return round(actual_pts, 2), 0.0, 1.0

    if not game_info:
        return round(pregame_proj, 2), round(pregame_proj, 2), 1.0

    state = game_info["state"]
    period = game_info["period"]
    clock_sec = game_info["clock"]

    if state == "pre":
        return round(pregame_proj, 2), round(pregame_proj, 2), 1.0
    if state == "post" or period > 4:
        return round(actual_pts, 2), 0.0, 1.0

    remaining_sec = ((4 - period) * 900) + clock_sec
    remaining_ratio = max(0.0, min(1.0, remaining_sec / 3600.0))

    blowout_mult = get_blowout_multiplier(game_info, position)
    remaining_proj = pregame_proj * remaining_ratio * blowout_mult
    live_proj = actual_pts + remaining_proj

    return round(live_proj, 2), round(remaining_proj, 2), blowout_mult


def calculate_win_probability(
    proj_a: float, rem_a: float, proj_b: float, rem_b: float
) -> tuple[float, float]:
    """Computes live win probability using a normal distribution over remaining points."""
    diff = proj_a - proj_b
    combined_std_dev = math.sqrt((rem_a * 2.5) + (rem_b * 2.5))

    if combined_std_dev <= 0.01:
        if diff > 0:
            return 100.0, 0.0
        elif diff < 0:
            return 0.0, 100.0
        return 50.0, 50.0

    z_score = diff / combined_std_dev
    prob_a = 0.5 * (1.0 + math.erf(z_score / math.sqrt(2.0)))
    prob_a_pct = round(prob_a * 100, 1)
    return prob_a_pct, round(100.0 - prob_a_pct, 1)


def _hungarian_min_cost(cost: list[list[int]]) -> list[int]:
    """
    Solves the rectangular linear assignment problem (minimization) using exact integer costs.
    cost is an n x m matrix with n <= m.
    Returns a list of length n where assignment[i] is the 0-based column assigned to row i.
    """
    n = len(cost)
    if n == 0:
        return []
    m = len(cost[0])
    inf = 10**18
    u = [0] * (n + 1)
    v = [0] * (m + 1)
    p = [0] * (m + 1)
    way = [0] * (m + 1)

    for i in range(1, n + 1):
        p[0] = i
        j0 = 0
        minv = [inf] * (m + 1)
        used = [False] * (m + 1)
        while True:
            used[j0] = True
            i0 = p[j0]
            delta = inf
            j1 = 0
            row_cost = cost[i0 - 1]
            for j in range(1, m + 1):
                if not used[j]:
                    cur = row_cost[j - 1] - u[i0] - v[j]
                    if cur < minv[j]:
                        minv[j] = cur
                        way[j] = j0
                    if minv[j] < delta:
                        delta = minv[j]
                        j1 = j
            for j in range(m + 1):
                if used[j]:
                    u[p[j]] += delta
                    v[j] -= delta
                else:
                    minv[j] -= delta
            j0 = j1
            if p[j0] == 0:
                break
        while True:
            j1 = way[j0]
            p[j0] = p[j1]
            j0 = j1
            if j0 == 0:
                break

    assignment = [-1] * n
    for j in range(1, m + 1):
        if p[j] > 0:
            assignment[p[j] - 1] = j - 1
    return assignment


def get_player_positions(p_info: dict[str, Any]) -> set:
    """Returns the set of fantasy-eligible positions for a Sleeper player."""
    fantasy_positions = p_info.get("fantasy_positions")
    if fantasy_positions:
        return set(fantasy_positions)
    pos = p_info.get("position")
    return {pos} if pos else set()


def evaluate_player(
    pid: str,
    players: dict[str, Any],
    actual_map: dict[str, float],
    pregame_stats: dict[str, dict[str, float]],
    scoring_settings: dict[str, float],
    game_clocks: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Computes live scoring and projection metadata for a single player."""
    p_info = players.get(pid, {})
    name = p_info.get("full_name") or f"{p_info.get('first_name', '')} {p_info.get('last_name', '')}".strip() or pid
    pos = p_info.get("position") or ""
    positions = get_player_positions(p_info)
    raw_team = p_info.get("team") or ""
    espn_team = TEAM_ABBR_MAP.get(raw_team, raw_team)
    inj_status = p_info.get("injury_status") or ""

    actual = round(actual_map.get(pid, 0.0), 2)
    pregame = round(calculate_custom_points(pregame_stats.get(pid, {}), scoring_settings), 2)
    game_info = game_clocks.get(espn_team)

    live_proj, rem_proj, b_mult = calculate_live_projection(
        pregame, actual, game_info, inj_status, pos
    )
    game_state = game_info["state"] if game_info else "bye"
    is_active = bool(game_state == "in" and inj_status not in INACTIVE_STATUSES)
    status_tag = inj_status.upper() if inj_status in INACTIVE_STATUSES else (game_state.upper() if game_info else "BYE")
    damp_note = f" [Blowout x{b_mult:.2f}]" if b_mult < 1.0 else ""

    if pos == "DEF" and raw_team:
        headshot_url = f"https://sleepercdn.com/images/team_logos/nfl/{raw_team.lower()}.png"
    else:
        headshot_url = f"https://sleepercdn.com/content/nfl/players/thumb/{pid}.jpg"

    game_score = ""
    game_detail = ""
    opp_abbr = ""
    if game_info:
        opp_abbr = game_info.get("opp_abbr", "")
        game_detail = game_info.get("short_detail", "")
        if game_state in ("in", "post"):
            game_score = f"{game_info.get('team_score', 0)}-{game_info.get('opp_score', 0)}"

    return {
        "pid": pid,
        "name": name,
        "pos": pos,
        "positions": positions,
        "raw_team": raw_team,
        "inj_status": inj_status,
        "actual": actual,
        "pregame": pregame,
        "live_proj": live_proj,
        "rem_proj": rem_proj,
        "delta_vs_pregame": round(live_proj - pregame, 2),
        "b_mult": b_mult,
        "game_state": game_state,
        "is_active": is_active,
        "game_detail": game_detail,
        "game_score": game_score,
        "opp_abbr": opp_abbr,
        "headshot_url": headshot_url,
        "status_tag": status_tag,
        "damp_note": damp_note,
    }


def optimize_best_ball_lineup(
    starter_slots: list[str],
    player_evals: list[dict[str, Any]],
) -> list[tuple[str, dict[str, Any]]]:
    """
    Assigns eligible players to starter_slots to maximize total live_proj
    (breaking ties by higher actual points). Returns (slot, player_eval) pairs
    for filled slots in starter_slots order.
    """
    num_slots = len(starter_slots)
    num_players = len(player_evals)
    if num_slots == 0 or num_players == 0:
        return []

    dummy_cost = 10**12
    ineligible_cost = 10**15

    # Columns: [0..num_players-1] are real players; [num_players..num_players+num_slots-1] are unfilled-slot dummies
    cost_matrix: list[list[int]] = []
    for slot in starter_slots:
        allowed_positions = SLOT_ELIGIBILITY.get(slot, {slot})
        row: list[int] = []
        for p in player_evals:
            if p["positions"] & allowed_positions:
                score_int = round(p["live_proj"] * 100) * 100_000 + round(p["actual"] * 100)
                row.append(-score_int)
            else:
                row.append(ineligible_cost)
        row.extend([dummy_cost] * num_slots)
        cost_matrix.append(row)

    assignment = _hungarian_min_cost(cost_matrix)
    optimal: list[tuple[str, dict[str, Any]]] = []
    for slot_idx, col_idx in enumerate(assignment):
        if 0 <= col_idx < num_players:
            slot = starter_slots[slot_idx]
            player = player_evals[col_idx]
            allowed_positions = SLOT_ELIGIBILITY.get(slot, {slot})
            if player["positions"] & allowed_positions:
                optimal.append((slot, player))
    return optimal


def sleeper_avatar_url(avatar_id: str | None, thumb: bool = True) -> str | None:
    """Resolves a Sleeper avatar ID or full URL into an image URL."""
    if not avatar_id:
        return None
    if avatar_id.startswith(("http://", "https://")):
        return avatar_id
    prefix = "https://sleepercdn.com/avatars/thumbs/" if thumb else "https://sleepercdn.com/avatars/"
    return f"{prefix}{avatar_id}"


def get_league_type_label(settings: dict[str, Any]) -> str:
    league_type = settings.get("type", 0)
    return {0: "Redraft", 1: "Keeper", 2: "Dynasty"}.get(league_type, "League")


def get_scoring_label(scoring_settings: dict[str, Any]) -> str:
    rec = scoring_settings.get("rec", 0.0) or 0.0
    if rec == 1.0:
        base = "PPR"
    elif rec == 0.5:
        base = "0.5 PPR"
    elif rec == 0.0:
        base = "STD"
    else:
        base = f"{rec:g} PPR"
    if (scoring_settings.get("bonus_rec_te") or 0.0) > 0:
        return f"{base} · TEP"
    return base


def _roster_fpts(roster_settings: dict[str, Any], key: str = "fpts") -> float:
    whole = roster_settings.get(key) or 0
    dec = roster_settings.get(f"{key}_decimal") or 0
    return round(float(whole) + float(dec) / 100.0, 2)


def _compute_roster_ranks(rosters: list[dict[str, Any]]) -> dict[int, int]:
    """Ranks rosters 1..N by wins desc, ties desc, points-for desc."""
    ordered = sorted(
        rosters,
        key=lambda r: (
            r.get("settings", {}).get("wins", 0),
            r.get("settings", {}).get("ties", 0),
            _roster_fpts(r.get("settings", {}), "fpts"),
        ),
        reverse=True,
    )
    return {r["roster_id"]: idx + 1 for idx, r in enumerate(ordered) if "roster_id" in r}


def _serialize_player_eval(p_eval: dict[str, Any], slot: str = "") -> dict[str, Any]:
    out = dict(p_eval)
    out["positions"] = sorted(p_eval.get("positions", set()))
    out["slot"] = slot
    return out


def get_nfl_state(ttl: int=300) -> dict[str, Any]:
    """Fetches current NFL season/week state from Sleeper."""
    data = load_or_fetch_cache(
        "nfl_state.json",
        SLEEPER_URL + "v1/state/nfl",
        ttl_seconds=ttl,
    )
    return {
        "season": int(data.get("season") or 2026),
        "week": int(data.get("display_week") or data.get("week") or 1),
        "season_type": data.get("season_type") or "regular",
    }


def get_user(user_query: str) -> dict[str, Any] | None:
    """Resolves a Sleeper username or user_id."""
    q = (user_query or "").strip()
    if not q:
        return None
    resp = requests.get(SLEEPER_URL + f"v1/user/{q}", timeout=10)
    resp.raise_for_status()
    data = resp.json()
    if not data or not data.get("user_id"):
        return None
    return {
        "user_id": data["user_id"],
        "username": data.get("username") or "",
        "display_name": data.get("display_name") or data.get("username") or data["user_id"],
        "avatar_url": sleeper_avatar_url(data.get("avatar")),
    }


def get_user_leagues_summary(user_id: str, season: int) -> list[dict[str, Any]]:
    """Fetches all NFL leagues for a user in a season, enriched with their roster record and rank."""
    resp = requests.get(SLEEPER_URL + f"v1/user/{user_id}/leagues/nfl/{season}", timeout=10)
    resp.raise_for_status()
    leagues = resp.json() or []

    def _fetch_rosters(league_id: str) -> list[dict[str, Any]]:
        try:
            r = requests.get(SLEEPER_URL + f"v1/league/{league_id}/rosters", timeout=10)
            r.raise_for_status()
            return r.json() or []
        except requests.RequestException as exc:
            logger.warning("Failed to fetch rosters for league %s: %s", league_id, exc)
            return []

    with ThreadPoolExecutor(max_workers=8) as pool:
        rosters_by_league = dict(
            zip(
                [lg["league_id"] for lg in leagues],
                pool.map(_fetch_rosters, [lg["league_id"] for lg in leagues]),
            )
        )

    summaries: list[dict[str, Any]] = []
    for lg in leagues:
        lid = lg["league_id"]
        settings = lg.get("settings") or {}
        scoring = lg.get("scoring_settings") or {}
        rosters = rosters_by_league.get(lid, [])
        ranks = _compute_roster_ranks(rosters)

        user_roster = next(
            (
                r
                for r in rosters
                if r.get("owner_id") == user_id or user_id in (r.get("co_owners") or [])
            ),
            None,
        )
        r_settings = (user_roster or {}).get("settings") or {}
        r_meta = (user_roster or {}).get("metadata") or {}
        rid = (user_roster or {}).get("roster_id")

        summaries.append({
            "league_id": lid,
            "name": lg.get("name") or f"League {lid}",
            "avatar_url": sleeper_avatar_url(lg.get("avatar")),
            "season": int(lg.get("season") or season),
            "status": lg.get("status") or "",
            "total_rosters": lg.get("total_rosters") or len(rosters),
            "is_best_ball": bool(settings.get("best_ball", 0)),
            "league_type": get_league_type_label(settings),
            "scoring_label": get_scoring_label(scoring),
            "current_leg": int(settings.get("leg") or 1),
            "playoff_teams": int(settings.get("playoff_teams") or 0),
            "user_team": {
                "roster_id": rid,
                "wins": int(r_settings.get("wins", 0)),
                "losses": int(r_settings.get("losses", 0)),
                "ties": int(r_settings.get("ties", 0)),
                "fpts": _roster_fpts(r_settings, "fpts"),
                "fpts_against": _roster_fpts(r_settings, "fpts_against"),
                "streak": r_meta.get("streak") or "",
                "rank": ranks.get(rid),
            } if user_roster else None,
        })

    return summaries


def get_league_scoreboard(
    league_id: str,
    season: int,
    week: int,
    user_id: str | None = None,
    refresh_cache: bool = False,
) -> dict[str, Any]:
    """Returns structured live scoreboard data for all matchups in a league."""
    ttl = 0 if refresh_cache else 43200

    league = load_or_fetch_cache(
        f"league_{league_id}.json",
        SLEEPER_URL + f"v1/league/{league_id}",
        ttl_seconds=ttl,
    )
    settings = league.get("settings") or {}
    scoring_settings = league.get("scoring_settings") or {}
    is_best_ball = bool(settings.get("best_ball", 0))
    starter_slots = [
        slot for slot in league.get("roster_positions", []) if slot not in NON_STARTER_SLOTS
    ]

    players = load_or_fetch_cache(
        "nfl_players.json",
        SLEEPER_URL + "v1/players/nfl",
        ttl_seconds=ttl,
    )

    proj_params = [
        ("season_type", "regular"),
        ("position[]", "QB"),
        ("position[]", "RB"),
        ("position[]", "WR"),
        ("position[]", "TE"),
        ("position[]", "K"),
        ("position[]", "DEF"),
    ]
    raw_proj = load_or_fetch_cache(
        f"proj_{season}_w{week}.json",
        SLEEPER_URL + f"projections/nfl/{season}/{week}",
        params=proj_params,
        ttl_seconds=0 if refresh_cache else 900,
    )
    if isinstance(raw_proj, list):
        pregame_stats = {p["player_id"]: p.get("stats", {}) for p in raw_proj if isinstance(p, dict) and "player_id" in p}
    elif isinstance(raw_proj, dict):
        pregame_stats = {pid: stats for pid, stats in raw_proj.items() if isinstance(stats, dict)}
    else:
        pregame_stats = {}

    with ThreadPoolExecutor(max_workers=4) as pool:
        f_users = pool.submit(lambda: requests.get(SLEEPER_URL + f"v1/league/{league_id}/users", timeout=10).json())
        f_rosters = pool.submit(lambda: requests.get(SLEEPER_URL + f"v1/league/{league_id}/rosters", timeout=10).json())
        f_matchups = pool.submit(lambda: requests.get(SLEEPER_URL + f"v1/league/{league_id}/matchups/{week}", timeout=10).json())
        f_clocks = pool.submit(get_nfl_game_clocks, season, week)

        users = f_users.result() or []
        rosters = f_rosters.result() or []
        matchups_raw = f_matchups.result() or []
        try:
            game_clocks = f_clocks.result()
        except (requests.RequestException, ValueError, KeyError, TypeError) as exc:
            logger.warning("Failed to fetch ESPN game clocks: %s", exc)
            game_clocks = {}

    users_by_id = {u["user_id"]: u for u in users if "user_id" in u}
    rosters_by_id = {r["roster_id"]: r for r in rosters if "roster_id" in r}
    ranks = _compute_roster_ranks(rosters)

    grouped_matchups: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for m in matchups_raw:
        m_id = m.get("matchup_id")
        if m_id is not None:
            grouped_matchups[m_id].append(m)

    matchups_out: list[dict[str, Any]] = []
    for m_id, pair in sorted(grouped_matchups.items()):
        teams_out: list[dict[str, Any]] = []
        is_user_matchup = False

        for m_entry in pair:
            r_id = m_entry["roster_id"]
            roster_obj = rosters_by_id.get(r_id, {})
            owner_id = roster_obj.get("owner_id")
            co_owners = roster_obj.get("co_owners") or []
            user_obj = users_by_id.get(owner_id, {})
            user_meta = user_obj.get("metadata") or {}
            r_settings = roster_obj.get("settings") or {}
            r_meta = roster_obj.get("metadata") or {}

            is_user_team = bool(user_id and (owner_id == user_id or user_id in co_owners))
            if is_user_team:
                is_user_matchup = True

            owner_name = user_obj.get("display_name") or f"Team {r_id}"
            team_name = user_meta.get("team_name") or owner_name
            avatar_url = sleeper_avatar_url(user_meta.get("avatar") or user_obj.get("avatar"))

            starters_raw = m_entry.get("starters") or []
            actual_map = m_entry.get("players_points") or {}

            all_player_ids = list(
                dict.fromkeys(
                    pid
                    for pid in (m_entry.get("players") or list(actual_map.keys()) or starters_raw)
                    if pid and pid != "0"
                )
            )
            eval_by_pid = {
                pid: evaluate_player(pid, players, actual_map, pregame_stats, scoring_settings, game_clocks)
                for pid in all_player_ids
            }
            for pid in starters_raw:
                if pid and pid != "0" and pid not in eval_by_pid:
                    eval_by_pid[pid] = evaluate_player(
                        pid, players, actual_map, pregame_stats, scoring_settings, game_clocks
                    )

            if is_best_ball and starter_slots:
                lineup = optimize_best_ball_lineup(starter_slots, list(eval_by_pid.values()))
            else:
                lineup = []
                for idx, pid in enumerate(starters_raw):
                    if pid and pid != "0":
                        slot_label = starter_slots[idx] if idx < len(starter_slots) else ""
                        lineup.append((slot_label, eval_by_pid[pid]))

            starter_ids = {p_eval["pid"] for _, p_eval in lineup}
            bench_evals = sorted(
                (p_eval for pid, p_eval in eval_by_pid.items() if pid not in starter_ids),
                key=lambda p: (p["live_proj"], p["actual"], p["pregame"]),
                reverse=True,
            )

            total_pregame = round(sum(p["pregame"] for _, p in lineup), 2)
            total_actual = round(sum(p["actual"] for _, p in lineup), 2)
            total_live = round(sum(p["live_proj"] for _, p in lineup), 2)
            total_rem = round(sum(p["rem_proj"] for _, p in lineup), 2)
            active_count = sum(1 for _, p in lineup if p["is_active"])
            pre_count = sum(1 for _, p in lineup if p["game_state"] == "pre" and p["inj_status"] not in INACTIVE_STATUSES)
            post_count = sum(1 for _, p in lineup if p["game_state"] == "post")

            teams_out.append({
                "roster_id": r_id,
                "owner_id": owner_id,
                "owner_name": owner_name,
                "team_name": team_name,
                "avatar_url": avatar_url,
                "is_user": is_user_team,
                "wins": int(r_settings.get("wins", 0)),
                "losses": int(r_settings.get("losses", 0)),
                "ties": int(r_settings.get("ties", 0)),
                "fpts": _roster_fpts(r_settings, "fpts"),
                "fpts_against": _roster_fpts(r_settings, "fpts_against"),
                "streak": r_meta.get("streak") or "",
                "rank": ranks.get(r_id),
                "win_prob": 50.0,
                "totals": {
                    "pregame_proj": total_pregame,
                    "actual": total_actual,
                    "live_proj": total_live,
                    "rem_proj": total_rem,
                    "delta_vs_pregame": round(total_live - total_pregame, 2),
                    "active_count": active_count,
                    "pre_count": pre_count,
                    "post_count": post_count,
                },
                "starters": [_serialize_player_eval(p, slot) for slot, p in lineup],
                "bench": [_serialize_player_eval(p, "BN") for p in bench_evals],
            })

        if len(teams_out) == 2:
            # Put the user's team on the left if present
            if teams_out[1]["is_user"] and not teams_out[0]["is_user"]:
                teams_out.reverse()
            t1, t2 = teams_out[0]["totals"], teams_out[1]["totals"]
            win1, win2 = calculate_win_probability(
                t1["live_proj"], t1["rem_proj"], t2["live_proj"], t2["rem_proj"]
            )
            teams_out[0]["win_prob"] = win1
            teams_out[1]["win_prob"] = win2

        matchups_out.append({
            "matchup_id": m_id,
            "is_user_matchup": is_user_matchup,
            "teams": teams_out,
        })

    # Sort user's matchup first, then by matchup_id
    matchups_out.sort(key=lambda m: (not m["is_user_matchup"], m["matchup_id"]))

    return {
        "league": {
            "league_id": league_id,
            "name": league.get("name") or f"League {league_id}",
            "avatar_url": sleeper_avatar_url(league.get("avatar")),
            "season": season,
            "week": week,
            "is_best_ball": is_best_ball,
            "league_type": get_league_type_label(settings),
            "scoring_label": get_scoring_label(scoring_settings),
            "starter_slots": starter_slots,
            "total_rosters": league.get("total_rosters") or len(rosters),
            "playoff_teams": int(settings.get("playoff_teams") or 0),
        },
        "updated_at": int(time.time()),
        "matchups": matchups_out,
    }


def run_live_scoreboard(league_id: str, season: int, week: int, refresh_cache: bool = False):
    ttl = 0 if refresh_cache else 43200

    logger.debug("Fetching league...")
    league = load_or_fetch_cache(
        f"league_{league_id}.json",
        SLEEPER_URL + f"v1/league/{league_id}",
        ttl_seconds=ttl,
    )
    
    logger.debug("Fetching scoring settings...")
    scoring_settings = league.get("scoring_settings", {})
    is_best_ball = bool(league.get("settings", {}).get("best_ball", 0))
    starter_slots = [
        slot for slot in league.get("roster_positions", []) if slot not in NON_STARTER_SLOTS
    ]

    logger.debug("Fetching players...")
    players = load_or_fetch_cache(
        "nfl_players.json",
        SLEEPER_URL + "v1/players/nfl",
        ttl_seconds=ttl,
    )

    logger.debug("Fetching projections...")
    proj_params = [
        ("season_type", "regular"),
        ("position[]", "QB"),
        ("position[]", "RB"),
        ("position[]", "WR"),
        ("position[]", "TE"),
        ("position[]", "K"),
        ("position[]", "DEF"),
    ]
    raw_proj = load_or_fetch_cache(
        f"proj_{season}_w{week}.json",
        SLEEPER_URL + f"projections/nfl/{season}/{week}",
        params=proj_params,
        ttl_seconds=0 if refresh_cache else 900,
    )
    pregame_stats = {p["player_id"]: p.get("stats", {}) for p in raw_proj if "player_id" in p}

    logger.debug("Fetching users...")
    users = requests.get(SLEEPER_URL + f"v1/league/{league_id}/users", timeout=10).json()
    logger.debug("Fetching rosters...")
    rosters = requests.get(SLEEPER_URL + f"v1/league/{league_id}/rosters", timeout=10).json()
    logger.debug("Fetching matchups...")
    matchups = requests.get(SLEEPER_URL + f"v1/league/{league_id}/matchups/{week}", timeout=10).json()
    logger.debug("Fetching game_clocks...")
    game_clocks = get_nfl_game_clocks(season=season, week=week)

    user_map = {u["user_id"]: u.get("display_name", "Unknown") for u in users}
    roster_owner = {r["roster_id"]: user_map.get(r.get("owner_id"), f"Team {r['roster_id']}") for r in rosters}

    grouped_matchups: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for m in matchups:
        m_id = m.get("matchup_id")
        if m_id is not None:
            grouped_matchups[m_id].append(m)

    for m_id, pair in sorted(grouped_matchups.items()):
        print(f"\n==================== MATCHUP {m_id} ====================")
        team_summaries = []

        for roster in pair:
            r_id = roster["roster_id"]
            owner_name = roster_owner.get(r_id, f"Roster {r_id}")
            starters = roster.get("starters", [])
            actual_map = roster.get("players_points", {})

            total_pregame_proj = 0.0
            total_actual = 0.0
            total_live_proj = 0.0
            total_rem_proj = 0.0

            all_player_ids = list(
                dict.fromkeys(
                    pid
                    for pid in (roster.get("players") or list(actual_map.keys()) or starters)
                    if pid and pid != "0"
                )
            )
            eval_by_pid = {
                pid: evaluate_player(pid, players, actual_map, pregame_stats, scoring_settings, game_clocks)
                for pid in all_player_ids
            }
            for pid in starters:
                if pid and pid != "0" and pid not in eval_by_pid:
                    eval_by_pid[pid] = evaluate_player(
                        pid, players, actual_map, pregame_stats, scoring_settings, game_clocks
                    )

            use_slots = bool(is_best_ball and starter_slots)
            if use_slots:
                lineup = optimize_best_ball_lineup(starter_slots, list(eval_by_pid.values()))
                print(f"\n[{owner_name}] (Best Ball Optimal)")
            else:
                lineup = [
                    ("", eval_by_pid[pid])
                    for pid in starters
                    if pid and pid != "0"
                ]
                print(f"\n[{owner_name}]")

            starter_ids = {p_eval["pid"] for _, p_eval in lineup}
            bench = sorted(
                (p_eval for pid, p_eval in eval_by_pid.items() if pid not in starter_ids),
                key=lambda p: (p["live_proj"], p["actual"], p["pregame"]),
                reverse=True,
            )

            for slot, p_eval in lineup:
                total_pregame_proj += p_eval["pregame"]
                total_actual += p_eval["actual"]
                total_live_proj += p_eval["live_proj"]
                total_rem_proj += p_eval["rem_proj"]

                slot_prefix = f"{slot:<10} " if use_slots else ""
                print(
                    f"  {slot_prefix}{p_eval['name']:<22} "
                    f"({p_eval['pos']:<3} {p_eval['raw_team']:<3} | {p_eval['status_tag']:<4}) "
                    f"-> Pre: {p_eval['pregame']:5.2f} | Act: {p_eval['actual']:5.2f} | Live: {p_eval['live_proj']:5.2f}{p_eval['damp_note']}"
                )

            if bench:
                print("  -- Bench --")
                bench_prefix = f"{'BN':<10} " if use_slots else ""
                for p_eval in bench:
                    print(
                        f"  {bench_prefix}{p_eval['name']:<22} "
                        f"({p_eval['pos']:<3} {p_eval['raw_team']:<3} | {p_eval['status_tag']:<4}) "
                        f"-> Pre: {p_eval['pregame']:5.2f} | Act: {p_eval['actual']:5.2f} | Live: {p_eval['live_proj']:5.2f}{p_eval['damp_note']}"
                    )

            team_summaries.append({
                "owner": owner_name,
                "pregame_proj": total_pregame_proj,
                "actual": total_actual,
                "live_proj": total_live_proj,
                "rem_proj": total_rem_proj,
            })

        if len(team_summaries) == 2:
            t1, t2 = team_summaries[0], team_summaries[1]
            win1, win2 = calculate_win_probability(t1["live_proj"], t1["rem_proj"], t2["live_proj"], t2["rem_proj"])
            print("\n--- Matchup Summary ---")
            print(
                f"  {t1['owner']:<20} | Pre-Game: {t1['pregame_proj']:6.2f} "
                f"| Actual: {t1['actual']:6.2f} | Live Proj: {t1['live_proj']:6.2f} | Win Prob: {win1:5.1f}%"
            )
            print(
                f"  {t2['owner']:<20} | Pre-Game: {t2['pregame_proj']:6.2f} "
                f"| Actual: {t2['actual']:6.2f} | Live Proj: {t2['live_proj']:6.2f} | Win Prob: {win2:5.1f}%"
            )


if __name__ == "__main__":
    
    logger.setLevel(logging.DEBUG)

    nfl_state = get_nfl_state(ttl=0)
    print(nfl_state)
    
    # LEAGUE_ID = "1322935618086379520"
    # SEASON = 2026
    # WEEK = 12
    # run_live_scoreboard(LEAGUE_ID, SEASON, WEEK, refresh_cache=False)
