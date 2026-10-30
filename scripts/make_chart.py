"""Render docs/before_after.png from the measured benchmark results."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.ticker import FuncFormatter

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from evtband import corpus
from evtband.optimize import frontier, width_greedy
from evtband.plan import build_targets

SURFACE = "#fcfcfb"
INK = "#0b0b0b"
INK2 = "#52514e"
MUTED = "#898781"
GRID = "#e1e0d9"
AXIS = "#c3c2b7"
NEUTRAL = "#8a8980"
HEURISTIC = "#eb6834"
EXACT = "#2a78d6"

LABELS = {
    "postfilter": "fetch channel, filter in client",
    "provider": "name the providers only",
    "span": "one range, lowest to highest ID",
    "width": "merge by ID distance",
    "evtband": "evtband, exact",
}
ORDER = ["postfilter", "provider", "span", "width", "evtband"]
OVER_BUDGET = "too many providers to name, read it all"


def colour(policy: str) -> str:
    if policy == "evtband":
        return EXACT
    if policy == "width":
        return HEURISTIC
    return NEUTRAL


def thousands(x, _pos=None) -> str:
    return f"{int(x):,}"


def binding(r: dict) -> bool:
    """True when the budget actually forces a merge.

    If an exact enumeration of the target IDs fits the budget there is nothing
    to optimise: every policy lands on the same selector.
    """
    return "enumerate" not in r["policies"]


def pick_headline(bench: dict) -> dict:
    """The widest binding instance on the largest channel."""
    cands = [
        r for r in bench["instances"]
        if not r["qualified"] and binding(r)
        and "width" in r["policies"] and "evtband" in r["policies"]
    ]
    return max(cands, key=lambda r: (r["channel_total"], r["n_targets"]))


def rows_for(bench: dict, rec: dict, retime: dict | None):
    """(policy, extra events, seconds, label) per policy, in display order."""
    ch = bench["channels"][rec["channel"]]
    rt = (retime or {}).get("policies", {}) if (retime or {}).get(
        "instance") == rec["instance"] else {}
    out = []
    for name in ORDER:
        if name == "postfilter":
            ms = ch["postfilter_ms"]
            if rt:
                ms = retime["postfilter"]["ms"]
            out.append((name, rec["channel_total"] - rec["target_events"],
                        ms / 1000.0, LABELS[name]))
            continue
        p = rec["policies"].get(name)
        if p is None:
            continue
        label = LABELS[name]
        if name == "provider" and p["xpath"] == "*":
            label = OVER_BUDGET
        ms = rt[name]["ms"] if name in rt else p["ms"]
        out.append((name, p["measured_waste"], ms / 1000.0, label))
    return out


def barh(ax, rows, values, title, xlabel, fmt):
    names = [r[3] for r in rows]
    cols = [colour(r[0]) for r in rows]
    y = range(len(rows))
    ax.barh(list(y), values, height=0.6, color=cols, zorder=3)
    ax.set_yticks(list(y))
    ax.set_yticklabels(names, fontsize=9, color=INK2)
    ax.invert_yaxis()
    ax.set_title(title, fontsize=11, color=INK, loc="left", pad=10)
    ax.set_xlabel(xlabel, fontsize=9, color=MUTED)
    ax.xaxis.set_major_formatter(FuncFormatter(thousands))
    pad = max(values) * 0.02 if max(values) else 1
    for i, v in enumerate(values):
        ax.text(v + pad, i, fmt(v), va="center", ha="left", fontsize=9, color=INK)
    ax.set_xlim(0, max(values) * 1.22 if max(values) else 1)
    _chrome(ax)


def _chrome(ax, axis="x"):
    ax.set_facecolor(SURFACE)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_color(AXIS)
    ax.spines["left"].set_color(AXIS)
    ax.grid(True, axis=axis, color=GRID, linewidth=0.8, zorder=0)
    ax.tick_params(colors=MUTED, labelsize=9, length=0)


def main() -> int:
    bench = json.loads((ROOT / "data" / "bench.json").read_text(encoding="utf-8"))
    rec = pick_headline(bench)
    rt_path = ROOT / "data" / "retime.json"
    retime = json.loads(rt_path.read_text(encoding="utf-8")) if rt_path.exists() else None
    if retime and retime.get("instance") == rec["instance"]:
        print(f"using data/retime.json for the headline times "
              f"({retime['rounds']} rounds per selector)")
    rows = rows_for(bench, rec, retime)
    budget = bench["budget"]["total"]

    fig = plt.figure(figsize=(13.4, 11.0), facecolor=SURFACE)
    gs = fig.add_gridspec(2, 2, height_ratios=[1.0, 1.08], hspace=0.34, wspace=0.26,
                          left=0.20, right=0.965, top=0.90, bottom=0.06)

    ax1 = fig.add_subplot(gs[0, 0])
    barh(ax1, rows, [r[1] for r in rows],
         "Extra events the service hands back", "non-target events returned",
         lambda v: f"{int(v):,}")

    ax2 = fig.add_subplot(gs[0, 1])
    barh(ax2, rows, [r[2] for r in rows],
         "Time to collect the same target events", "seconds",
         lambda v: f"{v:,.2f}" if v < 10 else f"{v:,.1f}")
    ax2.set_yticklabels([])

    # The exact frontier against the heuristic, at every budget.
    ax4 = fig.add_subplot(gs[1, 0])
    _, channels = corpus.load(ROOT / "data" / "histograms.json")
    ch = channels[rec["channel"]]
    mode, k = rec["mode"].rsplit("-", 1)
    targets = corpus.targets_for(ch, mode, int(k))
    pts = build_targets(targets, ch.volumes, rec["qualified"])
    front = frontier(rec["channel"], pts, "exact", rec["qualified"])
    costs = sorted({p.cost for p in front})
    exact_curve = []
    width_curve = []
    for c in costs:
        best = min((p.predicted_waste for p in front if p.cost <= c), default=None)
        exact_curve.append(best)
        width_curve.append(width_greedy(rec["channel"], pts, c, rec["qualified"]).predicted_waste)
    floor = min(exact_curve)
    ax4.plot(costs, width_curve, color=HEURISTIC, linewidth=2, zorder=3,
             label="merge by ID distance")
    ax4.plot(costs, exact_curve, color=EXACT, linewidth=2, zorder=4,
             label="evtband, exact frontier")
    ax4.axhline(floor, color=AXIS, linewidth=1.2, linestyle=(0, (4, 3)), zorder=2)
    ax4.text(costs[-1], floor * 0.97, "floor: other providers at the target IDs",
             fontsize=8, color=MUTED, va="top", ha="right")
    wm = rec["policies"]["width"]
    em = rec["policies"]["evtband"]
    ax4.plot([wm["cost"]], [wm["measured_waste"]], "o", color=HEURISTIC,
             markersize=9, markeredgecolor=SURFACE, markeredgewidth=2, zorder=6)
    ax4.plot([em["cost"]], [em["measured_waste"]], "o", color=EXACT,
             markersize=9, markeredgecolor=SURFACE, markeredgewidth=2, zorder=6)
    ax4.axvline(budget, color=AXIS, linewidth=1.0, zorder=1)
    ax4.text(budget, floor * 1.08, f" host budget {budget}",
             fontsize=8, color=MUTED, va="bottom", ha="left")
    ax4.set_yscale("log")
    ax4.set_title("Extra events against expression budget", fontsize=11,
                  color=INK, loc="left", pad=10)
    ax4.set_xlabel("expressions in the selector", fontsize=9, color=MUTED)
    ax4.set_ylabel("non-target events returned", fontsize=9, color=MUTED)
    ax4.legend(frameon=False, fontsize=9, labelcolor=INK2, loc="lower left",
               bbox_to_anchor=(0.02, 0.02))
    ax4.yaxis.set_major_formatter(FuncFormatter(thousands))
    _chrome(ax4, axis="both")

    # Every instance, absolute extra events: the heuristic against the exact plan.
    ax3 = fig.add_subplot(gs[1, 1])
    pairs = []
    for r in bench["instances"]:
        if not binding(r):
            continue
        w = r["policies"].get("width")
        e = r["policies"].get("evtband")
        if not w or not e:
            continue
        pairs.append((r, w["measured_waste"], e["measured_waste"]))
    pairs.sort(key=lambda t: t[1])
    labels = [
        t[0]["channel"].replace("Microsoft-Windows-", "").replace("/Operational", "")
        + "  " + t[0]["mode"]
        for t in pairs
    ]
    y = list(range(len(pairs)))
    h = 0.36
    wv = [t[1] for t in pairs]
    ev = [t[2] for t in pairs]
    ax3.barh([i + h / 2 for i in y], wv, height=h, color=HEURISTIC, zorder=3)
    ax3.barh([i - h / 2 for i in y], ev, height=h, color=EXACT, zorder=3)
    top = max(wv + [1])
    for i, (vw, ve) in enumerate(zip(wv, ev)):
        if vw == ve:
            ax3.text(vw + top * 0.015, i, f"{vw:,} both", va="center", ha="left",
                     fontsize=7.5, color=INK)
        else:
            ax3.text(vw + top * 0.015, i + h / 2, f"{vw:,}", va="center",
                     ha="left", fontsize=7.5, color=INK)
            ax3.text(ve + top * 0.015, i - h / 2, f"{ve:,}", va="center",
                     ha="left", fontsize=7.5, color=INK)
    ax3.set_yticks(y)
    ax3.set_yticklabels(labels, fontsize=8, color=INK2)
    ax3.set_xlim(0, top * 1.18)
    ax3.set_title(
        f"Every instance where {budget} expressions force a merge: "
        + chr(10)
        + "extra events returned, heuristic against exact",
        fontsize=11, color=INK, loc="left", pad=10)
    ax3.set_xlabel("non-target events returned", fontsize=9, color=MUTED)
    ax3.xaxis.set_major_formatter(FuncFormatter(thousands))
    _chrome(ax3)

    fig.text(0.013, 0.972,
             "evtband: minimum-waste EventID selectors for Windows Event Log queries",
             fontsize=14, color=INK, ha="left", va="top", weight="bold")
    fig.text(0.013, 0.936,
             f"{rec['channel']} channel, {rec['channel_total']:,} records. "
             f"Target set: {rec['n_targets']} event kinds "
             f"({rec['target_events']:,} events) across "
             f"{rec['n_providers']} providers. "
             f"Host selector budget measured at {budget} expressions, so the "
             f"{rec['n_targets']} IDs cannot be listed exactly.",
             fontsize=9.5, color=INK2, ha="left", va="top")

    handles = [
        plt.Rectangle((0, 0), 1, 1, color=NEUTRAL),
        plt.Rectangle((0, 0), 1, 1, color=HEURISTIC),
        plt.Rectangle((0, 0), 1, 1, color=EXACT),
    ]
    fig.legend(handles,
               ["what people write today", "same budget, merged by ID distance",
                "same budget, evtband exact"],
               loc="upper right", bbox_to_anchor=(0.985, 0.995), frameon=False,
               fontsize=9, labelcolor=INK2, ncol=1)

    dest = ROOT / "docs" / "before_after.png"
    dest.parent.mkdir(exist_ok=True)
    fig.savefig(dest, dpi=140, facecolor=SURFACE)
    print(f"wrote {dest}")
    n_free = sum(1 for r in bench["instances"] if not binding(r))
    print(f"headline instance: {rec['instance']}")
    print(f"binding instances: {len(pairs)}, non-binding: {n_free}")
    wins = sum(1 for _, w, e in pairs if w > e)
    print(f"evtband strictly better on {wins} of {len(pairs)} binding instances")
    for n, w, sec, _ in rows:
        print(f"  {n:<11} extra={w:>7,}  {sec:>8.2f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
