# Ben transcript analysis guide

Instructions for the scheduled Claude task that analyses new Benjamin Cowen
transcripts. Read this fully every run.

## Goal
Extract **every insight** from each new transcript, put it in context against
what Ben has said in earlier videos, and deliver it as one HTML email with
charts embedded. Completeness beats brevity: no word limit. Skim-friendly
structure still matters (headings, tables, bullets), and the first screen
must hold the thesis and the key calls.

## Finding work
1. `git pull --rebase origin main`.
2. A transcript `transcripts/<STEM>.txt` needs analysis if there is no folder
   `reports/<STEM>/`. Handle oldest first. If none are pending, stop silently
   (no commit, no output).
3. Read `memory/ben.md` first. Then read the transcript completely. Long
   ones: read in consecutive chunks (`sed -n` / Read with offset); never
   sample or skim. The text is auto-captioned: names and numbers can be
   misheard. Flag doubtful figures instead of guessing.

## What to extract (all of it)
Write each section in order; say "Not covered" if empty.

1. **Thesis and stance**: central argument, overall outlook, uncertainty,
   tone shift versus the previous video (use memory).
2. **Every prediction/expectation**: table with asset/market, what he expects,
   target or % move, timeframe, conditions, conviction (only if he states it),
   and whether it is his base case, an alternative scenario or a hypothetical.
3. **Levels**: every price, support, resistance, target, invalidation, moving
   average, band or ratio he names, and why it matters to him.
4. **Indicators and metrics he uses**: RSI, moving averages, risk metric,
   dominance, yield curve, liquidity, on-chain, credit spreads, etc., what he
   says each is currently showing and what would change his view.
5. **Reasoning chains**: the "because" behind each call, in his order, with
   the evidence he cites (data, history, cycles, analogues, other markets).
6. **Macro and cross-market context**: equities, bonds, dollar, commodities,
   Fed, economy; relationships between markets that he draws.
7. **Historical comparisons**: cycles/years/analogues; label clearly when he
   uses one as a comparison and not a forecast.
8. **If-then conditions**: every "if X then Y", "watch for Z", confirm/weaken/
   invalidate statement. Keep conditional language: never turn "could" into
   "will".
9. **Doubts, hedges, caveats**: where he says he is unsure, wrong before, or
   that the data conflicts.
10. **Other content**: audience Q&A, other people's views he quotes (attribute
    to them), sponsors, personal remarks (one line each).
11. **Versus earlier videos** (from memory): what changed or repeated, which
    earlier predictions are now confirmed / failed / still open. Check current
    prices with web search for this. State the source/date of any price.
12. **My interpretation** (clearly labelled as inference, not Ben's words):
    2-4 non-obvious takeaways, assumptions his argument depends on.
13. **Watch list**: dated events and levels to monitor next.

Rules: use only the transcript (plus memory and current prices for sections
11 and charts); attribute to Ben; figures refer to recording time; no
investment advice; do not invent quotes, timestamps, targets or timeframes;
paraphrase (at most a few words verbatim).

## Charts
Create `charts.json` (spec format in `render_report.py` docstring). Chart
**everything chartable that he discusses**, typically 3-8 charts: each asset
with his levels, each indicator he leans on. Rules:
- Only use levels/indicators he actually mentions. Put his numbers in
  `levels` with `kind` support/resistance/target/invalidation.
- Supported indicators: `sma<N>`, `ema<N>`, `bb`, `rsi<N>`, `macd`.
  Use weekly (`"interval":"1wk"`, `"period":"5y"` or `"max"`) for long-term
  averages such as the 200-week MA.
- Symbols are Yahoo Finance tickers: BTC-USD, ETH-USD, SOL-USD, ^GSPC, ^IXIC,
  ^TNX (10y yield), ^IRX, DX-Y.NYB (dollar), GC=F (gold), CL=F, ^VIX, plus
  ratios like `ETH-USD/BTC-USD` or `BTC-USD/^GSPC`.
- Things Yahoo has no series for (dominance, on-chain, risk metric, total
  market cap): do not fake them. Describe them in the text and mention in the
  analysis that no chart was possible.
- Each chart needs a one-line `caption` explaining why it matters.
- Place charts inside `analysis.html` by writing `<!--chart:ID-->` where each
  belongs (near the relevant section). Unplaced charts are appended.

## Files to write (folder `reports/<STEM>/`)
- `analysis.html`: lean HTML for email: `h2`, `h3`, `p`, `ul/li`, `table`
  with inline styles (`border-collapse:collapse`, `border:1px solid #e5e7eb`,
  `padding:6px`). No classes, scripts, external images, or custom fonts.
  Keep it under ~90 KB so Gmail does not clip it. Start with a title, the
  video link and date, then a 4-6 bullet "At a glance".
- `charts.json`: list of chart specs (`[]` if none).
- `meta.json`: `{"title": "<video title>", "link": "<url>", "published": "<iso>"}`.
Do NOT create a `SENT` file: the workflow emails the report and marks it.

## Memory (`memory/ben.md`)
Update it every run, after the report. Keep it compact and organised, never
a transcript dump. Sections: Current stance (dated), Predictions log (one row
each: date/video, asset, call, target, timeframe, conditions, status
open/hit/missed/changed + note), Key levels, Indicators he follows (and their
last stated reading), Recurring themes, Changes of view (dated). When he
changes a view, keep the old entry and add the change. Mark predictions that
have resolved, using current prices.

## Commit
`git add reports memory && git commit` then `git pull --rebase origin main`
and `git push origin HEAD:main`. Commit message ends with the attribution
lines the session instructs. Do not touch other files.
