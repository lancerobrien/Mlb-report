# =====================================================================
# MLB DAILY MATCHUP + BATTER-vs-PITCHER (BvP) REPORT
# =====================================================================
# Runs in Google Colab (or any Python 3.9+). No API key, no install
# needed beyond `requests` (Colab already has it).
#
# WHAT IT DOES:
#   For every game today it prints:
#     - both probable starting pitchers
#     - the opposing lineup (once posted)
#     - each hitter's CAREER numbers vs that starter: AB, AVG, HR, K
#
# DATA SOURCE: MLB StatsAPI (statsapi.mlb.com) — official & free.
#
# HOW TO RUN IN COLAB (on your phone):
#   1. colab.research.google.com  ->  New notebook
#   2. Paste this whole thing into a cell
#   3. Run it (play button)
#   4. Copy the printed output and paste it to Claude
#
# TIMING: run it 2-3 hours before first pitch so lineups are posted.
#   If you run it too early, it still shows the pitchers and says
#   "lineup not posted yet."
# =====================================================================

import requests
from datetime import datetime, timezone, timedelta
import time

# ------------------------- CONFIG -------------------------
DATE = None            # e.g. "2026-07-19"; None = today (US/Eastern)
MIN_AB_FLAG = 20       # need this many career AB before a BvP flag fires at all
STRONG_AB_FLAG = 40    # at/above this = "strong confidence" tier vs "moderate"
GOOD_AVG_FLAG = 0.300  # flag "hot" BvP at/above this AVG
SLEEP = 0.05           # tiny pause between calls (be polite to the API)
INCLUDE_TEAM_FORM = True        # last-5 AND last-10-games offense snapshot
INCLUDE_BULLPEN_FATIGUE = True  # recent relief usage
INCLUDE_SCHEDULE_CONTEXT = True # homestand/road-trip, short turnaround, TZ shift
INCLUDE_WEATHER = True          # game-time wind/temp/precip at the actual venue
BULLPEN_LOOKBACK_GAMES = 3      # how many recent games to check for pen fatigue
FORM_LOOKBACK_GAMES = 10        # how many recent games for the last-10 offense line
FORM_LOOKBACK_GAMES_SHORT = 5   # how many recent games for the last-5 offense line
# ----------------------------------------------------------

# ---- Weather source: Open-Meteo (free, no API key, no signup) ----
WEATHER_BASE = "https://api.open-meteo.com/v1/forecast"

# Stadium coordinates + roof type. Coordinates are stable public geographic
# facts (low risk). roof: "open" | "dome" (always closed) | "retractable"
# (status not knowable in advance — weather shown is the OUTDOOR reading).
STADIUM = {
    "NYY": (40.8296, -73.9262, "open"),       "BOS": (42.3467, -71.0972, "open"),
    "TB":  (27.7683, -82.6534, "dome"),       "TOR": (43.6414, -79.3894, "retractable"),
    "BAL": (39.2838, -76.6217, "open"),       "CLE": (41.4962, -81.6852, "open"),
    "DET": (42.3390, -83.0485, "open"),       "KC":  (39.0517, -94.4803, "open"),
    "CWS": (41.8299, -87.6338, "open"),       "MIN": (44.9817, -93.2777, "open"),
    "HOU": (29.7573, -95.3555, "retractable"),"LAA": (33.8003, -117.8827, "open"),
    "SEA": (47.5914, -122.3325, "retractable"),"TEX": (32.7473, -97.0842, "retractable"),
    "ATH": (38.5820, -121.5140, "open"),      "ATL": (33.8907, -84.4677, "open"),
    "MIA": (25.7781, -80.2196, "retractable"),"NYM": (40.7571, -73.8458, "open"),
    "PHI": (39.9061, -75.1665, "open"),       "WSH": (38.8730, -77.0074, "open"),
    "CHC": (41.9484, -87.6553, "open"),       "CIN": (39.0975, -84.5074, "open"),
    "MIL": (43.0280, -87.9712, "retractable"),"PIT": (40.4469, -80.0057, "open"),
    "STL": (38.6226, -90.1928, "open"),       "AZ":  (33.4455, -112.0667, "retractable"),
    "COL": (39.7559, -104.9942, "open"),      "LAD": (34.0739, -118.2400, "open"),
    "SD":  (32.7073, -117.1566, "open"),      "SF":  (37.7786, -122.3893, "open"),
}

# Compass bearing (0=N,90=E,180=S,270=W) from home plate toward straight-
# away center field. DELIBERATELY SMALL — only parks verified with real
# confidence get a "blowing out/in" translation; everything else still
# gets accurate raw wind speed/direction, just no park-relative label.
# Add more here only once verified (don't guess — a wrong label is worse
# than no label).
CF_BEARING = {
    "CHC": 30,   # Wrigley — matches this season's validated wind reads
}

# Rough home time zone bucket per team (approximation, not GPS-precise —
# good enough to flag "crossed multiple zones" type fatigue).
TEAM_TZ = {
    "NYY": "ET", "BOS": "ET", "TB": "ET", "TOR": "ET", "BAL": "ET",
    "PHI": "ET", "ATL": "ET", "NYM": "ET", "WSH": "ET", "MIA": "ET",
    "PIT": "ET", "DET": "ET", "CLE": "ET", "CIN": "ET",
    "CHC": "CT", "CWS": "CT", "MIL": "CT", "STL": "CT", "MIN": "CT",
    "KC": "CT", "TEX": "CT", "HOU": "CT",
    "COL": "MT", "AZ": "MT", "ARI": "MT",
    "LAD": "PT", "LAA": "PT", "SD": "PT", "SF": "PT", "SEA": "PT",
    "ATH": "PT", "OAK": "PT",
}
TZ_ORDER = {"ET": 0, "CT": 1, "MT": 2, "PT": 3}  # for computing zone distance

BASE = "https://statsapi.mlb.com/api/v1"
session = requests.Session()
session.headers.update({"User-Agent": "bvp-report/1.0"})


def eastern_today():
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("America/New_York")).strftime("%Y-%m-%d")
    except Exception:
        return (datetime.now(timezone.utc) - timedelta(hours=4)).strftime("%Y-%m-%d")


def get_schedule(date):
    r = session.get(
        f"{BASE}/schedule",
        params={
            "sportId": 1,
            "date": date,
            "hydrate": "probablePitcher,lineups,team",
        },
        timeout=30,
    )
    r.raise_for_status()
    dates = r.json().get("dates", [])
    return dates[0].get("games", []) if dates else []


def get_bvp(batter_id, pitcher_id):
    """Career batter-vs-pitcher hitting line. Returns dict or None."""
    try:
        r = session.get(
            f"{BASE}/people/{batter_id}/stats",
            params={
                "stats": "vsPlayerTotal",
                "opposingPlayerId": pitcher_id,
                "group": "hitting",
            },
            timeout=30,
        )
        r.raise_for_status()
        for block in r.json().get("stats", []):
            splits = block.get("splits", [])
            if splits:
                s = splits[0].get("stat", {})
                return {
                    "ab": s.get("atBats", 0),
                    "hits": s.get("hits", 0),
                    "avg": s.get("avg", "-"),
                    "hr": s.get("homeRuns", 0),
                    "so": s.get("strikeOuts", 0),
                    "bb": s.get("baseOnBalls", 0),
                    "pa": s.get("plateAppearances", 0),
                }
    except Exception:
        return None
    return None


def get_team_schedule(team_id, start_date, end_date):
    """Team's games in a date range, with linescore hydrated (for extra-innings check)."""
    try:
        r = session.get(
            f"{BASE}/schedule",
            params={
                "sportId": 1, "teamId": team_id,
                "startDate": start_date, "endDate": end_date,
                "hydrate": "linescore,team",
            },
            timeout=30,
        )
        r.raise_for_status()
        games = []
        for d in r.json().get("dates", []):
            games.extend(d.get("games", []))
        return sorted(games, key=lambda g: g.get("gameDate", ""))
    except Exception:
        return []


def get_team_recent_form(team_id, as_of_date, num_games=FORM_LOOKBACK_GAMES):
    """Offense snapshot over the team's last `num_games` completed games."""
    try:
        as_of = datetime.strptime(as_of_date, "%Y-%m-%d")
        start = (as_of - timedelta(days=25)).strftime("%Y-%m-%d")
        end = (as_of - timedelta(days=1)).strftime("%Y-%m-%d")
        games = get_team_schedule(team_id, start, end)
        completed = [g for g in games if g.get("status", {}).get("abstractGameState") == "Final"]
        if not completed:
            return None
        recent = completed[-num_games:]
        d_start = recent[0]["officialDate"]
        d_end = recent[-1]["officialDate"]

        r = session.get(
            f"{BASE}/teams/{team_id}/stats",
            params={"stats": "byDateRange", "group": "hitting",
                    "startDate": d_start, "endDate": d_end, "season": as_of.year},
            timeout=30,
        )
        r.raise_for_status()
        for block in r.json().get("stats", []):
            splits = block.get("splits", [])
            if splits:
                s = splits[0].get("stat", {})
                gp = s.get("gamesPlayed", len(recent)) or len(recent)
                runs = s.get("runs", 0)
                return {
                    "games": gp,
                    "runs_per_game": round(runs / gp, 2) if gp else 0,
                    "avg": s.get("avg", "-"),
                    "ops": s.get("ops", "-"),
                }
    except Exception:
        return None
    return None


def get_bullpen_fatigue(team_id, as_of_date, num_games=BULLPEN_LOOKBACK_GAMES):
    """Relief usage over the last `num_games`: total bullpen IP + any reliever
    who's pitched in multiple of those games (possible fatigue)."""
    try:
        as_of = datetime.strptime(as_of_date, "%Y-%m-%d")
        start = (as_of - timedelta(days=12)).strftime("%Y-%m-%d")
        end = (as_of - timedelta(days=1)).strftime("%Y-%m-%d")
        games = get_team_schedule(team_id, start, end)
        completed = [g for g in games if g.get("status", {}).get("abstractGameState") == "Final"]
        recent = completed[-num_games:]
        if not recent:
            return None

        total_bullpen_ip = 0.0
        reliever_appearances = {}  # name -> count across these games

        for g in recent:
            game_pk = g.get("gamePk")
            side = "home" if g.get("teams", {}).get("home", {}).get("team", {}).get("id") == team_id else "away"
            r = session.get(f"{BASE}/game/{game_pk}/boxscore", timeout=30)
            time.sleep(SLEEP)
            r.raise_for_status()
            team_box = r.json().get("teams", {}).get(side, {})
            players = team_box.get("players", {})

            pitcher_lines = []
            for _, p in players.items():
                pitching = p.get("stats", {}).get("pitching", {})
                ip_str = pitching.get("inningsPitched")
                if ip_str is None:
                    continue
                try:
                    # "6.1" innings-pitched notation -> real float (1/3 & 2/3 outs)
                    whole, _, frac = ip_str.partition(".")
                    ip_val = int(whole) + {"0": 0, "1": 1 / 3, "2": 2 / 3}.get(frac, 0)
                except Exception:
                    ip_val = 0.0
                pitcher_lines.append((p["person"]["fullName"], ip_val))

            if not pitcher_lines:
                continue
            pitcher_lines.sort(key=lambda x: -x[1])
            starter_ip = pitcher_lines[0][1]  # most-IP pitcher ~= starter (approximation)
            game_bullpen_ip = sum(ip for _, ip in pitcher_lines[1:])
            total_bullpen_ip += game_bullpen_ip
            for name, ip in pitcher_lines[1:]:
                if ip > 0:
                    reliever_appearances[name] = reliever_appearances.get(name, 0) + 1

        taxed = [name for name, cnt in reliever_appearances.items() if cnt >= 2]
        return {
            "games_checked": len(recent),
            "bullpen_ip": round(total_bullpen_ip, 1),
            "taxed_relievers": taxed,
        }
    except Exception:
        return None


def get_schedule_context(team_id, team_abbr, as_of_date, today_venue_abbr):
    """Homestand/road-trip streak, short-turnaround flag, extra-innings hangover,
    and a rough time-zone-shift flag heading into today's game.
    `today_venue_abbr` = abbreviation of whichever team is hosting TODAY's game
    (i.e. today's actual city), so the zone check is accurate for both sides."""
    try:
        as_of = datetime.strptime(as_of_date, "%Y-%m-%d")
        start = (as_of - timedelta(days=15)).strftime("%Y-%m-%d")
        end = as_of_date
        games = get_team_schedule(team_id, start, end)
        games = [g for g in games if g.get("officialDate", g.get("gameDate", ""))[:10] < as_of_date]
        if not games:
            return None
        games.sort(key=lambda g: g.get("gameDate", ""))
        last = games[-1]

        def is_home(g):
            return g.get("teams", {}).get("home", {}).get("team", {}).get("id") == team_id

        # current streak of same home/away status walking backward
        streak_type = "home" if is_home(last) else "away"
        streak_len = 0
        for g in reversed(games):
            if (g.get("status", {}).get("abstractGameState") == "Final") and \
               (("home" if is_home(g) else "away") == streak_type):
                streak_len += 1
            else:
                break

        flags = []
        if streak_type == "away" and streak_len >= 6:
            flags.append(f"late in a {streak_len}-game road trip")
        elif streak_type == "home" and streak_len >= 6:
            flags.append(f"deep in a {streak_len}-game homestand")

        # short turnaround: yesterday's game started late at night
        try:
            last_dt = datetime.fromisoformat(last["gameDate"].replace("Z", "+00:00"))
            from zoneinfo import ZoneInfo
            last_et = last_dt.astimezone(ZoneInfo("America/New_York"))
            if last_et.hour >= 21:  # 9pm ET or later start
                flags.append("short turnaround (late game yesterday)")
        except Exception:
            pass

        # extra innings hangover
        linescore = last.get("linescore", {})
        innings = linescore.get("currentInning") or len(linescore.get("innings", []) or [])
        if innings and innings > 9:
            flags.append(f"played {innings} innings yesterday")

        # rough time-zone shift: opponent's zone yesterday vs today's actual venue zone
        opp = last.get("teams", {}).get("away" if is_home(last) else "home", {}) \
                  .get("team", {}).get("abbreviation")
        prev_zone = TEAM_TZ.get(team_abbr) if is_home(last) else TEAM_TZ.get(opp)
        today_zone = TEAM_TZ.get(today_venue_abbr)
        if prev_zone and today_zone and prev_zone != today_zone:
            dist = abs(TZ_ORDER.get(prev_zone, 0) - TZ_ORDER.get(today_zone, 0))
            if dist >= 2:
                flags.append(f"crossed {dist} time zones since last game")

        return {"streak": f"{streak_len} straight {streak_type}", "flags": flags}
    except Exception:
        return None



def deg_to_compass(deg):
    """0-360 -> 16-point compass label."""
    dirs = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
            "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"]
    return dirs[round(deg / 22.5) % 16]


def classify_wind_vs_park(wind_from_deg, cf_bearing):
    """wind_from_deg = meteorological 'from' direction. Returns a short
    park-relative label using the verified CF_BEARING for that park."""
    blow_to = (wind_from_deg + 180) % 360
    signed_diff = ((blow_to - cf_bearing + 180) % 360) - 180  # -180..180
    abs_diff = abs(signed_diff)
    if abs_diff <= 40:
        return "blowing OUT (toward CF)"
    if abs_diff >= 140:
        return "blowing IN (from CF)"
    return "blowing L\u2192R (toward RF)" if signed_diff > 0 else "blowing R\u2192L (toward LF)"


def get_game_weather(team_abbr, game_date_iso):
    """Fetch temp/wind/precip at this venue for the exact first-pitch hour.
    Returns a dict, or a dome/retractable marker, or None on failure."""
    park = STADIUM.get(team_abbr)
    if not park:
        return None
    lat, lon, roof = park
    if roof == "dome":
        return {"roof": "dome"}

    try:
        game_dt = datetime.fromisoformat(game_date_iso.replace("Z", "+00:00"))
        r = session.get(
            WEATHER_BASE,
            params={
                "latitude": lat, "longitude": lon,
                "hourly": "temperature_2m,wind_speed_10m,wind_direction_10m,"
                          "precipitation_probability",
                "temperature_unit": "fahrenheit", "wind_speed_unit": "mph",
                "timezone": "UTC",
                "start_date": game_dt.strftime("%Y-%m-%d"),
                "end_date": game_dt.strftime("%Y-%m-%d"),
            },
            timeout=30,
        )
        r.raise_for_status()
        hourly = r.json().get("hourly", {})
        times = hourly.get("time", [])
        if not times:
            return None
        # find the hour closest to first pitch
        target = game_dt.strftime("%Y-%m-%dT%H:00")
        idx = times.index(target) if target in times else min(
            range(len(times)),
            key=lambda i: abs(datetime.fromisoformat(times[i]) - game_dt.replace(tzinfo=None))
        )
        wind_dir = hourly["wind_direction_10m"][idx]
        result = {
            "roof": roof,
            "temp_f": round(hourly["temperature_2m"][idx]),
            "wind_mph": round(hourly["wind_speed_10m"][idx]),
            "wind_from_deg": wind_dir,
            "wind_from_compass": deg_to_compass(wind_dir),
            "precip_pct": hourly["precipitation_probability"][idx],
        }
        if team_abbr in CF_BEARING:
            result["park_relative"] = classify_wind_vs_park(wind_dir, CF_BEARING[team_abbr])
        return result
    except Exception:
        return None


def fmt_weather(w):
    if not w:
        return "  Weather: unavailable"
    if w.get("roof") == "dome":
        return "  Weather: Dome \u2014 no weather factor"
    line = (f"  Weather: {w['temp_f']}\u00b0F, wind {w['wind_mph']} mph from "
            f"{w['wind_from_compass']} ({w['wind_from_deg']}\u00b0), "
            f"{w['precip_pct']}% precip")
    if w.get("roof") == "retractable":
        line += "  [retractable roof \u2014 status unknown, showing outdoor conditions]"
    if "park_relative" in w:
        line += f"\n    \u2192 {w['park_relative']}"
    return line


def fmt_team_context(team_name, team_id, team_abbr, as_of_date, today_venue_abbr):
    """One compact block: recent offensive form, bullpen fatigue, schedule flags."""
    lines = [f"  [{team_name} — last {FORM_LOOKBACK_GAMES} games]"]

    if INCLUDE_TEAM_FORM:
        form10 = get_team_recent_form(team_id, as_of_date, FORM_LOOKBACK_GAMES)
        form5 = get_team_recent_form(team_id, as_of_date, FORM_LOOKBACK_GAMES_SHORT)
        if form10:
            slump = ""
            try:
                if form10["runs_per_game"] < 3.5:
                    slump = "  \u26a0\ufe0f offense cold"
                elif form10["runs_per_game"] >= 5.5:
                    slump = "  \U0001f525 offense hot"
            except Exception:
                pass
            lines.append(f"    Offense (L10): {form10['runs_per_game']} R/G, "
                         f"{form10['avg']} AVG, {form10['ops']} OPS "
                         f"({form10['games']} G){slump}")
        else:
            lines.append("    Offense (L10): no recent-form data available")

        if form5:
            trend = ""
            if form10 and form10.get("runs_per_game") is not None:
                try:
                    delta = form5["runs_per_game"] - form10["runs_per_game"]
                    if delta >= 1.0:
                        trend = "  \U0001f4c8 trending UP"
                    elif delta <= -1.0:
                        trend = "  \U0001f4c9 trending DOWN"
                except Exception:
                    pass
            lines.append(f"    Offense (L5):  {form5['runs_per_game']} R/G, "
                         f"{form5['avg']} AVG, {form5['ops']} OPS "
                         f"({form5['games']} G){trend}")
        else:
            lines.append("    Offense (L5):  no recent-form data available")

    if INCLUDE_BULLPEN_FATIGUE:
        bp = get_bullpen_fatigue(team_id, as_of_date)
        if bp:
            tax = f" — TAXED: {', '.join(bp['taxed_relievers'])}" if bp["taxed_relievers"] else ""
            lines.append(f"    Bullpen: {bp['bullpen_ip']} IP over last "
                         f"{bp['games_checked']} games{tax}")
        else:
            lines.append("    Bullpen: no recent data available")

    if INCLUDE_SCHEDULE_CONTEXT:
        ctx = get_schedule_context(team_id, team_abbr, as_of_date, today_venue_abbr)
        if ctx:
            flag_txt = f" — {'; '.join(ctx['flags'])}" if ctx["flags"] else ""
            lines.append(f"    Schedule: {ctx['streak']}{flag_txt}")
        else:
            lines.append("    Schedule: no data available")

    return "\n".join(lines)


def fmt_line(name, bvp):
    if not bvp or bvp["ab"] == 0:
        if bvp and bvp.get("pa", 0) > 0:
            return f"    {name:<24} {bvp['pa']} PA, 0 AB (walks only)"
        return f"    {name:<24} no career history"
    try:
        avg_val = float(bvp["avg"]) if bvp["avg"] not in ("-", ".---", None) else 0.0
    except Exception:
        avg_val = 0.0
    # Confidence tier: BvP is a CONFIRMING signal, not a primary one — only
    # flag it at all past MIN_AB_FLAG, and mark whether the sample is
    # moderate (20-39 AB) or strong (40+ AB) confidence.
    flag = ""
    if bvp["ab"] >= MIN_AB_FLAG:
        tier = " \U0001f4aa" if bvp["ab"] >= STRONG_AB_FLAG else " \u25cb"  # 💪 strong / ○ moderate
        if avg_val >= GOOD_AVG_FLAG:
            flag += f"  \u2b50HOT{tier}"
        if bvp["hr"] >= 2:
            flag += f"  \U0001f4a3{tier}"
        if bvp["so"] >= max(4, bvp["ab"] * 0.35):
            flag += f"  \u2744\ufe0fK{tier}"
    return (f"    {name:<24} {bvp['ab']:>2} AB, {str(bvp['avg']):>5} AVG, "
            f"{bvp['hr']} HR, {bvp['so']} K{flag}")


def lineup(game, side):
    lu = game.get("lineups", {}) or {}
    return lu.get("homePlayers" if side == "home" else "awayPlayers", []) or []


def print_matchup(team_name, batters, pitcher):
    """Print each hitter's BvP line, then a TEAM aggregate vs this pitcher."""
    print(f"\n  {team_name} hitters vs {pitcher['fullName']}:")
    tot_ab = tot_h = tot_hr = tot_so = 0
    with_history = 0
    for b in batters:
        bvp = get_bvp(b["id"], pitcher["id"])
        time.sleep(SLEEP)
        print(fmt_line(b["fullName"], bvp))
        if bvp and bvp["ab"] > 0:
            with_history += 1
            tot_ab += bvp["ab"]; tot_h += bvp["hits"]
            tot_hr += bvp["hr"]; tot_so += bvp["so"]
    # Aggregate line — the lineup's collective history vs this arm
    if tot_ab > 0:
        avg_val = tot_h / tot_ab
        team_avg = f"{avg_val:.3f}".lstrip("0")  # e.g. ".264"
        k_rate = tot_so / tot_ab
        verdict = ""
        if tot_ab >= 40:  # only judge when the sample is real
            if avg_val >= 0.270 and tot_hr >= 3:
                verdict = "  \u2b50 lineup MASHES him"
            elif avg_val <= 0.210 or k_rate >= 0.30:
                verdict = "  \u2744\ufe0f lineup STRUGGLES vs him"
        print(f"    {'--- TEAM TOTAL ---':<24} {tot_ab:>3} AB, {team_avg} AVG, "
              f"{tot_hr} HR, {tot_so} K  ({with_history} hitters w/ history){verdict}")
    else:
        print(f"    {'--- TEAM TOTAL ---':<24} no meaningful history vs this pitcher")


def main():
    date = DATE or eastern_today()
    print(f"===== MLB Matchups + Batter-vs-Pitcher — {date} =====")
    print("Priority order: pitcher form > L5 offense > L10 offense > bullpen "
          "> weather > BvP (confirmation only)")
    print(f"BvP flags (need {MIN_AB_FLAG}+ career AB):  \u2b50HOT = .300+ AVG   "
          f"\U0001f4a3 = 2+ HR   \u2744\ufe0fK = strikeout-prone   "
          f"[\u25cb = moderate sample {MIN_AB_FLAG}-{STRONG_AB_FLAG-1} AB, "
          f"\U0001f4aa = strong sample {STRONG_AB_FLAG}+ AB]\n")

    games = get_schedule(date)
    if not games:
        print("No games found (schedule may not be posted yet).")
        return

    # Quick pitching-matchup summary first
    print("PITCHING MATCHUPS")
    for g in games:
        try:
            a = g["teams"]["away"]; h = g["teams"]["home"]
            ap = a.get("probablePitcher"); hp = h.get("probablePitcher")
            print(f"  {a['team']['name']} ({ap['fullName'] if ap else 'TBD'})"
                  f"  @  {h['team']['name']} ({hp['fullName'] if hp else 'TBD'})")
        except Exception:
            continue
    print()

    # Detailed BvP
    for g in games:
        try:
            a = g["teams"]["away"]; h = g["teams"]["home"]
            an = a["team"]["name"]; hn = h["team"]["name"]
            a_id = a["team"].get("id"); h_id = h["team"].get("id")
            a_abbr = a["team"].get("abbreviation"); h_abbr = h["team"].get("abbreviation")
            ap = a.get("probablePitcher"); hp = h.get("probablePitcher")
            print("=" * 62)
            print(f"{an} @ {hn}")

            if INCLUDE_WEATHER:
                print(fmt_weather(get_game_weather(h_abbr, g.get("gameDate", ""))))

            if INCLUDE_TEAM_FORM or INCLUDE_BULLPEN_FATIGUE or INCLUDE_SCHEDULE_CONTEXT:
                # today's venue = the HOME team's city, for both sides' TZ check
                print(fmt_team_context(an, a_id, a_abbr, date, h_abbr))
                print(fmt_team_context(hn, h_id, h_abbr, date, h_abbr))

            if ap and lineup(g, "home"):
                print_matchup(hn, lineup(g, "home"), ap)
            elif ap:
                print(f"\n  {hn} lineup not posted yet (SP: {ap['fullName']}).")

            if hp and lineup(g, "away"):
                print_matchup(an, lineup(g, "away"), hp)
            elif hp:
                print(f"\n  {an} lineup not posted yet (SP: {hp['fullName']}).")
            print()
        except Exception as e:
            print(f"  (skipped a game due to error: {e})")
            continue


main()
