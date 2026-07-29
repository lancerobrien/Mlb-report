"""
One-button mobile web app for the MLB BvP report.
Wraps mlb_bvp_report.py's logic (unchanged) and serves it as a simple
mobile-friendly page: tap a button, see today's report.
"""
import io
import contextlib
import importlib
from flask import Flask, Response

app = Flask(__name__)

PAGE_TOP = """
<!doctype html>
<html>
<head>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>MLB Report</title>
  <style>
    body { font-family: -apple-system, system-ui, sans-serif; margin:0;
           background:#111; color:#eee; }
    .wrap { padding: 16px; }
    button, .btn { display:block; width:100%; padding:18px; font-size:20px;
           background:#2563eb; color:white; border:none; border-radius:12px;
           text-align:center; text-decoration:none; margin-bottom:12px; }
    pre { white-space: pre-wrap; word-wrap: break-word; font-size:13px;
          line-height:1.35; background:#1c1c1c; padding:12px; border-radius:8px; }
  </style>
</head>
<body><div class="wrap">
"""
PAGE_BOTTOM = "</div></body></html>"


def run_report_and_capture():
    """Runs the existing script's main() and captures its printed output."""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        import mlb_bvp_report as rpt
        importlib.reload(rpt)  # re-run main() fresh each request
    return buf.getvalue()


@app.route("/")
def home():
    return Response(
        PAGE_TOP + '<a class="btn" href="/run">&#9654; Run Today\'s Report</a>'
        + PAGE_BOTTOM,
        mimetype="text/html",
    )


@app.route("/run")
def run():
    try:
        output = run_report_and_capture()
    except Exception as e:
        output = f"Error running report: {e}"
    html = (
        PAGE_TOP
        + '<a class="btn" href="/run">&#8635; Refresh</a>'
        + f"<pre>{output}</pre>"
        + PAGE_BOTTOM
    )
    return Response(html, mimetype="text/html")


if __name__ == "__main__":
    app.run(debug=True)
