"""Exact minimum-waste EventID selector planning for Windows Event Log queries.

A selector is either a plain EventID disjunction, which is what a hand-written
channel filter looks like:

    *[System[(EventID=1 or (EventID>=7 and EventID<=9) or EventID=41)]]

or a disjunction of provider-qualified groups, which cannot pick up another
provider's events but costs one expression per provider:

    *[System[(Provider[@Name='A'] and (EventID=1 or (EventID>=7 and EventID<=9)))
          or (Provider[@Name='B'] and EventID=41)]]

Every comparison term and every provider term costs one expression against the
host's per-selector budget. A target set with more distinct event IDs than the
budget allows cannot be expressed exactly, so some terms must widen into ranges
that also admit non-target IDs. Which ranges you pick decides how many extra
events the channel hands back, and that is what this module optimises exactly.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

Key = tuple[str, int]


@dataclass(frozen=True)
class Gap:
    """A run of non-target IDs between two consecutive target IDs of a provider."""

    provider: str
    lo: int          # target ID immediately below the gap
    hi: int          # target ID immediately above the gap
    weight: int      # observed events with lo < EventID < hi for this provider

    @property
    def width(self) -> int:
        return self.hi - self.lo - 1


@dataclass
class ProviderTargets:
    provider: str
    ids: tuple[int, ...]           # sorted target IDs
    gaps: tuple[Gap, ...]          # len(ids) - 1 gaps, in ID order
    fixed_waste: int = 0           # waste no interval choice can avoid

    @property
    def span_waste(self) -> int:
        """Waste if all target IDs collapse into one range."""
        return self.fixed_waste + sum(g.weight for g in self.gaps)


ANY = "*"   # pseudo provider used by the ID-only selector form


@dataclass
class Plan:
    """A concrete selector: per provider, the intervals that cover its targets.

    ``qualified`` selects the form. Qualified plans name each provider and so
    cannot match another provider's events, at the price of one expression per
    provider. Unqualified plans filter on EventID alone, which is what a
    hand-written channel filter looks like, and pick up any other provider in
    the channel that happens to reuse a covered ID.
    """

    channel: str
    intervals: dict[str, tuple[tuple[int, int], ...]]
    predicted_waste: int
    policy: str = "exact"
    qualified: bool = True

    @property
    def cost(self) -> int:
        """Expression count under the calibrated flat single-chain model.

        One expression per comparison term, one per provider term, plus one
        surcharge for using any range at all. For a single-provider or
        EventID-only plan this is exactly what the service counts. For a
        multi-provider plan the service nests each provider in its own group
        and applies the limit per chain, so it accepts more than this number;
        the figure is then a conservative bound, not the service's own count.
        """
        terms = sum(len(v) for v in self.intervals.values())
        providers = len(self.intervals) if self.qualified else 0
        has_range = any(a != b for v in self.intervals.values() for a, b in v)
        return terms + providers + (1 if has_range else 0)

    @property
    def n_terms(self) -> int:
        return sum(len(v) for v in self.intervals.values())

    def covers(self, provider: str, eid: int) -> bool:
        key = provider if self.qualified else ANY
        for lo, hi in self.intervals.get(key, ()):
            if lo <= eid <= hi:
                return True
        return False

    def waste_against(self, volumes: dict[Key, int], targets: set[Key]) -> int:
        """Recompute waste directly from the volume table (used to self-check)."""
        total = 0
        for (prov, eid), n in volumes.items():
            if (prov, eid) in targets:
                continue
            if self.covers(prov, eid):
                total += n
        return total

    def to_xpath(self) -> str:
        groups = []
        for prov in sorted(self.intervals):
            terms = []
            for lo, hi in self.intervals[prov]:
                if lo == hi:
                    terms.append(f"EventID={lo}")
                else:
                    terms.append(f"(EventID>={lo} and EventID<={hi})")
            ids = " or ".join(terms)
            body = ids if len(terms) == 1 else f"({ids})"
            if self.qualified:
                groups.append(f"(Provider[@Name='{_esc(prov)}'] and {body})")
            else:
                groups.append(body)
        inner = groups[0] if len(groups) == 1 else " or ".join(groups)
        return f"*[System[{inner}]]"


def _esc(name: str) -> str:
    """XPath 1.0 has no escape inside a string literal, so refuse the input.

    No Windows provider name observed on this host contains an apostrophe. If
    one does, the selector cannot be written in this grammar and silently
    mangling it would be worse than saying so.
    """
    if "'" in name:
        raise ValueError(f"provider name contains an apostrophe: {name!r}")
    return name


def build_targets(
    targets: Iterable[Key], volumes: dict[Key, int], qualified: bool = True
) -> list[ProviderTargets]:
    """Group targets by provider and weigh the gaps between consecutive IDs.

    With ``qualified=False`` every target collapses onto one pseudo provider and
    gap weights are summed over the whole channel, which is exactly what an
    EventID-only selector picks up.
    """
    targets = set(targets)
    target_ids = {eid for _, eid in targets}
    by_provider: dict[str, list[int]] = {}
    for prov, eid in targets:
        by_provider.setdefault(prov if qualified else ANY, []).append(eid)

    # An EventID-only selector also matches target IDs emitted by providers we
    # did not ask for. No choice of intervals avoids that, so it is a constant.
    fixed = 0
    if not qualified:
        fixed = sum(
            n for (p, e), n in volumes.items()
            if e in target_ids and (p, e) not in targets
        )

    out: list[ProviderTargets] = []
    for prov, ids in sorted(by_provider.items()):
        ids = tuple(sorted(set(ids)))
        gaps = []
        for lo, hi in zip(ids, ids[1:]):
            w = 0
            for (p, e), n in volumes.items():
                if not lo < e < hi:
                    continue
                if qualified:
                    if p == prov and (p, e) not in targets:
                        w += n
                elif e not in target_ids:
                    w += n
            gaps.append(Gap(prov, lo, hi, w))
        out.append(ProviderTargets(prov, ids, tuple(gaps), fixed))
        fixed = 0  # charged once, to the first group
    return out
