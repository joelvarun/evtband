"""Exhaustive verification of the optimiser against brute force.

Two independent things are checked for every random instance:

1. the gap-sum waste formula agrees with a direct recount over the volume table;
2. the greedy frontier matches the true minimum waste at every budget, where the
   truth is found by enumerating every way to cut each provider's target IDs
   into intervals.

Both selector forms are checked: provider-qualified and EventID only.
"""
from __future__ import annotations

import itertools
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from evtband.optimize import _make_plan, frontier, optimal, width_greedy
from evtband.plan import build_targets


def random_instance(rng: random.Random):
    n_prov = rng.randint(1, 2)
    volumes: dict[tuple[str, int], int] = {}
    targets: set[tuple[str, int]] = set()
    for p in range(n_prov):
        prov = f"P{p}"
        ids = rng.sample(range(1, 19), rng.randint(2, 6))
        for eid in ids:
            targets.add((prov, eid))
        # non-target IDs with volumes as skewed as a real channel's
        for eid in range(1, 19):
            if rng.random() < 0.6:
                volumes[(prov, eid)] = rng.choice([1, 2, 5, 40, 900, 5000])
    return volumes, targets


def brute_force(channel, pts, volumes, targets, qualified=True):
    """min waste per expression cost, over every interval cutting."""
    per_provider = []
    for pt in pts:
        opts = []
        for r in range(len(pt.gaps) + 1):
            for combo in itertools.combinations(range(len(pt.gaps)), r):
                opts.append({(pt.provider, i) for i in combo})
        per_provider.append(opts)

    best: dict[int, int] = {}
    for choice in itertools.product(*per_provider):
        opened = set().union(*choice) if choice else set()
        plan = _make_plan(channel, pts, opened, "brute", qualified)
        # recount waste straight from the volume table
        waste = plan.waste_against(volumes, targets)
        assert waste == plan.predicted_waste, (waste, plan.predicted_waste)
        c = plan.cost
        if c not in best or waste < best[c]:
            best[c] = waste
    # make it monotone: a bigger budget can always fall back to a cheaper plan
    out = {}
    run = None
    for c in sorted(best):
        run = best[c] if run is None else min(run, best[c])
        out[c] = run
    return out


def main(trials: int = 400, seed: int = 20261030) -> int:
    rng = random.Random(seed)
    checked = 0
    width_losses = 0
    width_ratio_sum = 0.0
    for _ in range(trials):
        volumes, targets = random_instance(rng)
        for qualified in (True, False):
            pts = build_targets(targets, volumes, qualified)
            truth = brute_force("c", pts, volumes, targets, qualified)
            base_cost = min(truth)
            for budget in range(base_cost, max(truth) + 2):
                got = optimal("c", pts, budget, qualified)
                want = truth[min(budget, max(truth))]
                assert got.cost <= budget, (got.cost, budget)
                assert got.predicted_waste == want, (
                    f"budget={budget} greedy={got.predicted_waste} brute={want}"
                )
                checked += 1
                w = width_greedy("c", pts, budget, qualified)
                if w.predicted_waste > got.predicted_waste:
                    width_losses += 1
                    width_ratio_sum += (w.predicted_waste + 1) / (got.predicted_waste + 1)
    print(f"instances           : {trials}")
    print(f"budget points        : {checked}")
    print(f"greedy == brute      : all {checked}")
    print(f"width heuristic lost : {width_losses} of {checked} budget points")
    if width_losses:
        print(f"mean waste ratio when it lost: {width_ratio_sum / width_losses:.1f}x")
    return 0


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 400
    raise SystemExit(main(n))
