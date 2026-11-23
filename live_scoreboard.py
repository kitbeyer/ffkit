import json
import logging
import math
import os
import time
from collections import defaultdict
from typing import Any, Dict, List, Tuple
import requests

import sleeper

CACHE_DIR = "./sleeper_cache"
TEAM_ABBR_MAP = {"JAC": "JAX", "WAS": "WSH"}
INACTIVE_STATUSES = {"Out", "IR", "PUP", "COV", "Sus", "NA"}

logger = logging.getLogger(__name__)

def load_or_fetch_cache(filename: str, url: str, params: Any = None, ttl_seconds: int = 86400) -> Any:
    """Caches large or static API responses locally to allow safe 30-second live polling."""
    os.makedirs(CACHE_DIR, exist_ok=True)
    filepath = os.path.join(CACHE_DIR, filename)

    if os.path.exists(filepath):
        age = time.time() - os.path.getmtime(filepath)
        if age < ttl_seconds:
            with open(filepath, "r", encoding="utf-8") as f:
                return json.load(f)

    resp = requests.get(url, params=params, timeout=20)
    resp.raise_for_status()
    data = resp.json()

    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f)
    return data


def get_nfl_game_clocks() -> Dict[str, Dict[str, Any]]:
    """Fetches live NFL game states, clocks, and team scores from ESPN's public scoreboard."""
    url = "https://espn.com"
    resp = requests.get(url, timeout=10)
    resp.raise_for_status()
    data = resp.json()

    clocks = {}
    for event in data.get("events", []):
        status = event.get("status", {})
        state = status.get("type", {}).get("state", "pre")
        period = status.get("period", 1)
        clock_sec = int(status.get("clock", 0))

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
                    "team_score": score1,
                    "opp_score": score2,
                }
                clocks[abbr2] = {
                    "state": state,
                    "period": period,
                    "clock": clock_sec,
                    "team_score": score2,
                    "opp_score": score1,
                }
    return clocks


def get_blowout_multiplier(game_info: Dict[str, Any], position: str) -> float:
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


def calculate_custom_points(stats: Dict[str, float], scoring: Dict[str, float]) -> float:
    """Calculates fantasy points using the league's custom scoring settings."""
    return sum(val * scoring[key] for key, val in stats.items() if key in scoring)


def calculate_live_projection(
    pregame_proj: float,
    actual_pts: float,
    game_info: Dict[str, Any],
    injury_status: str,
    position: str,
) -> Tuple[float, float, float]:
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
) -> Tuple[float, float]:
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


def run_live_scoreboard(league_id: str, season: int, week: int, refresh_cache: bool = False):
    ttl = 0 if refresh_cache else 43200

    logger.debug("Fetching league...")
    league = load_or_fetch_cache(
        f"league_{league_id}.json",
        f"https://sleeper.app{league_id}",
        ttl_seconds=ttl,
    )
    
    logger.debug("Fetching scoring settings...")
    scoring_settings = league.get("scoring_settings", {})

    logger.debug("Fetching players...")
    players = load_or_fetch_cache(
        "nfl_players.json",
        "https://sleeper.app",
        ttl_seconds=0 if refresh_cache else 900,
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
        f"https://sleeper.app{season}/{week}",
        params=proj_params,
        ttl_seconds=ttl,
    )
    pregame_stats = {p["player_id"]: p.get("stats", {}) for p in raw_proj if "player_id" in p}

    logger.debug("Fetching users...")
    users = requests.get(f"https://sleeper.app{league_id}/users", timeout=10).json()
    logger.debug("Fetching rosters...")
    rosters = requests.get(f"https://sleeper.app{league_id}/rosters", timeout=10).json()
    logger.debug("Fetching matchups...")
    matchups = requests.get(f"https://sleeper.app{league_id}/matchups/{week}", timeout=10).json()
    logger.debug("Fetching game_clocks...")
    game_clocks = get_nfl_game_clocks()

    user_map = {u["user_id"]: u.get("display_name", "Unknown") for u in users}
    roster_owner = {r["roster_id"]: user_map.get(r.get("owner_id"), f"Team {r['roster_id']}") for r in rosters}

    grouped_matchups: Dict[int, List[Dict[str, Any]]] = defaultdict(list)
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

            total_actual = 0.0
            total_live_proj = 0.0
            total_rem_proj = 0.0

            print(f"\n[{owner_name}]")
            for pid in starters:
                if not pid or pid == "0":
                    continue

                p_info = players.get(pid, {})
                name = p_info.get("full_name") or f"{p_info.get('first_name', '')} {p_info.get('last_name', '')}".strip()
                pos = p_info.get("position", "")
                raw_team = p_info.get("team", "")
                espn_team = TEAM_ABBR_MAP.get(raw_team, raw_team)
                inj_status = p_info.get("injury_status") or ""

                actual = actual_map.get(pid, 0.0)
                pregame = calculate_custom_points(pregame_stats.get(pid, {}), scoring_settings)
                game_info = game_clocks.get(espn_team)

                live_proj, rem_proj, b_mult = calculate_live_projection(
                    pregame, actual, game_info, inj_status, pos
                )

                total_actual += actual
                total_live_proj += live_proj
                total_rem_proj += rem_proj

                status_tag = inj_status.upper() if inj_status in INACTIVE_STATUSES else (game_info["state"].upper() if game_info else "BYE")
                damp_note = f" [Blowout x{b_mult:.2f}]" if b_mult < 1.0 else ""
                print(f"  {name:<22} ({pos:<3} {raw_team:<3} | {status_tag:<4}) -> Act: {actual:5.2f} | Proj: {live_proj:5.2f}{damp_note}")

            team_summaries.append({
                "owner": owner_name,
                "actual": total_actual,
                "live_proj": total_live_proj,
                "rem_proj": total_rem_proj,
            })

        if len(team_summaries) == 2:
            t1, t2 = team_summaries[0], team_summaries[1]
            win1, win2 = calculate_win_probability(t1["live_proj"], t1["rem_proj"], t2["live_proj"], t2["rem_proj"])
            print("\n--- Matchup Summary ---")
            print(f"  {t1['owner']:<20} | Actual: {t1['actual']:6.2f} | Live Proj: {t1['live_proj']:6.2f} | Win Prob: {win1:5.1f}%")
            print(f" {t2['owner']:<20} | Actual: {t2['actual']:6.2f} | Live Proj: {t2['live_proj']:6.2f} | Win Prob: {win2:5.1f}%")


if __name__ == "__main__":
    
    logger.setLevel(logging.DEBUG)
    
    LEAGUE_ID = "1322935618086379520"
    SEASON = 2026
    WEEK = 11
    run_live_scoreboard(LEAGUE_ID, SEASON, WEEK, refresh_cache=True)
