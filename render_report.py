"""Render Claude-written reports into one HTML email with inline charts.

Looks for reports/<id>/ folders that contain analysis.html but no SENT
marker. For each one it:
  1. reads charts.json (optional) and downloads price history,
  2. draws each chart (indicators + Ben's levels) as a PNG,
  3. emails ONE message with the analysis and the charts embedded,
  4. writes reports/<id>/SENT so it is never sent twice.

Chart spec (charts.json) - a list of objects:
  {
    "id": "btc_weekly",              # referenced as <!--chart:btc_weekly-->
    "title": "BTC weekly vs 200-week MA",
    "symbol": "BTC-USD",             # Yahoo ticker, or ratio "ETH-USD/BTC-USD"
    "interval": "1wk",               # 1d | 1wk | 1mo
    "period": "5y",                  # 1mo 3mo 6mo 1y 2y 5y 10y max
    "indicators": ["sma200", "ema21", "bb", "rsi14", "macd"],
    "levels": [{"price": 70000, "label": "Support", "kind": "support"}],
    "log": false,
    "caption": "Why this chart matters"
  }
kind: support | resistance | target | invalidation | level
"""

import glob
import json
import os
import re
import smtplib
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from email.mime.image import MIMEImage
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

REPORT_DIR = "reports"

PERIOD_DAYS = {
    "1mo": 31,
    "3mo": 92,
    "6mo": 183,
    "1y": 366,
    "2y": 731,
    "5y": 1827,
    "10y": 3653,
    "max": None,
}

LEVEL_STYLE = {
    "support": ("#1a7f37", "--"),
    "resistance": ("#cf222e", "--"),
    "target": ("#0969da", ":"),
    "invalidation": ("#8250df", "-."),
    "level": ("#6e7781", "--"),
}


def log(message):
    print(message, flush=True)


# --------------------------------------------------------------------------
# Market data
# --------------------------------------------------------------------------

def fetch_series(symbol, interval):
    """Return (dates, closes) lists from Yahoo Finance for one symbol."""
    url = (
        "https://query1.finance.yahoo.com/v8/finance/chart/"
        f"{urllib.parse.quote(symbol)}"
        f"?range=max&interval={interval}&includePrePost=false"
    )

    last_error = None

    for attempt in range(3):
        try:
            request = urllib.request.Request(
                url,
                headers={"User-Agent": "Mozilla/5.0"},
            )

            with urllib.request.urlopen(request, timeout=45) as response:
                payload = json.loads(response.read().decode("utf-8"))

            result = payload["chart"]["result"][0]
            stamps = result["timestamp"]
            closes = result["indicators"]["quote"][0]["close"]

            dates, values = [], []

            for stamp, close in zip(stamps, closes):
                if close is None:
                    continue

                dates.append(
                    datetime.fromtimestamp(stamp, tz=timezone.utc)
                    .replace(tzinfo=None)
                )
                values.append(float(close))

            if len(values) < 10:
                raise RuntimeError("too little data returned")

            return dates, values

        except (
            urllib.error.URLError,
            TimeoutError,
            KeyError,
            IndexError,
            TypeError,
            ValueError,
            RuntimeError,
        ) as error:
            last_error = error
            time.sleep(2 + attempt * 3)

    raise RuntimeError(f"{symbol}: {last_error}")


def load_prices(symbol, interval):
    """Support plain tickers and ratios such as ETH-USD/BTC-USD."""
    if "/" in symbol:
        top, bottom = symbol.split("/", 1)
        d1, v1 = fetch_series(top.strip(), interval)
        d2, v2 = fetch_series(bottom.strip(), interval)

        lookup = {d.date(): v for d, v in zip(d2, v2)}

        dates, values = [], []

        for d, v in zip(d1, v1):
            denominator = lookup.get(d.date())

            if denominator:
                dates.append(d)
                values.append(v / denominator)

        if len(values) < 10:
            raise RuntimeError(f"{symbol}: ratio has too little data")

        return dates, values

    return fetch_series(symbol, interval)


# --------------------------------------------------------------------------
# Indicators
# --------------------------------------------------------------------------

def sma(values, window):
    import numpy as np

    array = np.asarray(values, dtype=float)
    out = np.full(len(array), np.nan)

    if len(array) >= window:
        cumulative = np.cumsum(np.insert(array, 0, 0.0))
        out[window - 1:] = (
            cumulative[window:] - cumulative[:-window]
        ) / window

    return out


def ema(values, window):
    import numpy as np

    array = np.asarray(values, dtype=float)
    out = np.full(len(array), np.nan)

    if len(array) < window:
        return out

    alpha = 2.0 / (window + 1)
    out[window - 1] = array[:window].mean()

    for i in range(window, len(array)):
        out[i] = alpha * array[i] + (1 - alpha) * out[i - 1]

    return out


def rsi(values, window=14):
    import numpy as np

    array = np.asarray(values, dtype=float)
    out = np.full(len(array), np.nan)

    if len(array) <= window:
        return out

    delta = np.diff(array)
    gain = np.where(delta > 0, delta, 0.0)
    loss = np.where(delta < 0, -delta, 0.0)

    avg_gain = gain[:window].mean()
    avg_loss = loss[:window].mean()

    def value(g, l):
        return 100.0 if l == 0 else 100.0 - 100.0 / (1 + g / l)

    out[window] = value(avg_gain, avg_loss)

    for i in range(window, len(delta)):
        avg_gain = (avg_gain * (window - 1) + gain[i]) / window
        avg_loss = (avg_loss * (window - 1) + loss[i]) / window
        out[i + 1] = value(avg_gain, avg_loss)

    return out


def bollinger(values, window=20, width=2.0):
    import numpy as np

    array = np.asarray(values, dtype=float)
    middle = sma(array, window)
    upper = np.full(len(array), np.nan)
    lower = np.full(len(array), np.nan)

    for i in range(window - 1, len(array)):
        deviation = array[i - window + 1:i + 1].std()
        upper[i] = middle[i] + width * deviation
        lower[i] = middle[i] - width * deviation

    return lower, middle, upper


def macd(values):
    import numpy as np

    fast = ema(values, 12)
    slow = ema(values, 26)
    line = fast - slow

    valid = ~np.isnan(line)
    signal = np.full(len(line), np.nan)

    if valid.sum() >= 9:
        start = int(np.argmax(valid))
        signal[start:] = ema(line[start:], 9)

    return line, signal, line - signal


def parse_indicator(name):
    """'sma200' -> ('sma', 200); 'bb' -> ('bb', 20); 'rsi' -> ('rsi', 14)."""
    match = re.fullmatch(r"([a-z]+)(\d*)", name.strip().lower())

    if not match:
        return None, None

    kind = match.group(1)
    number = int(match.group(2)) if match.group(2) else None

    return kind, number


# --------------------------------------------------------------------------
# Chart drawing
# --------------------------------------------------------------------------

def draw_chart(spec, loader=load_prices):
    """Return (png_bytes, skipped_indicator_notes)."""
    import io

    import matplotlib

    matplotlib.use("Agg")

    import matplotlib.dates as mdates
    import matplotlib.pyplot as plt
    import numpy as np

    interval = spec.get("interval", "1d")
    dates, closes = loader(spec["symbol"], interval)

    notes = []
    wanted = [str(x) for x in spec.get("indicators", [])]

    overlays = []
    panels = []

    for name in wanted:
        kind, number = parse_indicator(name)

        if kind == "sma":
            if number and len(closes) >= number:
                overlays.append((f"SMA {number}", sma(closes, number)))
            else:
                notes.append(f"{name}: not enough history")
        elif kind == "ema":
            if number and len(closes) >= number:
                overlays.append((f"EMA {number}", ema(closes, number)))
            else:
                notes.append(f"{name}: not enough history")
        elif kind == "bb":
            overlays.append(("BB", bollinger(closes, number or 20)))
        elif kind == "rsi":
            panels.append(("RSI", rsi(closes, number or 14)))
        elif kind == "macd":
            panels.append(("MACD", macd(closes)))
        else:
            notes.append(f"{name}: indicator not supported")

    cutoff_days = PERIOD_DAYS.get(spec.get("period", "1y"), 366)
    start = (
        0
        if cutoff_days is None
        else next(
            (
                i
                for i, d in enumerate(dates)
                if d >= dates[-1] - timedelta(days=cutoff_days)
            ),
            0,
        )
    )

    x = dates[start:]
    price = np.asarray(closes[start:], dtype=float)

    heights = [4] + [1.3] * len(panels)

    figure, axes = plt.subplots(
        len(panels) + 1,
        1,
        figsize=(8.2, 4.2 + 1.5 * len(panels)),
        sharex=True,
        gridspec_kw={"height_ratios": heights},
        dpi=130,
    )

    if len(panels) == 0:
        axes = [axes]

    main = axes[0]
    main.plot(x, price, color="#111827", linewidth=1.6, label="Close")

    palette = ["#d97706", "#2563eb", "#9333ea", "#0f766e", "#be185d"]
    color_index = 0

    for label, data in overlays:
        if label == "BB":
            lower, middle, upper = (a[start:] for a in data)
            main.plot(x, upper, color="#9ca3af", linewidth=0.9)
            main.plot(x, lower, color="#9ca3af", linewidth=0.9)
            main.fill_between(x, lower, upper, color="#9ca3af", alpha=0.12,
                              label="Bollinger (20, 2)")
        else:
            main.plot(
                x,
                data[start:],
                color=palette[color_index % len(palette)],
                linewidth=1.2,
                label=label,
            )
            color_index += 1

    low, high = float(np.nanmin(price)), float(np.nanmax(price))

    for level in spec.get("levels", []):
        try:
            level_price = float(level["price"])
        except (KeyError, TypeError, ValueError):
            continue

        color, style = LEVEL_STYLE.get(
            level.get("kind", "level"), LEVEL_STYLE["level"]
        )

        main.axhline(level_price, color=color, linestyle=style,
                     linewidth=1.1, alpha=0.9)

        main.annotate(
            f"{level.get('label', '')} {level_price:,.6g}".strip(),
            xy=(x[0], level_price),
            xytext=(4, 3),
            textcoords="offset points",
            fontsize=7.5,
            color=color,
        )

        low = min(low, level_price)
        high = max(high, level_price)

    if spec.get("log"):
        main.set_yscale("log")

    margin = (high - low) * 0.06 if not spec.get("log") else 0
    if not spec.get("log"):
        main.set_ylim(low - margin, high + margin)

    last_value = float(price[-1])
    main.set_title(
        f"{spec.get('title', spec['symbol'])}  |  last {last_value:,.6g} "
        f"({x[-1]:%d %b %Y})",
        fontsize=10,
        loc="left",
    )
    main.grid(alpha=0.25)
    main.legend(loc="upper left", fontsize=7, ncol=3, frameon=False)

    for axis, (name, data) in zip(axes[1:], panels):
        if name == "RSI":
            axis.plot(x, data[start:], color="#2563eb", linewidth=1.1)
            axis.axhline(70, color="#cf222e", linestyle="--", linewidth=0.8)
            axis.axhline(30, color="#1a7f37", linestyle="--", linewidth=0.8)
            axis.set_ylim(0, 100)
            axis.set_ylabel("RSI", fontsize=8)
        else:
            line, signal, hist = (a[start:] for a in data)
            axis.bar(x, hist, color="#9ca3af", width=_bar_width(x))
            axis.plot(x, line, color="#2563eb", linewidth=1.0)
            axis.plot(x, signal, color="#d97706", linewidth=1.0)
            axis.set_ylabel("MACD", fontsize=8)

        axis.grid(alpha=0.25)

    axes[-1].xaxis.set_major_formatter(mdates.AutoDateFormatter(
        mdates.AutoDateLocator()))

    for axis in axes:
        axis.tick_params(labelsize=7.5)

    figure.tight_layout()

    buffer = io.BytesIO()
    figure.savefig(buffer, format="png", facecolor="white")
    plt.close(figure)

    return buffer.getvalue(), notes


def _bar_width(x):
    if len(x) < 2:
        return 1.0

    return max((x[-1] - x[0]).days / len(x) * 0.8, 0.5)


# --------------------------------------------------------------------------
# Email
# --------------------------------------------------------------------------

def build_message(report_id, meta, analysis_html, charts):
    """charts: list of dicts with id, title, caption, png (bytes)."""
    placed = set()

    def place(match):
        chart_id = match.group(1)
        chart = next((c for c in charts if c["id"] == chart_id), None)

        if not chart:
            return ""

        placed.add(chart_id)

        return chart_html(chart)

    body = re.sub(r"<!--\s*chart:([\w\-]+)\s*-->", place, analysis_html)

    leftovers = "".join(
        chart_html(c) for c in charts if c["id"] not in placed
    )

    skipped = [
        line
        for chart in charts
        for line in chart.get("notes", [])
    ] + [
        f"Chart '{cid}' skipped: {why}"
        for cid, why in meta.get("_failed_charts", [])
    ]

    footer = ""

    if skipped:
        items = "".join(f"<li>{_escape(line)}</li>" for line in skipped)
        footer = (
            "<hr><p style='color:#6b7280;font-size:12px'>"
            "Charts/indicators not drawn:</p>"
            f"<ul style='color:#6b7280;font-size:12px'>{items}</ul>"
        )

    html = (
        "<html><body style=\"font-family:-apple-system,Segoe UI,Roboto,"
        "Arial,sans-serif;line-height:1.5;color:#111827;max-width:720px;"
        "margin:0 auto;padding:12px\">"
        f"{body}{leftovers}{footer}"
        "<p style='color:#9ca3af;font-size:11px'>Analysis by Claude from "
        "the video transcript. Prices from Yahoo Finance at send time.</p>"
        "</body></html>"
    )

    plain = re.sub(r"<[^>]+>", "", re.sub(r"<!--.*?-->", "", analysis_html))

    related = MIMEMultipart("related")
    alternative = MIMEMultipart("alternative")
    alternative.attach(MIMEText(plain, "plain", "utf-8"))
    alternative.attach(MIMEText(html, "html", "utf-8"))
    related.attach(alternative)

    for chart in charts:
        image = MIMEImage(chart["png"], "png")
        image.add_header("Content-ID", f"<{chart['id']}>")
        image.add_header(
            "Content-Disposition", "inline", filename=f"{chart['id']}.png"
        )
        related.attach(image)

    title = meta.get("title", report_id).replace("\r", " ").replace("\n", " ")

    related["Subject"] = f"Ben analysis: {title}"

    return related


def chart_html(chart):
    caption = (
        f"<p style='color:#4b5563;font-size:13px;margin:2px 0 14px'>"
        f"{_escape(chart['caption'])}</p>"
        if chart.get("caption")
        else ""
    )

    return (
        f"<div style='margin:14px 0'><img src='cid:{chart['id']}' "
        f"alt='{_escape(chart.get('title', chart['id']))}' "
        "style='width:100%;max-width:700px;height:auto;border:1px solid "
        f"#e5e7eb;border-radius:6px'>{caption}</div>"
    )


def _escape(text):
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace("'", "&#39;")
    )


def send(message):
    gmail_user = os.environ["GMAIL_USER"].strip()
    app_password = "".join(os.environ["GMAIL_APP_PASSWORD"].split())

    message["From"] = gmail_user
    message["To"] = gmail_user

    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=60) as server:
        server.login(gmail_user, app_password)
        server.send_message(message)


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------

def pending_reports():
    for folder in sorted(glob.glob(os.path.join(REPORT_DIR, "*"))):
        if (
            os.path.isdir(folder)
            and os.path.exists(os.path.join(folder, "analysis.html"))
            and not os.path.exists(os.path.join(folder, "SENT"))
        ):
            yield folder


def read_json(path, default):
    if not os.path.exists(path):
        return default

    with open(path, encoding="utf-8") as file:
        return json.load(file)


def process(folder, loader=load_prices, sender=send):
    report_id = os.path.basename(folder)
    meta = read_json(os.path.join(folder, "meta.json"), {})
    specs = read_json(os.path.join(folder, "charts.json"), [])

    with open(os.path.join(folder, "analysis.html"), encoding="utf-8") as f:
        analysis_html = f.read()

    charts = []
    failed = []

    for spec in specs:
        try:
            png, notes = draw_chart(spec, loader=loader)
            charts.append(
                {
                    "id": spec["id"],
                    "title": spec.get("title", spec["id"]),
                    "caption": spec.get("caption", ""),
                    "png": png,
                    "notes": [
                        f"{spec.get('title', spec['id'])}: {n}"
                        for n in notes
                    ],
                }
            )
        except Exception as error:  # one bad chart must not block the email
            failed.append((spec.get("id", "?"), str(error)[:200]))
            log(f"Chart {spec.get('id')} failed: {error}")

    meta["_failed_charts"] = failed

    message = build_message(report_id, meta, analysis_html, charts)
    sender(message)

    with open(os.path.join(folder, "SENT"), "w", encoding="utf-8") as f:
        f.write(datetime.now(timezone.utc).isoformat() + "\n")

    log(f"Report emailed: {report_id} ({len(charts)} charts, "
        f"{len(failed)} failed)")


def main():
    folders = list(pending_reports())

    if not folders:
        log("No unsent reports.")
        return

    errors = []

    for folder in folders:
        try:
            process(folder)
        except Exception as error:
            errors.append(f"{folder}: {error}")
            log(f"Report failed: {folder}: {error}")

    if errors:
        raise RuntimeError("; ".join(errors))


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        from check import record_alert, should_alert

        if should_alert():
            record_alert()
            log(f"FAILURE (alerting; next alert in 24h): {error}")
            sys.exit(1)

        log(f"FAILURE (alert already sent today, not failing): {error}")
