#!/usr/bin/env python3
"""Render the figures used by the XeLaTeX build report.

Figures are saved with a white face colour, light grids and no top/right
spines, matching the editorial report style: a tinted page would otherwise
leave visible rectangles around every PNG.

Single-hue by design. Every chart here is one series, so colour carries no
meaning and the sort order does the work; the palette's deep end is used for
marks because pale tints do not clear 3:1 against a white page. The
context-mode chart is the one two-series exception; see the palette note
below for why it uses two hues rather than two tints.

Run inside the conda "ai" env, from the repo root:
    python3 qa_generation/export/make_report_figures.py
"""

import json
import sys
from pathlib import Path

import matplotlib as mpl
import matplotlib.pyplot as plt
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import qa_config as cfg

# Muted, low-chroma report palette: a calm professional register rather than
# a saturated corporate blue.
#
# One honest trade-off is baked in here. Pastel means low chroma, and low
# chroma is exactly what a categorical-palette validator rejects ("reads
# gray"). That is acceptable in this document because every chart but one is
# single series, so hue carries no meaning at all and the sort order does the
# work. The one two-series chart (context mode) uses ACCENT against CLAY,
# which separates cleanly for normal vision (dE 16.6) but only reaches dE 6.0
# under protanopia -- inside the band that is permitted *with* secondary
# encoding. Both of its bars are directly labelled, which is that encoding.
# Do not add a third series to any chart in this palette without re-checking.
ACCENT = "#35706B"      # deep muted teal, 5.70:1 on white
CLAY = "#A9705B"        # secondary, used only in the two-series chart, 4.07:1
WASH = "#CFE0DC"        # pale teal, area fills only -- never a mark
INK = "#262A28"         # 14.54:1
INKSOFT = "#5B635E"     # 6.19:1, still passes body-text contrast
CREAMLINE = "#DCE4E0"   # rules and grid

OUT = cfg.OUTPUT_DIR / "figures"

mpl.rcParams.update({
    "figure.facecolor": "white", "axes.facecolor": "white",
    "savefig.facecolor": "white", "savefig.bbox": "tight", "savefig.dpi": 200,
    "font.family": "sans-serif",
    "font.sans-serif": ["Helvetica Neue", "Helvetica", "Arial", "DejaVu Sans"],
    "font.size": 9, "text.color": INK,
    "axes.labelcolor": INKSOFT, "axes.edgecolor": "#C6D2CD",
    "xtick.color": INKSOFT, "ytick.color": INKSOFT,
    "axes.spines.top": False, "axes.spines.right": False,
    "axes.grid": True, "grid.color": CREAMLINE, "grid.alpha": 0.25,
    "grid.linewidth": 0.8,
})


def barh(ax, labels, values, fmt="{:,.0f}", color=ACCENT, pad=0.014):
    y = np.arange(len(labels))[::-1]
    ax.barh(y, values, height=0.6, color=color, zorder=3)
    ax.set_yticks(y, labels)
    ax.set_xticks([])
    ax.grid(False)
    ax.spines["bottom"].set_visible(False)
    ax.spines["left"].set_color("#C6D2CD")
    span = max(values)
    for yi, v in zip(y, values):
        ax.text(v + span * pad, yi, fmt.format(v), va="center", fontsize=8.4, color=INK)
    ax.set_xlim(0, span * 1.19)


def save(fig, name):
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / name
    fig.savefig(path, facecolor="white")
    plt.close(fig)
    print(f"  {path.name}")


def main() -> int:
    stats = json.loads((cfg.DATASET_DIR / "stats.json").read_text())
    print("Writing figures ...")

    # 1. Chunk queue by source, split by origin -------------------------------
    chunks_by_source = {
        "telegram_badini_book": 90283, "telegram_jihana_pertuken_pdf": 79546,
        "pertokenbadini": 21729, "telegram_pertok_badini": 21017,
        "facebook": 16491, "zcks": 8755, "spirez": 4770,
        "sh2_unicodefixed_bahdini": 3924,
    }
    items = sorted(chunks_by_source.items(), key=lambda kv: kv[1])
    fig, ax = plt.subplots(figsize=(7.2, 3.0))
    barh(ax, [k for k, _ in items], [v for _, v in items])
    ax.set_title("Chunks in the work queue, by source corpus",
                 loc="left", fontsize=10.5, color=INK, pad=10)
    save(fig, "fig_chunks_by_source.png")

    # 2. Token budget of the finished dataset ---------------------------------
    t = stats["tokens"]
    rows = [("system", t["system"]),
            ("chat template", t["total_with_template"] - t["total_content"]),
            ("reasoning", t["reasoning"]),
            ("assistant", t["assistant"]),
            ("user", t["user"])]
    fig, ax = plt.subplots(figsize=(7.2, 2.3))
    barh(ax, [k for k, _ in rows], [v / 1e6 for _, v in rows], fmt="{:,.1f}M")
    ax.set_title("Where the 644.3M training tokens sit",
                 loc="left", fontsize=10.5, color=INK, pad=10)
    save(fig, "fig_token_budget.png")

    # 3. Record-length distribution -------------------------------------------
    h = stats["histograms"]["record_tokens"]
    edges, counts = np.array(h["edges"]), np.array(h["counts"])
    centres = (edges[:-1] + edges[1:]) / 2
    d = stats["distributions"]["record_tokens"]
    fig, ax = plt.subplots(figsize=(7.2, 2.7))
    ax.fill_between(centres, counts, step="mid", color=WASH, zorder=2)
    ax.step(centres, counts, where="mid", color=ACCENT, linewidth=1.8, zorder=3)
    for label, x in (("median 793", d["median"]), ("p95 1,117", d["p95"])):
        ax.axvline(x, color=INKSOFT, linestyle="--", linewidth=0.9, zorder=4)
        ax.text(x + 12, ax.get_ylim()[1] * 0.9, label, fontsize=8, color=INKSOFT)
    ax.set_xlabel("tokens per record")
    ax.set_ylabel("records")
    ax.set_title("Record length is bimodal, not centred on its mean",
                 loc="left", fontsize=10.5, color=INK, pad=10)
    ax.grid(axis="x", visible=False)
    save(fig, "fig_record_tokens.png")

    # 4. Context mode ----------------------------------------------------------
    mode = stats["by_context_mode"]
    fig, axes = plt.subplots(1, 2, figsize=(7.2, 2.2))
    labels = ["with_context", "no_context"]
    pairs = [mode[k]["pairs"] for k in labels]
    toks = [mode[k]["tokens"] / mode[k]["pairs"] for k in labels]
    for ax, vals, title, fmt in (
            (axes[0], pairs, "Pairs", "{:,.0f}"),
            (axes[1], toks, "Mean tokens per pair", "{:,.0f}")):
        # Two hues, not two tints: no tint of ACCENT pale enough to read as
        # a second series still clears 3:1 on white. Both bars are directly
        # labelled, which is the secondary encoding the CVD margin requires.
        ax.barh([1, 0], vals, height=0.55, color=[ACCENT, CLAY], zorder=3)
        ax.set_yticks([1, 0], labels)
        ax.set_xticks([]); ax.grid(False)
        ax.spines["bottom"].set_visible(False)
        for yi, v in zip([1, 0], vals):
            ax.text(v + max(vals) * 0.02, yi, fmt.format(v), va="center",
                    fontsize=8.4, color=INK)
        ax.set_xlim(0, max(vals) * 1.25)
        ax.set_title(title, loc="left", fontsize=9.5, color=INK, pad=8)
    fig.suptitle("The 70/30 context split, and what it costs in tokens",
                 x=0.005, ha="left", fontsize=10.5, color=INK, y=1.06)
    # Two side-by-side subplots each carry outside value labels on the right
    # and tick labels on the left, so the default gap lets the left chart's
    # "667,214" land on top of the right chart's "with_context". Widen it.
    fig.tight_layout()
    fig.subplots_adjust(wspace=0.55)
    save(fig, "fig_context_mode.png")

    # 5. Question types --------------------------------------------------------
    qt = [(k, v["pairs"]) for k, v in stats["by_question_type"].items()
          if v["pairs"] > 100]
    qt.sort(key=lambda kv: kv[1])
    fig, ax = plt.subplots(figsize=(7.2, 2.3))
    barh(ax, [k for k, _ in qt], [v for _, v in qt])
    ax.set_title("QA pairs by question type", loc="left",
                 fontsize=10.5, color=INK, pad=10)
    save(fig, "fig_question_types.png")

    # 6. Quality flags ---------------------------------------------------------
    flags = [("offlist_question_type", 4), ("long_prompt", 27),
             ("offpipeline_model", 391), ("latin_in_answer", 13593),
             ("duplicate_question", 25441), ("sorani_context (heuristic)", 28272),
             ("sorani_answer (heuristic)", 41512)]
    fig, ax = plt.subplots(figsize=(7.2, 2.7))
    barh(ax, [k for k, _ in flags], [v for _, v in flags])
    ax.set_title("Flagged pairs by issue, 103,357 rows in total",
                 loc="left", fontsize=10.5, color=INK, pad=10)
    save(fig, "fig_flags.png")

    # 7. Sorani rate by source -------------------------------------------------
    rate = [("telegram_pertok", 0.6), ("telegram_jihana", 0.9),
            ("sh2_unicodefixed", 1.2), ("telegram_badini_book", 1.6),
            ("pertokenbadini", 1.7), ("spirez", 2.6), ("zcks", 13.8),
            ("facebook", 21.0)]
    fig, ax = plt.subplots(figsize=(7.2, 2.5))
    barh(ax, [k for k, _ in rate], [v for _, v in rate], fmt="{:.1f}%")
    ax.set_title("Suspected Sorani context, share of each source's pairs",
                 loc="left", fontsize=10.5, color=INK, pad=10)
    save(fig, "fig_sorani_rate.png")

    # 8. Pairs returned per successful call ------------------------------------
    ppc = {1: 4, 2: 34, 3: 361, 4: 237913, 5: 3}
    fig, ax = plt.subplots(figsize=(7.2, 2.0))
    ks = sorted(ppc)
    ax.bar([str(k) for k in ks], [ppc[k] for k in ks], width=0.55,
           color=ACCENT, zorder=3)
    ax.set_yscale("log")
    ax.set_ylabel("calls (log scale)")
    ax.set_xlabel("QA pairs returned")
    for k in ks:
        ax.text(str(k), ppc[k] * 1.35, f"{ppc[k]:,}", ha="center",
                fontsize=8.2, color=INK)
    ax.set_ylim(0.6, ppc[4] * 12)
    ax.grid(axis="x", visible=False)
    ax.set_title("The model returned exactly 4 pairs on 99.8% of successful calls",
                 loc="left", fontsize=10.5, color=INK, pad=10)
    save(fig, "fig_pairs_per_call.png")

    print(f"\n8 figures -> {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
