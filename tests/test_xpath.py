"""Check that the emitted XPath says exactly what the plan says.

The benchmark proves the semantics end to end against the live service. This
test is the fast offline half: parse the emitted selector back into intervals
and compare it with the plan that produced it.
"""
from __future__ import annotations

import random
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from evtband.optimize import optimal, span_merge, width_greedy
from evtband.plan import build_targets

RE_EQ = re.compile(r"EventID=(\d+)")
RE_RANGE = re.compile(r"\(EventID>=(\d+) and EventID<=(\d+)\)")
RE_PROV = re.compile(r"^\(Provider\[@Name='([^']*)'\] and (.*)\)$")


def split_top_level(body: str) -> list[str]:
    """Split an or-chain on its top level parentheses depth."""
    parts, depth, start = [], 0, 0
    i = 0
    while i < len(body):
        c = body[i]
        if c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
        elif depth == 0 and body.startswith(" or ", i):
            parts.append(body[start:i])
            i += 4
            start = i
            continue
        i += 1
    parts.append(body[start:])
    return parts


def parse_terms(body: str) -> list[tuple[int, int]]:
    out = [(int(a), int(b)) for a, b in RE_RANGE.findall(body)]
    out += [(int(a), int(a)) for a in RE_EQ.findall(RE_RANGE.sub("", body))]
    return sorted(out)


def main(trials: int = 600, seed: int = 7) -> int:
    rng = random.Random(seed)
    checked = 0
    for _ in range(trials):
        volumes, targets = {}, set()
        for p in range(rng.randint(1, 3)):
            prov = f"Prov {p}"
            for eid in rng.sample(range(1, 60), rng.randint(2, 9)):
                targets.add((prov, eid))
            for eid in range(1, 60):
                if rng.random() < 0.5:
                    volumes[(prov, eid)] = rng.randint(1, 4000)

        for qualified in (True, False):
            pts = build_targets(targets, volumes, qualified)
            base = span_merge("c", pts, qualified).cost
            for budget in (base, base + 3, base + 40):
                for plan in (
                    span_merge("c", pts, qualified),
                    width_greedy("c", pts, budget, qualified),
                    optimal("c", pts, budget, qualified),
                ):
                    xp = plan.to_xpath()
                    assert xp.startswith("*[System[") and xp.endswith("]]"), xp
                    body = xp[len("*[System["):-2]
                    if qualified:
                        groups = split_top_level(body)
                        assert len(groups) == len(plan.intervals), (xp, groups)
                        for group in groups:
                            m = RE_PROV.match(group)
                            assert m, group
                            prov, inner = m.group(1), m.group(2)
                            assert parse_terms(inner) == sorted(
                                plan.intervals[prov]
                            ), (xp, prov)
                    else:
                        want = sorted(next(iter(plan.intervals.values())))
                        assert parse_terms(body) == want, xp
                    # every target lands inside the emitted intervals
                    for prov, eid in targets:
                        assert plan.covers(prov, eid), (prov, eid, xp)
                    checked += 1
    print(f"selectors round tripped: {checked}")
    print("every target covered    : yes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
