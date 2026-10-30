"""The exact optimiser and the heuristics it is measured against.

Exactness argument
------------------
Let the target IDs of provider p be t_1 < ... < t_n, with gap g_i covering the
non-target IDs strictly between t_i and t_{i+1}.  Any selector that covers the
targets of p with j intervals corresponds to leaving exactly j - 1 of those gaps
"open" (an interval boundary falls inside them) and spanning the rest.  A gap
that is spanned contributes its full weight to the waste; a gap that is open
contributes nothing, and no other choice changes that contribution.  Therefore

    waste(plan) = SUM_p span_waste(p) - SUM_{open gaps} weight(gap)

plus, for an EventID-only selector, a constant: the events other providers emit
at the target IDs, which no interval choice can shed.

The expression cost is one interval per provider (and one provider term each, if
the selector names providers) plus exactly one more expression per open gap.
Minimising waste under a budget is therefore cardinality-constrained
maximum-weight subset selection over the gaps of every provider pooled together:
a uniform matroid, where taking the heaviest gaps first is optimal.  One sort
over the pooled gaps yields the exact cost/waste frontier for every budget at
once, and among the selectors that reach the minimum waste the frontier's first
entry is the cheapest.
"""
from __future__ import annotations

from .plan import Plan, ProviderTargets


def _intervals_from_open(pt: ProviderTargets, open_idx: set[int]) -> tuple[tuple[int, int], ...]:
    """Cut pt.ids into intervals at the open gap indices."""
    out = []
    start = pt.ids[0]
    for i, gap in enumerate(pt.gaps):
        if i in open_idx:
            out.append((start, gap.lo))
            start = gap.hi
    out.append((start, pt.ids[-1]))
    return tuple(out)


def _make_plan(
    channel: str,
    pts: list[ProviderTargets],
    open_gaps: set[tuple[str, int]],
    policy: str,
    qualified: bool = True,
) -> Plan:
    intervals = {}
    waste = 0
    for pt in pts:
        idx = {i for i in range(len(pt.gaps)) if (pt.provider, i) in open_gaps}
        intervals[pt.provider] = _intervals_from_open(pt, idx)
        waste += pt.fixed_waste
        waste += sum(g.weight for i, g in enumerate(pt.gaps) if i not in idx)
    return Plan(channel, intervals, waste, policy, qualified)


def _ranked(pts: list[ProviderTargets], key) -> list[tuple[str, int]]:
    pool: list[tuple[object, str, int]] = []
    for pt in pts:
        for i, gap in enumerate(pt.gaps):
            pool.append((key(gap), pt.provider, i))
    pool.sort(key=lambda r: r[0], reverse=True)
    return [(prov, i) for _, prov, i in pool]


def frontier(
    channel: str,
    pts: list[ProviderTargets],
    policy: str = "exact",
    qualified: bool = True,
) -> list[Plan]:
    """Every exactly-optimal plan, from the cheapest selector to the widest one.

    Element e of the result opens the e heaviest gaps, so it is the minimum-waste
    plan among all selectors using that many expressions.
    """
    order = _ranked(pts, key=lambda g: g.weight)
    plans = []
    opened: set[tuple[str, int]] = set()
    plans.append(_make_plan(channel, pts, set(opened), policy, qualified))
    for ref in order:
        opened.add(ref)
        plans.append(_make_plan(channel, pts, set(opened), policy, qualified))
    return plans


def optimal(
    channel: str, pts: list[ProviderTargets], budget: int, qualified: bool = True
) -> Plan:
    """Minimum-waste selector that fits the expression budget."""
    best = None
    for plan in frontier(channel, pts, "exact", qualified):
        if plan.cost <= budget:
            if best is None or plan.predicted_waste < best.predicted_waste:
                best = plan
    if best is None:
        cheapest = frontier(channel, pts, "exact", qualified)[0].cost
        raise ValueError(
            f"budget {budget} too small: the cheapest selector needs "
            f"{cheapest} expressions"
        )
    return best


# ---------------------------------------------------------------- heuristics

def span_merge(channel: str, pts: list[ProviderTargets], qualified: bool = True) -> Plan:
    """One range per provider: min(target) to max(target). The cheapest selector."""
    return _make_plan(channel, pts, set(), "span", qualified)


def width_greedy(
    channel: str, pts: list[ProviderTargets], budget: int, qualified: bool = True
) -> Plan:
    """Split where the ID numbers are furthest apart: the intuitive merge.

    This is what hand-written filters do, and what any "group the nearby IDs"
    clustering produces. It never looks at how many events live in each gap.
    """
    order = _ranked(pts, key=lambda g: g.width)
    return _best_prefix(channel, pts, order, budget, "width", qualified)


def exact_ids(channel: str, pts: list[ProviderTargets], qualified: bool = True) -> Plan:
    """Enumerate every target ID. Zero waste, but usually over budget."""
    opened = {(pt.provider, i) for pt in pts for i in range(len(pt.gaps))}
    return _make_plan(channel, pts, opened, "enumerate", qualified)


def _best_prefix(channel, pts, order, budget, policy, qualified=True) -> Plan:
    """Best plan reachable by opening gaps in the heuristic's own order.

    Scans every prefix rather than stopping at the first over-budget one, so a
    heuristic is credited with the best selector its ranking can produce.
    """
    base = _make_plan(channel, pts, set(), policy, qualified)
    if base.cost > budget:
        raise ValueError(f"budget {budget} too small for {len(pts)} providers")
    best = base
    opened: set[tuple[str, int]] = set()
    for ref in order:
        opened.add(ref)
        cand = _make_plan(channel, pts, set(opened), policy, qualified)
        if cand.cost <= budget and cand.predicted_waste < best.predicted_waste:
            best = cand
    return best
