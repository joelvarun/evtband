"""End to end demo: plan a selector, then prove the prediction on the live log.

    python demo.py                      # System channel, 40 rare event kinds
    python demo.py Application 32       # any channel in data/histograms.json
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from evtband import corpus, host
from evtband.optimize import exact_ids, frontier, optimal, span_merge, width_greedy
from evtband.plan import build_targets


def main(argv: list[str]) -> int:
    channel = argv[1] if len(argv) > 1 else "System"
    k = int(argv[2]) if len(argv) > 2 else 40

    meta, channels = corpus.load(ROOT / "data" / "histograms.json")
    if channel not in channels:
        print(f"no volume table for {channel}; run scripts/collect_histograms.py")
        return 2
    ch = channels[channel]

    print("host selector budget")
    print("-" * 72)
    budget = host.calibrate(channel)
    print()

    targets = corpus.rare_targets(ch, k)
    pts = build_targets(targets, ch.volumes, qualified=False)
    target_events = sum(ch.volumes.get(t, 0) for t in targets)

    print("instance")
    print("-" * 72)
    print(corpus.describe(ch, targets))
    print(f"an exact enumeration needs {exact_ids(channel, pts, False).cost} "
          f"expressions, the host allows {budget.total}")
    print()

    print("exact cost and waste frontier (minimum extra events per expression)")
    print("-" * 72)
    print(f"{'expressions':>12} {'terms':>6} {'extra events':>13}")
    seen = set()
    for plan in frontier(channel, pts, "exact", False):
        if plan.cost in seen:
            continue
        seen.add(plan.cost)
        mark = "  <- budget" if plan.cost == budget.total else ""
        if plan.cost <= budget.total + 3 or plan.cost % 6 == 0:
            print(f"{plan.cost:12d} {plan.n_terms:6d} {plan.predicted_waste:13d}{mark}")
    print()

    plans = {
        "span": span_merge(channel, pts, False),
        "width": width_greedy(channel, pts, budget.total, False),
        "evtband": optimal(channel, pts, budget.total, False),
    }

    print(f"policies at {budget.total} expressions, predicted then measured")
    print("-" * 72)
    print(f"{'policy':<9} {'cost':>4} {'predicted':>10} {'measured':>9} "
          f"{'match':>6} {'ms':>8}")
    for name, plan in plans.items():
        n, ms = host.time_xpath(channel, plan.to_xpath())
        measured = n - target_events
        ok = "yes" if measured == plan.predicted_waste else f"off {measured - plan.predicted_waste}"
        print(f"{name:<9} {plan.cost:4d} {plan.predicted_waste:10d} "
              f"{measured:9d} {ok:>6} {ms:8.0f}")

    pf_n, pf_seen, pf_ms = host.time_postfilter(channel, targets)
    pf_waste = pf_seen - pf_n
    predicted = ch.total - target_events
    pf_ok = "yes" if pf_waste == predicted else f"off {pf_waste - predicted}"
    print(f"{'postfilter':<9} {'-':>4} {predicted:10d} "
          f"{pf_waste:9d} {pf_ok:>6} {pf_ms:8.0f}")
    print()
    print("selector")
    print("-" * 72)
    print(plans["evtband"].to_xpath())
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
