"""
MLB BvP Report — mobile web app with AI-built picks.

Flow:
  1. User sets which signals matter most + what bet types they want.
  2. App runs mlb_bvp_report.py (unchanged data engine) and captures output.
  3. App sends that data + the user's preferences to Claude via the API.
  4. Claude returns the day's picks, shown in a clean mobile page.

Requires an environment variable ANTHROPIC_API_KEY set in Render
(Dashboard -> your service -> Environment -> Add Environment Variable).
"""
import io
import os
import sys
import re
import json
import contextlib
import importlib
import requests
from flask import Flask, Response, request

app = Flask(__name__)

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
ANTHROPIC_MODEL = "claude-sonnet-5"

PAGE_TOP = """
<!doctype html>
<html>
<head>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Matchup Report</title>
  <style>
    * { box-sizing: border-box; }
    body {
      font-family: -apple-system, system-ui, sans-serif; margin:0; color:#eee;
      min-height:100vh;
      background-color:#0d1526;
      background-image:
        radial-gradient(ellipse 60% 40% at 20% 0%, rgba(255,176,32,.16), transparent 60%),
        radial-gradient(ellipse 60% 40% at 80% 0%, rgba(255,176,32,.12), transparent 60%),
        repeating-linear-gradient(0deg, rgba(255,255,255,.025) 0px, rgba(255,255,255,.025) 1px,
          transparent 1px, transparent 34px),
        linear-gradient(180deg, #0d1526 0%, #0a1020 55%, #070b16 100%);
      background-attachment: fixed;
    }
    .wrap { padding: 18px 16px 40px; max-width: 640px; margin: 0 auto; }
    button, .btn { display:block; width:100%; padding:16px; font-size:17px; font-weight:700;
           background:#ffb020; color:#0d1526; border:none; border-radius:12px;
           text-align:center; text-decoration:none; margin-bottom:12px;
           cursor:pointer; letter-spacing:.01em; }
    .btn.secondary { background:rgba(255,255,255,.08); color:#dfe6f5;
           border:1px solid rgba(255,255,255,.12); }
    pre { white-space: pre-wrap; word-wrap: break-word; font-size:13px;
          line-height:1.35; background:#0f1626; padding:12px; border-radius:8px;
          border:1px solid rgba(255,255,255,.06); }
    h1 { font-size:24px; font-weight:800; font-style:italic; letter-spacing:-.01em;
         color:#fff; margin:4px 0 18px; }
    fieldset { border:1px solid rgba(255,255,255,.12); border-radius:12px; padding:12px 14px;
               margin-bottom:16px; background:rgba(255,255,255,.03); }
    legend { padding:0 6px; color:#ffb020; font-size:12px; font-weight:700;
             text-transform:uppercase; letter-spacing:.06em; }
    label { display:flex; align-items:center; gap:10px; padding:9px 0;
            font-size:15px; color:#dfe6f5; }
    input[type=checkbox] { width:20px; height:20px; accent-color:#ffb020; }
    .out { animation: fade .3s ease-in; }
    @keyframes fade { from {opacity:0} to {opacity:1} }
    .section-title { background:#ffb020; color:#0d1526; font-size:11px; font-weight:800;
           letter-spacing:.08em; text-transform:uppercase; display:inline-block;
           padding:4px 10px; border-radius:4px; margin:20px 0 10px; }
    .card { background:linear-gradient(135deg,#16213d,#131b30); border-radius:14px;
           padding:14px 16px; margin-bottom:10px; box-shadow:0 2px 10px rgba(0,0,0,.35);
           border:1px solid rgba(255,255,255,.05); }
    .pick-name { color:#fff; font-weight:800; font-size:16px;
           display:block; margin-bottom:6px; }
    .reason { color:#9fb0d0; font-size:13.5px; line-height:1.45; display:block; margin-top:6px; }
    .conf { display:inline-block; margin-top:10px; padding:3px 10px;
           border-radius:20px; font-size:10.5px; font-weight:800;
           text-transform:uppercase; letter-spacing:.04em; }
    .conf-high { background:#0f3d2b; color:#4ade80; }
    .conf-medium { background:#3d3208; color:#ffb020; }
    .conf-low { background:#3d1616; color:#f87171; }
    .empty-note { color:#7a88a8; font-size:14px; font-style:italic; }
    ul.legs { margin:8px 0 0; padding-left:16px; }
    ul.legs li { color:#9fb0d0; font-size:12.5px; line-height:1.6; }
    details { margin-top:24px; border:1px solid rgba(255,255,255,.12); border-radius:12px;
           padding:10px 14px; background:rgba(255,255,255,.03); }
    summary { color:#ffb020; font-size:14px; cursor:pointer; padding:6px 0; font-weight:600; }
    details pre { margin-top:10px; }
  </style>
</head>
<body><div class="wrap">
"""
PAGE_BOTTOM = "</div></body></html>"

SIGNALS = [
    ("pitcher_form", "Starting pitcher current form"),
    ("l5_offense", "Team offense — last 5 games"),
    ("l10_offense", "Team offense — last 10 games"),
    ("bullpen", "Bullpen usage / fatigue"),
    ("weather", "Weather / park factors"),
    ("bvp", "Batter-vs-pitcher history"),
]

BET_TYPES = [
    ("hits", "Hit picks"),
    ("hr", "Home run picks"),
    ("parlays", "Parlays (ML / run line / totals)"),
    ("props", "Strikeout / RBI-type props"),
]


def run_report_and_capture():
    """Runs the existing data-gathering script and captures its printed output.
    Imports it once, then reloads on subsequent calls — importing AND
    reloading in the same call would run main() twice and duplicate output."""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        if "mlb_bvp_report" in sys.modules:
            importlib.reload(sys.modules["mlb_bvp_report"])
        else:
            import mlb_bvp_report  # noqa: F401 (import itself runs main())
    return buf.getvalue()


def build_prompt(report_text, signal_weights, bet_types):
    """Builds the instructions sent to Claude, based on the user's chosen
    signal priority and preferred bet types."""
    ranked = [label for key, label in SIGNALS if key in signal_weights]
    unranked = [label for key, label in SIGNALS if key not in signal_weights]
    priority_lines = "\n".join(f"{i+1}. {label}" for i, label in enumerate(ranked))
    if unranked:
        priority_lines += "\n(Deprioritized / ignore unless nothing else stands out: "
        priority_lines += ", ".join(unranked) + ")"

    wanted_bets = ", ".join(label for key, label in BET_TYPES if key in bet_types) \
        or "hit picks, HR picks, and parlays"

    instructions = f"""You are building today's MLB betting card from the data below.

SIGNAL PRIORITY (most important first):
{priority_lines}

The best plays have multiple independent signals aligning. Batter-vs-pitcher
history is a confirming signal only, never the primary reason for a pick,
unless the user explicitly ranked it above other signals. Small BvP samples
(under 20 AB) should not be trusted.

Only build these bet types: {wanted_bets}.

For "Hit Picks" specifically: the app will show your 2 highest-scored, so
try to surface at least 2 real candidates when the data supports it,
rather than stopping after finding just one strong option — but never
pad with a weak pick just to hit that count.

For "Parlays": actively look for a real 2-4 leg combination before
concluding none exists — check across all qualifying games for legs that
would score 6+ individually before deciding to omit the section. Only
omit it if you genuinely can't find enough independently-supported legs.

Only build picks from games that clearly haven't started yet — if any data
looks like it's from an in-progress or finished game (duplicated stat
lines, empty lineups where one should exist, etc.), skip that game
entirely.

--- REAL NUMBERS ONLY ---
Only state a specific point total, spread, or moneyline price if it
appears verbatim in the "Odds" line for that game in the raw data below.
Never invent, estimate, or round a number that isn't explicitly given. If
a game has no "Odds" line (or it says unavailable/no line data), you may
still make hit/HR/prop picks for it using the stats, but do not build a
Parlays leg for that game and do not state any number for it.

--- BET TYPE CATEGORIES ---
Only put a pick under a section if it actually matches that bet type — a
team total or moneyline pick belongs under "Parlays", never under "Hit
Picks", even if a hit-related signal contributed to the reasoning.
"Hit Picks" means simple "player to record 1+ hit" bets only. Anything
using a combined or multi-stat threshold (e.g. "over 1.5 hits+runs+RBI"),
strikeout props, or total bases props belongs under "Props" instead, even
though it involves a hitter.

Props must be REAL, standard sportsbook prop formats only — one of: a
single player's hits (1+ hit, or over X.5 hits), a single player's home
run (anytime HR), a single player's total bases (over X.5), a single
player's combined hits+runs+RBI (over X.5), a pitcher's strikeouts
(over/under X.5), or a single player's RBI (over X.5). Never invent a
combined prop across MULTIPLE players (e.g. "Player A + Player B combined
hits") — that is not a real market any sportsbook offers.

--- PARLAYS ---
A parlay pick must be an ACTUAL multi-leg parlay: 2 to 4 legs combined
into one bet. Never put a one-leg pick under Parlays. If you don't have
enough real, independently-supported legs, omit the Parlays section
entirely rather than forcing one.

Every leg must be an actual bet a sportsbook would let someone place —
a specific team/player plus a specific outcome (e.g. "Nationals ML",
"James Wood over 1.5 hits+runs+RBI"). Never include a leg that is really
just reasoning restated as if it were a bet (e.g. "Braves offense trending
down supports Nationals" is NOT a leg — that belongs in the "reason"
field, not the "legs" list).

Avoid legs that are the same bet twice in disguise: never combine a team's
moneyline with that same team's run line, and never combine a game's run
line with that same game's total. Everything else is fine to combine,
including multiple legs on the same team/game outside those two combos
(e.g. a team's ML plus a prop from that same game is fine).

Parlay legs should lean primarily on moneyline / run line / totals as the
core — props should be sprinkled in only where there's a genuine standout
edge, never used to fill out the whole parlay. Never build a parlay made
entirely of bare "player to record 1+ hit" props; if using a hit-type
prop as a leg, prefer a real threshold like "over 1.5 hits+runs+RBI" over
a bare 1+ hit prop.

--- OUTPUT FORMAT ---
For every pick, internally rate it 1-10 for how strongly the signals
support it (10 = multiple independent signals strongly aligned, 1 = barely
any support). Include that as a numeric "score" field. Do not filter or
sort based on this yourself — just score honestly and include everything
you'd otherwise have built; the app will handle filtering and ordering
using your scores.

Never reveal your own deliberation, hedging, or what you considered and
rejected inside a pick's "pick" or "reason" text — e.g. never write
something like "X prop not offered, using Y instead." If a bet type isn't
valid or available, simply don't mention it at all; every pick must read
as a clean, standalone recommendation with no trace of what you ruled out
along the way.

Respond with ONLY valid JSON, no markdown fences, no commentary before or
after. Use exactly this shape:
{{
  "sections": [
    {{
      "title": "Hit Picks",
      "picks": [
        {{"pick": "Short pick description, e.g. player/team + bet",
          "reason": "One tight sentence citing the actual signals used",
          "score": 8}}
      ]
    }},
    {{
      "title": "Parlays",
      "picks": [
        {{"pick": "2-4 leg parlay name, e.g. '3-leg parlay'",
          "legs": ["Leg 1 description", "Leg 2 description", "Leg 3 description"],
          "reason": "One tight sentence on why these legs combine well",
          "score": 7}}
      ]
    }}
  ]
}}
Only include sections for bet types you were asked to build, and omit a
section entirely if it has zero real picks rather than inventing one.
score must be an integer 1-10. Only include "legs" for Parlays picks —
omit it for every other bet type.

RAW DATA:
{report_text}
"""
    return instructions


def call_claude(prompt):
    """Returns (picks_json_or_None, error_message_or_None)."""
    if not ANTHROPIC_API_KEY:
        return None, ("No ANTHROPIC_API_KEY is set on the server. Add it in "
                       "Render under Environment, then redeploy.")
    try:
        resp = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": ANTHROPIC_MODEL,
                "max_tokens": 8000,
                "thinking": {"type": "disabled"},
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=120,
        )
        data = resp.json()
        if resp.status_code != 200:
            return None, f"API error ({resp.status_code}): {json.dumps(data)[:1500]}"
        parts = [b.get("text", "") for b in data.get("content", []) if b.get("type") == "text"]
        if not parts:
            return None, f"(No text returned by the model.) Raw response: {json.dumps(data)[:1500]}"
        text = "\n".join(parts).strip()
        # Strip stray markdown fences if the model adds them despite instructions
        if text.startswith("```"):
            text = text.strip("`")
            text = text.split("\n", 1)[1] if "\n" in text else text
            if text.rstrip().endswith("```"):
                text = text.rstrip()[:-3]
        try:
            parsed = json.loads(text)
            return parsed, None
        except json.JSONDecodeError:
            return None, f"Model didn't return valid JSON. Raw text: {text[:1500]}"
    except Exception as e:
        return None, f"Error calling Claude API: {e}"


def escape_html(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


MIN_SCORE_TO_SHOW = 6   # drop anything scoring below this — no "low confidence" picks at all
SECTION_LIMITS = {"Hit Picks": 2}  # cap certain sections to their N highest-scored picks


def score_to_label(score):
    return "high" if score >= 8 else "medium"  # anything shown at all is already 8+ (see MIN_SCORE_TO_SHOW)


def _extract_ml_team(leg):
    """Returns the team name if this leg is a moneyline bet, else None."""
    m = re.match(r'^(.*?)\s+ML$', leg.strip(), re.IGNORECASE)
    return m.group(1).strip().lower() if m else None


def _extract_runline_team(leg):
    """Returns the team name if this leg is a run line / spread bet, else
    None. Deliberately excludes Over/Under legs (those are totals, not
    team spreads, and are fine to combine with a team's ML)."""
    stripped = leg.strip()
    if re.match(r'^(over|under)\b', stripped, re.IGNORECASE):
        return None
    m = re.match(r'^(.*?)\s+[+-]\d+(\.\d+)?', stripped)
    return m.group(1).strip().lower() if m else None


def fix_parlay_legs(legs):
    """Code-enforced backstop for the same-team ML+run-line rule — the
    prompt asks Claude to avoid this, but it's slipped through before, so
    this catches and fixes it deterministically rather than trusting the
    model alone. Keeps the ML leg and drops the redundant run line leg for
    any team that has both."""
    if not legs or not isinstance(legs, list):
        return legs
    ml_teams = set()
    rl_indices = {}
    for i, leg in enumerate(legs):
        if not isinstance(leg, str):
            continue
        ml_team = _extract_ml_team(leg)
        if ml_team:
            ml_teams.add(ml_team)
        rl_team = _extract_runline_team(leg)
        if rl_team:
            rl_indices.setdefault(rl_team, []).append(i)
    drop = set()
    for team, idxs in rl_indices.items():
        if team in ml_teams:
            drop.update(idxs)
    return [leg for i, leg in enumerate(legs) if i not in drop]


def render_picks(parsed):
    sections = parsed.get("sections", [])
    if not sections:
        return '<p class="empty-note">No picks came back — try different settings or check back closer to first pitch.</p>'
    html_parts = []
    any_shown = False
    for section in sections:
        title_raw = section.get("title", "Picks")
        title = escape_html(title_raw)
        raw_picks = section.get("picks", [])

        # Score, filter, sort, and cap — enforced here rather than trusting
        # the model to self-police "don't show weak picks."
        scored = []
        for p in raw_picks:
            try:
                score = int(p.get("score", 0))
            except (TypeError, ValueError):
                score = 0
            if score < MIN_SCORE_TO_SHOW:
                continue
            legs = p.get("legs")
            if legs and isinstance(legs, list):
                fixed_legs = fix_parlay_legs(legs)
                if len(fixed_legs) < 2:
                    continue  # fixing the ML+run-line conflict left too few real legs — drop this pick
                new_title = re.sub(r'^\d+-leg\b', f'{len(fixed_legs)}-leg', p.get("pick", ""), flags=re.IGNORECASE)
                if not re.match(r'^\d+-leg\b', new_title, re.IGNORECASE):
                    new_title = f'{len(fixed_legs)}-leg parlay'
                p = {**p, "legs": fixed_legs, "pick": new_title}
            scored.append((score, p))
        scored.sort(key=lambda t: t[0], reverse=True)
        limit = SECTION_LIMITS.get(title_raw)
        if limit:
            scored = scored[:limit]

        if not scored:
            continue  # nothing cleared the bar — skip the section entirely rather than showing an empty one

        any_shown = True
        html_parts.append(f'<div class="section-title">{title}</div>')
        for score, p in scored:
            pick = escape_html(p.get("pick", ""))
            reason = escape_html(p.get("reason", ""))
            conf = score_to_label(score)
            legs = p.get("legs")
            legs_html = ""
            if legs and isinstance(legs, list):
                legs_html = "<ul class='legs'>" + "".join(
                    f"<li>{escape_html(leg)}</li>" for leg in legs
                ) + "</ul>"
            html_parts.append(
                f'<div class="card"><span class="pick-name">{pick}</span>'
                f'{legs_html}'
                f'<span class="reason">{reason}</span>'
                f'<span class="conf conf-{conf}">{conf}</span></div>'
            )
    if not any_shown:
        return ('<p class="empty-note">No picks cleared the confidence bar today — '
                'try again closer to first pitch, or check different games.</p>')
    return "".join(html_parts)


def settings_form(action="/run"):
    signal_checks = "".join(
        f'<label><input type="checkbox" name="signal" value="{key}" checked> {label}</label>'
        for key, label in SIGNALS
    )
    bet_checks = "".join(
        f'<label><input type="checkbox" name="bet" value="{key}" checked> {label}</label>'
        for key, label in BET_TYPES
    )
    return f"""
    <h1>Matchup Report</h1>
    <form method="get" action="{action}">
      <fieldset>
        <legend>Signals to weigh (checked = in priority order, top to bottom)</legend>
        {signal_checks}
      </fieldset>
      <fieldset>
        <legend>Bet types to build</legend>
        {bet_checks}
      </fieldset>
      <button type="submit">Build Today's Picks</button>
    </form>
    """


@app.route("/")
def home():
    return Response(PAGE_TOP + settings_form() + PAGE_BOTTOM, mimetype="text/html")


@app.route("/run")
def run():
    signals = request.args.getlist("signal") or [k for k, _ in SIGNALS]
    bets = request.args.getlist("bet") or [k for k, _ in BET_TYPES]

    try:
        report_text = run_report_and_capture()
    except Exception as e:
        report_text = ""
        data_error = f"Error running data collection: {e}"
    else:
        data_error = None if report_text.strip() else "No data returned."

    if data_error:
        body = f'<p class="empty-note">{escape_html(data_error)}</p>'
    else:
        prompt = build_prompt(report_text, signals, bets)
        parsed, err = call_claude(prompt)
        if err:
            body = f'<pre>{escape_html(err)}</pre>'
        else:
            body = render_picks(parsed)

    query = "&".join([f"signal={s}" for s in signals] + [f"bet={b}" for b in bets])
    raw_block = ""
    if report_text.strip():
        raw_block = (
            '<details><summary>View raw data pulled (all games, '
            'lineups, form, weather, etc.)</summary>'
            f'<pre>{escape_html(report_text)}</pre></details>'
        )
    html = (
        PAGE_TOP
        + '<h1>Today\'s Picks</h1>'
        + f'<a class="btn" href="/run?{query}">Refresh</a>'
        + '<a class="btn secondary" href="/">Change Settings</a>'
        + f'<div class="out">{body}</div>'
        + raw_block
        + PAGE_BOTTOM
    )
    return Response(html, mimetype="text/html")


if __name__ == "__main__":
    app.run(debug=True)
