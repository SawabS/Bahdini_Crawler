#!/usr/bin/env python3
"""Live local monitoring dashboard for generate_qa_openrouter.py.

Serves an interactive, sub-second-refreshing page at http://127.0.0.1:8765
showing real generated QA-pair content as it lands: a streaming feed of
question/answer pairs (Bahdini, RTL) that animates each new arrival,
throughput and distribution charts, and corrected running cost.

Two design points worth knowing:

1. Incremental tailing. A background thread tracks a byte offset per
   generations/*.jsonl file and reads only newly-appended bytes each pass,
   so cost stays flat over a multi-hour run instead of growing with the
   corpus.

2. Delta transport. The browser polls /api/delta?since=<seq> at a few
   hundred ms and receives only records newer than its last sequence
   number, plus small aggregates. Full state is never re-sent, which is
   what makes a sub-second refresh cheap enough to leave open all day.

Cost is recomputed here from each record's raw token counts and its own
recorded model slug via qa_config.estimate_cost_usd, NOT read from the
record's stored est_cost_usd. Records written before the per-model pricing
fix carry a 3.75x-inflated value for that field; recomputing repairs the
history rather than displaying known-wrong totals.

Bahdini text is set in IBM Plex Sans Arabic, self-hosted from assets/ and
served at /fonts/ by this same process (see FONT_FILES) rather than pulled
from a CDN, so the page renders identically with no network.

Local only (binds 127.0.0.1), stdlib only.

    conda run --no-capture-output -n ai python -u qa_generation/dashboard.py
"""

import glob
import json
import sys
import threading
import time
from collections import Counter, deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import qa_config as cfg

PORT = 8765
TOTAL_CHUNKS = 246_515          # qa_generation/output/chunks_report.md, post-fix corpus

# IBM Plex Sans Arabic, self-hosted from assets/ and served at /fonts/*.
# The default ui-sans-serif stack resolves Arabic-script text to whatever
# generic fallback the OS picks (Geeza Pro on macOS), which is a legibility
# problem here specifically: reviewing dialect purity means reading Bahdini
# closely, and the Kurdish-specific letters (ێ ڤ ڕ ڵ ۆ ە) are exactly where
# a mediocre face gets ambiguous. Plex Arabic is a real text face, covers
# every one of those codepoints (verified against the shipped subset, not
# assumed), and is the only weight-matched Arabic companion to a neutral UI
# sans that is freely redistributable. Adobe Arabic and Calibri are both
# proprietary and absent from this machine; relying on either would have
# silently fallen through to the same generic default it replaces.
ASSETS_DIR = Path(__file__).resolve().parent / "assets"
FONT_FILES = {
    "ibm-plex-sans-arabic-400.woff2",
    "ibm-plex-sans-arabic-600.woff2",
}
FEED_MAXLEN = 400               # server-side ring buffer of recent generations
THROUGHPUT_MAXLEN = 180         # rolling samples for the throughput chart
POLL_INTERVAL_S = 1.0           # how often the tailer looks for new bytes

LOCK = threading.Lock()
OFFSETS = {}                    # path -> bytes already consumed

STATE = {
    "seq": 0,
    "ok": 0, "empty": 0, "error": 0,
    "pairs": 0,
    "input_tokens": 0, "output_tokens": 0,
    "cost_usd": 0.0,
    "by_source": Counter(),
    "by_qtype": Counter(),
    "with_reasoning": 0,
    "feed": deque(maxlen=FEED_MAXLEN),
    "throughput": deque(maxlen=THROUGHPUT_MAXLEN),   # (epoch_s, cumulative_processed)
    "started_ts": time.time(),
    "last_record_ts": None,
}


def _ingest(rec: dict, source: str) -> None:
    """Fold one generation record into STATE. Caller holds LOCK."""
    status = rec.get("status", "error")
    bucket = status if status in ("ok", "empty") else "error"
    STATE[bucket] += 1
    STATE["by_source"][source] += 1

    tokens_in = rec.get("input_tokens") or 0
    tokens_out = rec.get("output_tokens") or 0
    STATE["input_tokens"] += tokens_in
    STATE["output_tokens"] += tokens_out
    # Recomputed, not read off the record: see module docstring.
    STATE["cost_usd"] += cfg.estimate_cost_usd(tokens_in, tokens_out, rec.get("model"))

    pairs = rec.get("qa_pairs") or []
    STATE["pairs"] += len(pairs)
    for pair in pairs:
        if pair.get("question_type"):
            STATE["by_qtype"][pair["question_type"]] += 1
        if pair.get("reasoning"):
            STATE["with_reasoning"] += 1

    if rec.get("ts"):
        STATE["last_record_ts"] = rec["ts"]

    if bucket == "ok" and pairs:
        STATE["seq"] += 1
        STATE["feed"].append({
            "seq": STATE["seq"],
            "source": source,
            "chunk_id": rec.get("chunk_id", ""),
            "origin": "ocr" if str(rec.get("chunk_id", "")).startswith("ocr") else "native",
            "ts": rec.get("ts", ""),
            "tokens_out": tokens_out,
            "pairs": [
                {
                    "question": p.get("question", ""),
                    "answer": p.get("answer", ""),
                    "question_type": p.get("question_type", ""),
                    "reasoning": p.get("reasoning"),
                }
                for p in pairs
            ],
        })


def scan_once() -> None:
    for path in glob.glob(str(cfg.GENERATIONS_DIR / "*" / "*.jsonl")):
        try:
            size = Path(path).stat().st_size
        except OSError:
            continue
        seen = OFFSETS.get(path, 0)
        if size <= seen:
            continue
        with open(path, "rb") as handle:
            handle.seek(seen)
            raw = handle.read()
        # A record may still be mid-write; only consume through the last
        # complete line and leave the remainder for the next pass.
        cut = raw.rfind(b"\n")
        if cut == -1:
            continue
        OFFSETS[path] = seen + cut + 1
        source = Path(path).parent.name
        chunk = raw[:cut].decode("utf-8", errors="replace")
        with LOCK:
            for line in chunk.splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    _ingest(json.loads(line), source)
                except json.JSONDecodeError:
                    continue

    with LOCK:
        processed = STATE["ok"] + STATE["empty"] + STATE["error"]
        STATE["throughput"].append((time.time(), processed))


def scanner_loop() -> None:
    while True:
        try:
            scan_once()
        except Exception as exc:                       # keep the server alive
            print(f"[dashboard] scan error: {exc}", file=sys.stderr)
        time.sleep(POLL_INTERVAL_S)


def throughput_series() -> list:
    """Per-sample rate in chunks/min, derived from cumulative samples."""
    pts = list(STATE["throughput"])
    out = []
    for (t0, n0), (t1, n1) in zip(pts, pts[1:]):
        dt = t1 - t0
        if dt > 0:
            out.append({"t": round(t1, 1), "rate": round((n1 - n0) / dt * 60, 1)})
    return out


def build_delta(since: int) -> dict:
    with LOCK:
        processed = STATE["ok"] + STATE["empty"] + STATE["error"]
        series = throughput_series()
        # Smooth the ETA over the recent window rather than the last sample,
        # which is noisy at batch boundaries.
        recent = [p["rate"] for p in series[-30:]]
        rate = sum(recent) / len(recent) if recent else 0.0
        remaining = max(TOTAL_CHUNKS - processed, 0)
        new_items = [item for item in STATE["feed"] if item["seq"] > since]
        return {
            "seq": STATE["seq"],
            "totals": {
                "total_chunks": TOTAL_CHUNKS,
                "processed": processed,
                "ok": STATE["ok"],
                "empty": STATE["empty"],
                "error": STATE["error"],
                "pairs": STATE["pairs"],
                "with_reasoning": STATE["with_reasoning"],
                "cost_usd": round(STATE["cost_usd"], 2),
                "input_tokens": STATE["input_tokens"],
                "output_tokens": STATE["output_tokens"],
                "rate_per_min": round(rate, 1),
                "eta_hours": round(remaining / rate / 60, 2) if rate > 0 else None,
                "projected_total_cost": (
                    round(STATE["cost_usd"] / processed * TOTAL_CHUNKS, 2)
                    if processed else None
                ),
                "projected_pairs": (
                    int(STATE["pairs"] / processed * TOTAL_CHUNKS) if processed else None
                ),
                "elapsed_s": int(time.time() - STATE["started_ts"]),
                "last_record_ts": STATE["last_record_ts"],
            },
            "by_source": dict(STATE["by_source"].most_common()),
            "by_qtype": dict(STATE["by_qtype"].most_common()),
            "throughput": series,
            "new": new_items,
        }


PAGE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>QA Generation Monitor</title>
<style>
  /* Served from qa_generation/assets/ by this same process; no CDN, so the
     dashboard still renders correctly with no network. */
  @font-face {
    font-family: "Plex Arabic";
    src: url("/fonts/ibm-plex-sans-arabic-400.woff2") format("woff2");
    font-weight: 400; font-style: normal; font-display: swap;
  }
  @font-face {
    font-family: "Plex Arabic";
    src: url("/fonts/ibm-plex-sans-arabic-600.woff2") format("woff2");
    font-weight: 600; font-style: normal; font-display: swap;
  }
  :root {
    color-scheme: light;
    --bg: #f5f5f3; --surface: #fcfcfb; --surface-2: #ffffff; --border: #e6e5e0;
    --ink: #0b0b0b; --ink-2: #52514e; --ink-3: #86847a;
    --series: #2a78d6; --series-soft: rgba(42,120,214,.13);
    --good: #0ca30c; --warn: #b07800; --crit: #d03b3b;
    --new-flash: rgba(42,120,214,.16);
    --radius: 12px;
    /* The shipped subset is Arabic-script only, so Latin characters inside
       an RTL run (a stray English word, digits) fall through to the UI sans
       rather than rendering as tofu. */
    --kurdish: "Plex Arabic", "SF Arabic", "Geeza Pro", ui-sans-serif, sans-serif;
  }
  @media (prefers-color-scheme: dark) {
    :root:not([data-theme="light"]) {
      color-scheme: dark;
      --bg: #121211; --surface: #1a1a19; --surface-2: #222221; --border: #33322e;
      --ink: #ffffff; --ink-2: #c3c2b7; --ink-3: #8a887d;
      --series: #3987e5; --series-soft: rgba(57,135,229,.18);
      --good: #1fc21f; --warn: #eda100; --crit: #e66767;
      --new-flash: rgba(57,135,229,.22);
    }
  }
  :root[data-theme="dark"] {
    color-scheme: dark;
    --bg: #121211; --surface: #1a1a19; --surface-2: #222221; --border: #33322e;
    --ink: #ffffff; --ink-2: #c3c2b7; --ink-3: #8a887d;
    --series: #3987e5; --series-soft: rgba(57,135,229,.18);
    --good: #1fc21f; --warn: #eda100; --crit: #e66767;
    --new-flash: rgba(57,135,229,.22);
  }
  * { box-sizing: border-box; }
  body {
    margin: 0; background: var(--bg); color: var(--ink);
    font: 14px/1.55 ui-sans-serif, -apple-system, "Segoe UI", Roboto, sans-serif;
    -webkit-font-smoothing: antialiased;
  }
  .wrap { max-width: 1240px; margin: 0 auto; padding: 22px 20px 60px; }

  header { display: flex; align-items: center; gap: 12px; margin-bottom: 4px; flex-wrap: wrap; }
  h1 { font-size: 17px; font-weight: 650; margin: 0; letter-spacing: -.01em; }
  .live { display: inline-flex; align-items: center; gap: 7px; font-size: 12px; color: var(--ink-2); }
  .dot { width: 8px; height: 8px; border-radius: 50%; background: var(--good); }
  .dot.on { animation: pulse 1.6s ease-in-out infinite; }
  .dot.off { background: var(--ink-3); animation: none; }
  @keyframes pulse { 0%,100% { opacity: 1 } 50% { opacity: .3 } }
  .sub { color: var(--ink-3); font-size: 12.5px; margin-bottom: 20px; font-variant-numeric: tabular-nums; }
  .spacer { flex: 1 1 auto; }

  button, select, input[type=search] {
    font: inherit; font-size: 12.5px; color: var(--ink);
    background: var(--surface); border: 1px solid var(--border);
    border-radius: 8px; padding: 6px 11px; cursor: pointer;
  }
  button:hover, select:hover { border-color: var(--ink-3); }
  button.active { background: var(--series); border-color: var(--series); color: #fff; }
  input[type=search] { cursor: text; min-width: 190px; }

  .tiles { display: grid; grid-template-columns: repeat(auto-fit, minmax(158px,1fr)); gap: 10px; margin-bottom: 14px; }
  .tile { background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius); padding: 13px 15px; }
  .tile .k { font-size: 10.5px; text-transform: uppercase; letter-spacing: .05em; color: var(--ink-3); margin-bottom: 5px; }
  .tile .v { font-size: 23px; font-weight: 640; font-variant-numeric: tabular-nums; letter-spacing: -.02em; line-height: 1.15; }
  .tile .s { font-size: 11.5px; color: var(--ink-2); margin-top: 3px; font-variant-numeric: tabular-nums; }

  .panel { background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius); padding: 15px 17px; margin-bottom: 14px; }
  .panel h2 { font-size: 11px; text-transform: uppercase; letter-spacing: .05em; color: var(--ink-3); margin: 0 0 12px; font-weight: 600; }
  .prog-head { display: flex; justify-content: space-between; align-items: baseline; font-variant-numeric: tabular-nums; margin-bottom: 9px; }
  .prog-head .big { font-size: 15px; font-weight: 600; }
  .prog-head .r { font-size: 12.5px; color: var(--ink-2); }
  .track { height: 9px; background: var(--surface-2); border: 1px solid var(--border); border-radius: 5px; overflow: hidden; }
  .fill { height: 100%; background: var(--series); border-radius: 4px; transition: width .45s cubic-bezier(.4,0,.2,1); }

  .grid2 { display: grid; grid-template-columns: 1.35fr 1fr; gap: 14px; }
  @media (max-width: 900px) { .grid2 { grid-template-columns: 1fr; } }

  svg { display: block; width: 100%; overflow: visible; }
  .gridline { stroke: var(--border); stroke-width: 1; }
  .axis-label { fill: var(--ink-3); font-size: 10.5px; }
  .spark-line { fill: none; stroke: var(--series); stroke-width: 2; stroke-linejoin: round; stroke-linecap: round; }
  .spark-area { fill: var(--series-soft); }
  .crosshair { stroke: var(--ink-3); stroke-width: 1; stroke-dasharray: 3 3; }
  .hit { fill: transparent; }

  .bars { display: flex; flex-direction: column; gap: 7px; }
  .bar-row { display: grid; grid-template-columns: minmax(105px, 34%) 1fr auto; gap: 10px; align-items: center; font-size: 12.5px; }
  .bar-name { color: var(--ink-2); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .bar-track { background: var(--surface-2); border-radius: 4px; height: 15px; overflow: hidden; }
  .bar-fill { height: 100%; background: var(--series); border-radius: 0 4px 4px 0; transition: width .4s ease; }
  .bar-val { font-variant-numeric: tabular-nums; color: var(--ink); min-width: 52px; text-align: right; }

  .status-row { display: flex; gap: 16px; flex-wrap: wrap; font-size: 12.5px; }
  .status-item { display: flex; align-items: center; gap: 6px; color: var(--ink-2); }
  .status-item b { color: var(--ink); font-variant-numeric: tabular-nums; }
  .swatch { width: 9px; height: 9px; border-radius: 2px; flex: none; }

  .controls { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; margin-bottom: 12px; }
  .feed-head { display: flex; align-items: center; gap: 10px; margin: 22px 0 12px; flex-wrap: wrap; }
  .feed-head h2 { margin: 0; }
  .pill { background: var(--series); color: #fff; border-radius: 999px; padding: 2px 9px; font-size: 11px; font-weight: 600; font-variant-numeric: tabular-nums; }

  .feed { display: flex; flex-direction: column; gap: 10px; }
  .card { background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius); padding: 13px 16px; }
  .card.fresh { animation: land .8s cubic-bezier(.2,.8,.2,1); }
  @keyframes land {
    0%   { opacity: 0; transform: translateY(-9px); background: var(--new-flash); border-color: var(--series); }
    60%  { opacity: 1; transform: translateY(0);    background: var(--new-flash); border-color: var(--series); }
    100% { opacity: 1; transform: translateY(0);    background: var(--surface);   border-color: var(--border); }
  }
  .card-meta { display: flex; align-items: center; gap: 8px; margin-bottom: 9px; font-size: 11px; color: var(--ink-3); flex-wrap: wrap; }
  .tag { background: var(--surface-2); border: 1px solid var(--border); border-radius: 5px; padding: 2px 7px; color: var(--ink-2); }
  .tag.new { background: var(--series); border-color: var(--series); color: #fff; font-weight: 600; }
  .mono { font-family: ui-monospace, "SF Mono", Menlo, monospace; font-size: 10.5px; }

  .qa { padding: 9px 0; border-top: 1px solid var(--border); }
  .qa:first-of-type { border-top: none; padding-top: 0; }
  .qtype { display: inline-block; font-size: 9.5px; text-transform: uppercase; letter-spacing: .04em;
           color: var(--ink-3); border: 1px solid var(--border); border-radius: 4px; padding: 1px 5px; margin-bottom: 5px; }
  /* Arabic script has a smaller x-height than Latin at the same nominal
     size and stacks diacritics above and below the baseline, so it is set
     larger and looser here than the surrounding UI text -- otherwise the
     marks that distinguish ڕ from ر, or ێ from ی, collide. */
  .rtl { direction: rtl; text-align: right; unicode-bidi: isolate;
         font-family: var(--kurdish); }
  .q { font-size: 17px; font-weight: 600; line-height: 1.95; color: var(--ink); }
  .a { font-size: 16px; font-weight: 400; line-height: 2.0; color: var(--ink-2); margin-top: 6px; }
  .reason { font-size: 14.5px; line-height: 1.95; color: var(--ink-3); margin-top: 7px;
            border-inline-start: 2px solid var(--border); padding-inline-start: 10px; }

  table { width: 100%; border-collapse: collapse; font-size: 12.5px; font-variant-numeric: tabular-nums; }
  th, td { text-align: left; padding: 6px 10px; border-bottom: 1px solid var(--border); }
  th { color: var(--ink-3); font-weight: 600; font-size: 11px; text-transform: uppercase; letter-spacing: .04em; }
  td.rtl { font-size: 15px; line-height: 1.9; }        /* same reason as .q/.a above */
  .hidden { display: none; }
  .muted { color: var(--ink-3); font-size: 12.5px; padding: 26px; text-align: center; }

  .tip { position: fixed; pointer-events: none; z-index: 50; background: var(--surface-2);
         border: 1px solid var(--border); border-radius: 8px; padding: 7px 10px; font-size: 12px;
         box-shadow: 0 6px 22px rgba(0,0,0,.16); opacity: 0; transition: opacity .1s; font-variant-numeric: tabular-nums; }
  .tip.show { opacity: 1; }
</style>
</head>
<body>
<div class="wrap">

  <header>
    <h1>QA Generation Monitor</h1>
    <span class="live"><span class="dot on" id="dot"></span><span id="livetext">streaming</span></span>
    <span class="spacer"></span>
    <button id="btn-stream" class="active" title="Pause the live feed. Counters keep updating.">Pause feed</button>
    <button id="btn-theme" title="Toggle light and dark">Theme</button>
  </header>
  <div class="sub" id="sub">connecting to the generation job</div>

  <div class="tiles" id="tiles"></div>

  <div class="panel">
    <div class="prog-head">
      <span class="big" id="prog-text">0 / 0 chunks</span>
      <span class="r" id="prog-eta"></span>
    </div>
    <div class="track"><div class="fill" id="prog-fill" style="width:0%"></div></div>
    <div class="status-row" style="margin-top:12px;" id="status-row"></div>
  </div>

  <div class="grid2">
    <div class="panel">
      <h2>Throughput, chunks per minute</h2>
      <div id="chart-throughput"></div>
    </div>
    <div class="panel">
      <h2>Question types generated</h2>
      <div class="bars" id="chart-qtype"></div>
    </div>
  </div>

  <div class="panel">
    <h2>Chunks processed by source</h2>
    <div class="bars" id="chart-source"></div>
  </div>

  <div class="feed-head">
    <h2>Live generations</h2>
    <span class="pill" id="feed-count">0</span>
    <span class="spacer"></span>
    <div class="controls" style="margin:0">
      <select id="f-source"><option value="">All sources</option></select>
      <select id="f-qtype"><option value="">All question types</option></select>
      <input type="search" id="f-text" placeholder="Search questions and answers">
      <button id="btn-table">Table view</button>
    </div>
  </div>

  <div class="feed" id="feed"><div class="muted">Waiting for the next batch to land</div></div>

  <div class="panel hidden" id="table-panel">
    <h2>Recent generations, table view</h2>
    <div style="overflow-x:auto">
      <table id="table"><thead><tr>
        <th>Source</th><th>Type</th><th>Question</th><th>Answer</th>
      </tr></thead><tbody></tbody></table>
    </div>
  </div>

</div>
<div class="tip" id="tip"></div>

<script>
"use strict";

const POLL_MS = 350;          // sub-second refresh, delta payloads only
const CLIENT_FEED_MAX = 250;

let seq = 0;
let items = [];               // newest first
let streaming = true;
let pendingWhilePaused = 0;
let totals = null, bySource = {}, byQtype = {}, throughput = [];
let firstPaint = true;

const $ = (id) => document.getElementById(id);
const fmt = (n) => (n == null ? "0" : n.toLocaleString());
const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g,
  (c) => ({ "&":"&amp;", "<":"&lt;", ">":"&gt;", '"':"&quot;", "'":"&#39;" }[c]));

function ago(iso) {
  if (!iso) return "";
  const s = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
  if (s < 60) return Math.floor(s) + "s ago";
  if (s < 3600) return Math.floor(s / 60) + "m ago";
  return Math.floor(s / 3600) + "h ago";
}

/* ---------- data ---------- */

async function poll() {
  let data;
  try {
    const res = await fetch("/api/delta?since=" + seq, { cache: "no-store" });
    if (!res.ok) throw new Error("http " + res.status);
    data = await res.json();
  } catch (e) {
    $("dot").className = "dot off";
    $("livetext").textContent = "reconnecting";
    return;
  }
  $("dot").className = "dot on";
  $("livetext").textContent = streaming ? "streaming" : "feed paused";

  seq = data.seq;
  totals = data.totals;
  bySource = data.by_source;
  byQtype = data.by_qtype;
  throughput = data.throughput;

  if (data.new && data.new.length) {
    const arrivals = data.new.slice().reverse();      // newest first
    if (streaming) {
      for (const it of arrivals) it._fresh = !firstPaint;
      items = arrivals.concat(items).slice(0, CLIENT_FEED_MAX);
    } else {
      pendingWhilePaused += data.new.length;
    }
  }

  renderStats();
  renderCharts();
  if (streaming) renderFeed();
  $("feed-count").textContent = streaming
    ? fmt(items.length) + " shown"
    : fmt(pendingWhilePaused) + " new while paused";
  firstPaint = false;
}

/* ---------- stats ---------- */

function renderStats() {
  const t = totals;
  if (!t) return;

  const emptyPct = t.processed ? (t.empty / t.processed * 100).toFixed(1) : "0.0";
  const pairsPerChunk = t.processed ? (t.pairs / t.processed).toFixed(2) : "0";

  $("tiles").innerHTML = [
    tile("Real cost so far", "$" + t.cost_usd.toFixed(2),
         t.projected_total_cost != null ? "projects to $" + fmt(Math.round(t.projected_total_cost)) + " full run" : ""),
    tile("QA pairs built", fmt(t.pairs),
         t.projected_pairs != null ? "projects to " + fmt(t.projected_pairs) + " total" : ""),
    tile("Pairs per chunk", pairsPerChunk, "target is 4.00"),
    tile("Throughput", fmt(t.rate_per_min) + "/min",
         t.eta_hours != null ? "about " + t.eta_hours + "h remaining" : "measuring"),
    tile("Tokens in / out", fmt(t.input_tokens) + " / " + fmt(t.output_tokens), ""),
    tile("Empty responses", emptyPct + "%", fmt(t.empty) + " of " + fmt(t.processed)),
  ].join("");

  const pct = t.processed / t.total_chunks * 100;
  $("prog-fill").style.width = pct.toFixed(3) + "%";
  $("prog-text").textContent = fmt(t.processed) + " / " + fmt(t.total_chunks) + " chunks  (" + pct.toFixed(2) + "%)";
  $("prog-eta").textContent = t.eta_hours != null
    ? "about " + t.eta_hours + " hours remaining at current rate"
    : "estimating rate";

  // Status colors always carry a text label, never color alone.
  $("status-row").innerHTML = [
    statusItem("--good", "Succeeded", t.ok),
    statusItem("--warn", "Empty, no pairs returned", t.empty),
    statusItem("--crit", "Errors and parse failures", t.error),
    statusItem("--series", "With reasoning field", t.with_reasoning),
  ].join("");

  $("sub").textContent =
    "elapsed " + Math.floor(t.elapsed_s / 60) + "m, last record " + (ago(t.last_record_ts) || "not yet");
}

function tile(k, v, s) {
  return '<div class="tile"><div class="k">' + esc(k) + '</div><div class="v">' + esc(v) + "</div>" +
         (s ? '<div class="s">' + esc(s) + "</div>" : "") + "</div>";
}
function statusItem(varName, label, n) {
  return '<span class="status-item"><span class="swatch" style="background:var(' + varName + ')"></span>' +
         esc(label) + " <b>" + fmt(n) + "</b></span>";
}

/* ---------- charts ---------- */

function renderCharts() {
  drawThroughput(throughput);
  drawBars("chart-qtype", byQtype, "pairs");
  drawBars("chart-source", bySource, "chunks");
}

function drawBars(elId, obj, unit) {
  const entries = Object.entries(obj || {});
  if (!entries.length) { $(elId).innerHTML = '<div class="muted">No data yet</div>'; return; }
  const max = Math.max(...entries.map((e) => e[1])) || 1;
  const total = entries.reduce((a, e) => a + e[1], 0);
  $(elId).innerHTML = entries.map(([name, v]) => {
    const pctOfTotal = total ? (v / total * 100).toFixed(1) : "0";
    return '<div class="bar-row" data-tip="' + esc(name + ": " + fmt(v) + " " + unit + ", " + pctOfTotal + "% of total") + '">' +
      '<span class="bar-name">' + esc(name) + "</span>" +
      '<span class="bar-track"><span class="bar-fill" style="width:' + (v / max * 100).toFixed(2) + '%"></span></span>' +
      '<span class="bar-val">' + fmt(v) + "</span></div>";
  }).join("");
  bindTips(elId);
}

function drawThroughput(series) {
  const el = $("chart-throughput");
  if (!series || series.length < 2) { el.innerHTML = '<div class="muted">Collecting samples</div>'; return; }

  const W = el.clientWidth || 640, H = 168;
  const m = { t: 10, r: 8, b: 20, l: 40 };
  const iw = W - m.l - m.r, ih = H - m.t - m.b;
  const maxRate = Math.max(...series.map((p) => p.rate), 1);
  const yMax = maxRate * 1.15;

  const x = (i) => m.l + (i / (series.length - 1)) * iw;
  const y = (v) => m.t + ih - (v / yMax) * ih;

  const line = series.map((p, i) => (i ? "L" : "M") + x(i).toFixed(1) + " " + y(p.rate).toFixed(1)).join(" ");
  const area = line + " L" + x(series.length - 1).toFixed(1) + " " + (m.t + ih) + " L" + m.l + " " + (m.t + ih) + " Z";

  const ticks = [0, yMax / 2, yMax].map((v) =>
    '<line class="gridline" x1="' + m.l + '" y1="' + y(v).toFixed(1) + '" x2="' + (m.l + iw) + '" y2="' + y(v).toFixed(1) + '"/>' +
    '<text class="axis-label" x="' + (m.l - 7) + '" y="' + (y(v) + 3.5).toFixed(1) + '" text-anchor="end">' + Math.round(v) + "</text>"
  ).join("");

  el.innerHTML =
    '<svg viewBox="0 0 ' + W + " " + H + '" height="' + H + '" role="img" aria-label="Throughput over time in chunks per minute">' +
      ticks +
      '<path class="spark-area" d="' + area + '"/>' +
      '<path class="spark-line" d="' + line + '"/>' +
      '<line class="crosshair" id="xh" x1="0" y1="' + m.t + '" x2="0" y2="' + (m.t + ih) + '" style="display:none"/>' +
      '<circle id="xdot" r="4" fill="var(--series)" stroke="var(--surface)" stroke-width="2" style="display:none"/>' +
      '<rect class="hit" id="hit" x="' + m.l + '" y="' + m.t + '" width="' + iw + '" height="' + ih + '"/>' +
    "</svg>";

  const svg = el.querySelector("svg"), hit = el.querySelector("#hit");
  const xh = el.querySelector("#xh"), xdot = el.querySelector("#xdot"), tip = $("tip");
  hit.addEventListener("mousemove", (ev) => {
    const r = svg.getBoundingClientRect();
    const sx = (ev.clientX - r.left) * (W / r.width);
    let i = Math.round(((sx - m.l) / iw) * (series.length - 1));
    i = Math.max(0, Math.min(series.length - 1, i));
    const px = x(i), py = y(series[i].rate);
    xh.setAttribute("x1", px); xh.setAttribute("x2", px); xh.style.display = "";
    xdot.setAttribute("cx", px); xdot.setAttribute("cy", py); xdot.style.display = "";
    const when = new Date(series[i].t * 1000).toLocaleTimeString();
    tip.textContent = series[i].rate + " chunks/min at " + when;
    tip.classList.add("show");
    tip.style.left = Math.min(ev.clientX + 14, window.innerWidth - 210) + "px";
    tip.style.top = (ev.clientY - 34) + "px";
  });
  hit.addEventListener("mouseleave", () => {
    xh.style.display = "none"; xdot.style.display = "none"; $("tip").classList.remove("show");
  });
}

function bindTips(elId) {
  const tip = $("tip");
  $(elId).querySelectorAll("[data-tip]").forEach((row) => {
    row.addEventListener("mousemove", (ev) => {
      tip.textContent = row.getAttribute("data-tip");
      tip.classList.add("show");
      tip.style.left = Math.min(ev.clientX + 14, window.innerWidth - 250) + "px";
      tip.style.top = (ev.clientY - 34) + "px";
    });
    row.addEventListener("mouseleave", () => tip.classList.remove("show"));
  });
}

/* ---------- feed ---------- */

function currentFilters() {
  return {
    source: $("f-source").value,
    qtype: $("f-qtype").value,
    text: $("f-text").value.trim().toLowerCase(),
  };
}

function visibleItems() {
  const f = currentFilters();
  return items.filter((it) => {
    if (f.source && it.source !== f.source) return false;
    let pairs = it.pairs;
    if (f.qtype) pairs = pairs.filter((p) => p.question_type === f.qtype);
    if (f.text) pairs = pairs.filter((p) =>
      (p.question || "").toLowerCase().includes(f.text) ||
      (p.answer || "").toLowerCase().includes(f.text));
    if (!pairs.length) return false;
    it._visiblePairs = pairs;
    return true;
  });
}

function renderFeed() {
  const vis = visibleItems();
  syncFilterOptions();

  if (!vis.length) {
    $("feed").innerHTML = '<div class="muted">No generations match the current filters</div>';
    $("table").querySelector("tbody").innerHTML = "";
    return;
  }

  $("feed").innerHTML = vis.slice(0, 80).map((it) => {
    const pairs = it._visiblePairs || it.pairs;
    return '<div class="card' + (it._fresh ? " fresh" : "") + '">' +
      '<div class="card-meta">' +
        (it._fresh ? '<span class="tag new">new</span>' : "") +
        '<span class="tag">' + esc(it.source) + "</span>" +
        '<span class="tag">' + esc(it.origin) + "</span>" +
        '<span class="mono">' + esc(it.chunk_id) + "</span>" +
        '<span style="margin-inline-start:auto">' + esc(ago(it.ts)) + "</span>" +
      "</div>" +
      pairs.map((p) =>
        '<div class="qa">' +
          '<div class="qtype">' + esc(p.question_type || "unspecified") + "</div>" +
          '<div class="q rtl">' + esc(p.question) + "</div>" +
          '<div class="a rtl">' + esc(p.answer) + "</div>" +
          (p.reasoning ? '<div class="reason rtl">' + esc(p.reasoning) + "</div>" : "") +
        "</div>").join("") +
    "</div>";
  }).join("");

  // one-shot: the arrival animation should not replay on later repaints
  for (const it of items) it._fresh = false;

  $("table").querySelector("tbody").innerHTML = vis.slice(0, 60).flatMap((it) =>
    (it._visiblePairs || it.pairs).map((p) =>
      "<tr><td>" + esc(it.source) + "</td><td>" + esc(p.question_type || "") +
      '</td><td class="rtl">' + esc(p.question) + '</td><td class="rtl">' + esc(p.answer) + "</td></tr>")
  ).join("");
}

function syncFilterOptions() {
  syncSelect("f-source", Object.keys(bySource));
  syncSelect("f-qtype", Object.keys(byQtype));
}
function syncSelect(id, values) {
  const sel = $(id);
  const have = new Set(Array.from(sel.options).map((o) => o.value));
  for (const v of values) {
    if (!have.has(v)) {
      const o = document.createElement("option");
      o.value = v; o.textContent = v;
      sel.appendChild(o);
    }
  }
}

/* ---------- controls ---------- */

$("btn-stream").addEventListener("click", () => {
  streaming = !streaming;
  const b = $("btn-stream");
  b.textContent = streaming ? "Pause feed" : "Resume feed";
  b.classList.toggle("active", streaming);
  if (streaming) { pendingWhilePaused = 0; renderFeed(); }
});

$("btn-theme").addEventListener("click", () => {
  const cur = document.documentElement.getAttribute("data-theme");
  const next = cur === "dark" ? "light" : cur === "light" ? "dark"
    : (window.matchMedia("(prefers-color-scheme: dark)").matches ? "light" : "dark");
  document.documentElement.setAttribute("data-theme", next);
  renderCharts();
});

$("btn-table").addEventListener("click", () => {
  const p = $("table-panel");
  p.classList.toggle("hidden");
  $("btn-table").classList.toggle("active", !p.classList.contains("hidden"));
});

for (const id of ["f-source", "f-qtype", "f-text"]) {
  $(id).addEventListener("input", renderFeed);
}
window.addEventListener("resize", () => drawThroughput(throughput));

poll();
setInterval(poll, POLL_MS);
</script>
</body>
</html>
"""


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass                                   # no per-request access spam

    def _send(self, body: bytes, content_type: str, cache: str = "no-store") -> None:
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", cache)
        self.end_headers()
        self.wfile.write(body)

    def _send_font(self, name: str) -> bool:
        # Allowlisted by exact name rather than path-joined from the request,
        # so a crafted /fonts/../../.env cannot walk out of assets/.
        if name not in FONT_FILES:
            return False
        try:
            body = (ASSETS_DIR / name).read_bytes()
        except OSError:
            return False
        # Immutable: the file only changes if someone swaps the asset, and a
        # re-fetch on every 350ms-polling page load is pure waste.
        self._send(body, "font/woff2", cache="public, max-age=31536000, immutable")
        return True

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            self._send(PAGE.encode("utf-8"), "text/html; charset=utf-8")
        elif self.path.startswith("/fonts/"):
            if not self._send_font(self.path[len("/fonts/"):].split("?")[0]):
                self.send_response(404)
                self.send_header("Content-Length", "0")
                self.end_headers()
        elif self.path.startswith("/api/delta"):
            since = 0
            if "since=" in self.path:
                try:
                    since = int(self.path.split("since=", 1)[1].split("&")[0])
                except ValueError:
                    since = 0
            body = json.dumps(build_delta(since), ensure_ascii=False).encode("utf-8")
            self._send(body, "application/json; charset=utf-8")
        else:
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()


def main() -> int:
    print("Indexing existing generation records ...")
    scan_once()
    with LOCK:
        print(f"  {STATE['ok'] + STATE['empty'] + STATE['error']:,} records, "
              f"{STATE['pairs']:,} QA pairs, ${STATE['cost_usd']:.2f} real cost")
    threading.Thread(target=scanner_loop, daemon=True).start()
    print(f"Dashboard live at http://127.0.0.1:{PORT}")
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
