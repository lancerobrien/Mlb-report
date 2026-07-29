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

Output format: a short, clean, mobile-readable list. For each pick, give the
pick itself, a one-line reason referencing the actual signals above, and a
rough confidence (low/medium/high). Keep the whole thing tight — no filler,
no disclaimers, no repeating the raw data back.

RAW DATA:
{report_text}
"""
    return instructions


def call_claude(prompt):
    if not ANTHROPIC_API_KEY:
        return ("No ANTHROPIC_API_KEY is set on the server. Add it in Render "
                "under Environment, then redeploy.")
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
                "max_tokens": 4096,
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=120,
        )
        data = resp.json()
        if resp.status_code != 200:
            return f"API error ({resp.status_code}): {json.dumps(data)[:1500]}"
        parts = [b.get("text", "") for b in data.get("content", []) if b.get("type") == "text"]
        if parts:
            return "\n".join(parts)
        return f"(No text returned by the model.) Raw response: {json.dumps(data)[:1500]}"
    except Exception as e:
        return f"Error calling Claude API: {e}"


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
        report_text = f"Error running data collection: {e}"

    if report_text.strip() and "Error running" not in report_text:
        prompt = build_prompt(report_text, signals, bets)
        picks = call_claude(prompt)
    else:
        picks = report_text or "No data returned."

    query = "&".join([f"signal={s}" for s in signals] + [f"bet={b}" for b in bets])
    html = (
        PAGE_TOP
        + '<h1>&#9918; Today\'s Picks</h1>'
        + f'<a class="btn" href="/run?{query}">&#8635; Refresh</a>'
        + '<a class="btn secondary" href="/">&#9881; Change Settings</a>'
        + f'<div class="out"><pre>{picks}</pre></div>'
        + PAGE_BOTTOM
    )
    return Response(html, mimetype="text/html")


if __name__ == "__main__":
    app.run(debug=True)
