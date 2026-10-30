"""Command line entry point: calibrate, plan, show the frontier, run a query."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import corpus, host
from .optimize import frontier, optimal, span_merge, width_greedy
from .plan import build_targets

DEFAULT_HIST = Path(__file__).resolve().parents[1] / "data" / "histograms.json"


def _targets_from_args(ch, args):
    if args.ids:
        ids = [int(x) for x in args.ids.replace(",", " ").split()]
        provider = args.provider
        if not provider:
            raise SystemExit("--ids needs --provider")
        return {(provider, i) for i in ids}
    if args.rare:
        return corpus.rare_targets(ch, args.rare)
    raise SystemExit("give either --provider/--ids or --rare N")


def cmd_calibrate(args) -> int:
    b = host.calibrate(args.channel, verbose=False)
    print(f"flat selector budget on this host: {b.total} expressions")
    print(f"  largest accepted, bare equalities        {b.max_equalities}")
    print(f"  largest accepted, ranges                 {b.max_ranges}")
    print(f"  largest accepted, provider plus equality {b.max_with_provider}")
    print()
    print("  a provider term costs 1, an equality costs 1, a range costs 1,")
    print(f"  and using any range at all costs {b.range_surcharge} more")
    return 0


def cmd_plan(args) -> int:
    _, channels = corpus.load(args.histograms)
    ch = channels[args.channel]
    targets = _targets_from_args(ch, args)
    q = args.qualified
    pts = build_targets(targets, ch.volumes, q)
    budget = args.budget or host.calibrate(args.channel, verbose=False).total

    best = optimal(ch.name, pts, budget, q)
    print(corpus.describe(ch, targets))
    print(f"budget {budget} expressions, "
          f"{'provider-qualified' if q else 'EventID only'} form")
    for name, plan in (
        ("span", span_merge(ch.name, pts, q)),
        ("width", width_greedy(ch.name, pts, budget, q)),
        ("evtband", best),
    ):
        print(
            f"  {name:8s} cost={plan.cost:3d} terms={plan.n_terms:3d} "
            f"predicted waste={plan.predicted_waste}"
        )
    print()
    print(best.to_xpath())
    if args.run:
        n, ms = host.time_xpath(ch.name, best.to_xpath())
        hit = sum(ch.volumes.get(t, 0) for t in targets)
        print(f"\nran it: {n} events in {ms:.0f} ms "
              f"(targets {hit}, waste {n - hit}, predicted {best.predicted_waste})")
    return 0


def cmd_frontier(args) -> int:
    _, channels = corpus.load(args.histograms)
    ch = channels[args.channel]
    targets = _targets_from_args(ch, args)
    pts = build_targets(targets, ch.volumes, args.qualified)
    print(corpus.describe(ch, targets))
    print(f"{'cost':>5} {'terms':>6} {'min waste':>10}")
    seen = set()
    for plan in frontier(ch.name, pts, "exact", args.qualified):
        if plan.cost in seen:
            continue
        seen.add(plan.cost)
        print(f"{plan.cost:5d} {plan.n_terms:6d} {plan.predicted_waste:10d}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="evtband", description=__doc__)
    ap.add_argument("--histograms", default=str(DEFAULT_HIST))
    sub = ap.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("calibrate", help="probe this host's selector budget")
    c.add_argument("--channel", default="System")
    c.set_defaults(func=cmd_calibrate)

    for name, fn, extra in (("plan", cmd_plan, True), ("frontier", cmd_frontier, False)):
        p = sub.add_parser(name)
        p.add_argument("--channel", required=True)
        p.add_argument("--provider")
        p.add_argument("--ids")
        p.add_argument("--rare", type=int)
        p.add_argument("--budget", type=int)
        p.add_argument(
            "--qualified",
            action="store_true",
            help="name each provider in the selector (costs one expression each)",
        )
        if extra:
            p.add_argument("--run", action="store_true", help="execute the selector")
        p.set_defaults(func=fn)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
