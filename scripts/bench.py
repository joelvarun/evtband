"""Measure every selector policy against the live Windows Event Log.

For each channel and target set the script builds one selector per policy, runs
it through Get-WinEvent, and records both the number of events the service hands
back and the wall-clock time. Fast policies are run round robin several times
and the fastest run is kept, so slow drift on the host cannot favour one policy.

Results land in data/bench.json.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from evtband import corpus, host
from evtband.optimize import exact_ids, frontier, optimal, span_merge, width_greedy
from evtband.plan import build_targets

K_VALUES = (24, 40)
MODES = ("rare", "tail")
ROUNDS = 3
FAST_MS = 3000.0          # only re-run policies this quick
MIN_KINDS = 26


def provider_only_xpath(providers, budget: int) -> str:
    """Name the providers and accept every event they emit.

    Past the expression budget the providers cannot even be listed in one
    selector, and the only selector left is the whole channel.
    """
    # A parenthesised disjunction of provider terms costs one expression for the
    # group itself, measured on this host at 22 providers maximum.
    if len(providers) > budget - 1:
        return "*"
    terms = [f"Provider[@Name='{p}']" for p in sorted(providers)]
    body = terms[0] if len(terms) == 1 else "(" + " or ".join(terms) + ")"
    return f"*[System[{body}]]"


def instance_specs(channels):
    for ch in sorted(channels.values(), key=lambda c: -len(c.keys)):
        if len(ch.keys) < MIN_KINDS or ch.total < 2000:
            continue
        for mode in MODES:
            for k in K_VALUES:
                if k <= len(ch.keys) - 4:
                    yield ch, mode, k, False
    for name in ("System", "Application"):
        ch = channels.get(name)
        if ch is None:
            continue
        for mode in MODES:
            for k in K_VALUES:
                yield ch, mode, k, True


def main() -> int:
    meta, channels = corpus.load(ROOT / "data" / "histograms.json")
    budget = host.calibrate()

    out = {
        "meta": meta,
        "budget": budget.to_dict(),
        "rounds": ROUNDS,
        "fast_ms": FAST_MS,
        "instances": [],
        "channels": {},
        "skipped": [],
    }

    specs = list(instance_specs(channels))
    print(f"{len(specs)} candidate instances")

    for ch, mode, k, qualified in specs:
        targets = corpus.targets_for(ch, mode, k)
        if len(targets) < 6:
            continue
        pts = build_targets(targets, ch.volumes, qualified)
        cheapest = frontier(ch.name, pts, "exact", qualified)[0]
        tag = f"{ch.name}|{mode}-{k}|{'qualified' if qualified else 'idonly'}"
        if cheapest.cost > budget.total:
            out["skipped"].append({"instance": tag, "cheapest_cost": cheapest.cost})
            print(f"  [skip] {tag}: cheapest selector needs {cheapest.cost} expressions")
            continue

        best_plan = optimal(ch.name, pts, budget.total, qualified)
        if best_plan.predicted_waste == 0 and cheapest.predicted_waste == 0:
            out["skipped"].append({"instance": tag, "reason": "no waste to remove"})
            continue

        plans = {
            "span": span_merge(ch.name, pts, qualified),
            "width": width_greedy(ch.name, pts, budget.total, qualified),
            "evtband": best_plan,
        }
        enumerated = exact_ids(ch.name, pts, qualified)
        if enumerated.cost <= budget.total:
            plans["enumerate"] = enumerated

        queries = {n: p.to_xpath() for n, p in plans.items()}
        queries["provider"] = provider_only_xpath(
            {p for p, _ in targets}, budget.total
        )

        best: dict[str, tuple[int, float]] = {}
        rejected: list[str] = []
        for r in range(ROUNDS):
            for name, xp in list(queries.items()):
                if name in rejected or (r and best[name][1] > FAST_MS):
                    continue
                try:
                    n, ms = host.time_xpath(ch.name, xp)
                except host.InvalidQuery:
                    print(f"  [reject] {tag}/{name}: service refused the selector")
                    rejected.append(name)
                    best.pop(name, None)
                    continue
                if name not in best or ms < best[name][1]:
                    best[name] = (n, ms)

        target_events = sum(ch.volumes.get(t, 0) for t in targets)
        rec = {
            "instance": tag,
            "channel": ch.name,
            "mode": f"{mode}-{k}",
            "qualified": qualified,
            "n_targets": len(targets),
            "n_providers": len(pts) if qualified else len({p for p, _ in targets}),
            "target_events": target_events,
            "channel_total": ch.total,
            "policies": {},
        }
        for name, (n, ms) in best.items():
            p = plans.get(name)
            rec["policies"][name] = {
                "measured_events": n,
                "measured_waste": n - target_events,
                "predicted_waste": None if p is None else p.predicted_waste,
                "cost": None if p is None else p.cost,
                "terms": None if p is None else p.n_terms,
                "ms": round(ms, 1),
                "runs": ROUNDS if ms <= FAST_MS else 1,
                "xpath": queries[name],
            }
        out["instances"].append(rec)
        shown = {n: v["measured_events"] for n, v in rec["policies"].items()}
        print(f"  {tag}: targets={target_events} -> {shown}")

    # The common practice baseline: pull the channel and filter in the client.
    for name in sorted({r["channel"] for r in out["instances"]}):
        ch = channels[name]
        t = corpus.rare_targets(ch, 40)
        n, seen, ms = host.time_postfilter(ch.name, t)
        out["channels"][name] = {
            "total": ch.total,
            "kinds": len(ch.keys),
            "providers": len(ch.providers),
            "postfilter_ms": round(ms, 1),
            "postfilter_matched": n,
            "postfilter_scanned": seen,
            "collect_ms": ch.read_ms,
        }
        print(f"  postfilter {name}: {ms:.0f} ms for {ch.total} records")

    dest = ROOT / "data" / "bench.json"
    dest.write_text(json.dumps(out, indent=1), encoding="utf-8")
    print(f"\nwrote {dest}: {len(out['instances'])} instances, "
          f"{len(out['skipped'])} skipped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
