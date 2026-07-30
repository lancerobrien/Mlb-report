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
  <title>MLB Report</title>
  <style>
    body { font-family: -apple-system, system-ui, sans-serif; margin:0;
           background:#111; color:#eee; }
    .wrap { padding: 16px; max-width: 640px; margin: 0 auto; }
    button, .btn { display:block; width:100%; padding:16px; font-size:18px;
           background:#2563eb; color:white; border:none; border-radius:12px;
           text-align:center; text-decoration:none; margin-bottom:12px;
           cursor:pointer; }
    .btn.secondary { background:#333; }
    pre { white-space: pre-wrap; word-wrap: break-word; font-size:13px;
          line-height:1.35; background:#1c1c1c; padding:12px; border-radius:8px; }
    h1 { font-size:20px; }
    h2 { font-size:16px; color:#9ab; margin-top:24px; }
    fieldset { border:1px solid #333; border-radius:10px; padding:12px;
               margin-bottom:16px; }
    legend { padding:0 6px; color:#9ab; font-size:14px; }
    label { display:flex; align-items:center; gap:10px; padding:8px 0;
            font-size:15px; }
    input[type=checkbox] { width:20px; height:20px; }
    select, input[type=range] { width:100%; }
    .weight-row { margin-bottom:14px; }
    .weight-row .label-line { display:flex; justify-content:space-between;
            font-size:14px; margin-bottom:4px; }
    .out { animation: fade .3s ease-in; }
    @keyframes fade { from {opacity:0} to {opacity:1} }
    .section-title { font-size:13px; text-transform:uppercase; letter-spacing:.06em;
           color:#7a8aa8; margin:22px 0 10px; font-weight:600; }
    .card { background:#1c1c1c; border-radius:12px; padding:14px;
           margin-bottom:10px; border-left:3px solid #2563eb; }
    .pick-name { color:#4dd0e1; font-weight:700; font-size:16px;
           display:block; margin-bottom:6px; }
    .reason { color:#c7ccd6; font-size:14px; line-height:1.4; }
    .conf { display:inline-block; margin-top:8px; padding:3px 10px;
           border-radius:20px; font-size:11px; font-weight:700;
           text-transform:uppercase; letter-spacing:.03em; }
    .conf-high { background:#164e2b; color:#4ade80; }
    .conf-medium { background:#4a3b12; color:#fbbf24; }
    .conf-low { background:#3a1c1c; color:#f87171; }
    .empty-note { color:#666; font-size:14px; font-style:italic; }
    details { margin-top:24px; border:1px solid #333; border-radius:10px;
           padding:10px 14px; }
    summary { color:#9ab; font-size:14px; cursor:pointer; padding:6px 0; }
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
    """Runs the existing data-gathering script and captures its printed output."""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        import mlb_bvp_report as rpt
        importlib.reload(rpt)  # re-run main() fresh each request
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

Apply this priority order for weighing signals (most important first):
{priority_lines}

The best plays have multiple independent signals aligning. Batter-vs-pitcher
history should only be treated as a confirming signal, never the primary
reason for a pick, unless the user has explicitly ranked it above other
signals. Small BvP samples (under 20 AB) should not be trusted.

Only build these bet types: {wanted_bets}.

Skip any game where the data is missing, corrupted (e.g. an already-finished
game with a duplicated stat line), or too thin to support a real pick.

Only put a pick under a section if it actually matches that bet type — a
team total or moneyline pick belongs under "Parlays", never under
"Hit Picks", even if a hit-related signal contributed to the reasoning.

Respond with ONLY valid JSON, no markdown fences, no commentary before or
after. Use exactly this shape:
{{
  "sections": [
    {{
      "title": "Hit Picks",
      "picks": [
        {{"pick": "Short pick description, e.g. player/team + bet",
          "reason": "One tight sentence citing the actual signals used",
          "confidence": "high"}}
      ]
    }}
  ]
}}
Only include sections for bet types you were asked to build, and omit a
section entirely if it has zero real picks rather than inventing one.
confidence must be exactly one of: "high", "medium", "low".

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


def render_picks(parsed):
    sections = parsed.get("sections", [])
    if not sections:
        return '<p class="empty-note">No picks came back — try different settings or check back closer to first pitch.</p>'
    html_parts = []
    for section in sections:
        title = escape_html(section.get("title", "Picks"))
        picks = section.get("picks", [])
        html_parts.append(f'<div class="section-title">{title}</div>')
        if not picks:
            html_parts.append('<p class="empty-note">No picks in this category today.</p>')
            continue
        for p in picks:
            pick = escape_html(p.get("pick", ""))
            reason = escape_html(p.get("reason", ""))
            conf = str(p.get("confidence", "medium")).lower()
            if conf not in ("high", "medium", "low"):
                conf = "medium"
            html_parts.append(
                f'<div class="card"><span class="pick-name">{pick}</span>'
                f'<span class="reason">{reason}</span>'
                f'<span class="conf conf-{conf}">{conf}</span></div>'
            )
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
    <h1>&#9918; MLB Report</h1>
    <form method="get" action="{action}">
      <fieldset>
        <legend>Signals to weigh (checked = in priority order, top to bottom)</legend>
        {signal_checks}
      </fieldset>
      <fieldset>
        <legend>Bet types to build</legend>
        {bet_checks}
      </fieldset>
      <button type="submit">&#9654; Build Today's Picks</button>
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
            '<details><summary>&#128203; View raw data pulled (all games, '
            'lineups, form, weather, etc.)</summary>'
            f'<pre>{escape_html(report_text)}</pre></details>'
        )
    html = (
        PAGE_TOP
        + '<h1>&#9918; Today\'s Picks</h1>'
        + f'<a class="btn" href="/run?{query}">&#8635; Refresh</a>'
        + '<a class="btn secondary" href="/">&#9881; Change Settings</a>'
        + f'<div class="out">{body}</div>'
        + raw_block
        + PAGE_BOTTOM
    )
    return Response(html, mimetype="text/html")


if __name__ == "__main__":
    app.run(debug=True)
