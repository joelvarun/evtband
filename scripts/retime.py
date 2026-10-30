"""Re-time the headline instance's selectors on an otherwise idle host.

scripts/bench.py measures every instance in one long pass, and runs the slow
selectors once because they are an order of magnitude apart. This script
re-measures just the instance the chart puts on top, three interleaved rounds
for every selector including the slow ones, and writes data/retime.json. The
event counts are unchanged, since they are exact either way; only the times are
replaced, and only for that one instance.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from evtband import corpus, host

ROUNDS = 3


def main() -> int:
    bench = json.loads((ROOT / "data" / "bench.json").read_text(encoding="utf-8"))
    cands = [
        r for r in bench["instances"]
        if not r["qualified"] and "enumerate" not in r["policies"]
    ]
    rec = max(cands, key=lambda r: (r["channel_total"], r["n_targets"]))
    print(f"re-timing {rec['instance']}")

    queries = {n: p["xpath"] for n, p in rec["policies"].items()}
    best: dict[str, tuple[int, float]] = {}
    for r in range(ROUNDS):
        for name, xp in queries.items():
            n, ms = host.time_xpath(rec["channel"], xp)
            if name not in best or ms < best[name][1]:
                best[name] = (n, ms)
            print(f"  round {r + 1} {name:<10} {n:>7,} events {ms:>9.0f} ms")

    _, channels = corpus.load(ROOT / "data" / "histograms.json")
    ch = channels[rec["channel"]]
    mode, k = rec["mode"].rsplit("-", 1)
    targets = corpus.targets_for(ch, mode, int(k))
    pf_best = None
    for r in range(ROUNDS):
        pf_n, pf_seen, pf_ms = host.time_postfilter(rec["channel"], targets)
        if pf_best is None or pf_ms < pf_best[2]:
            pf_best = (pf_n, pf_seen, pf_ms)
        print(f"  round {r + 1} postfilter {pf_n:>7,} matched, "
              f"{pf_seen:>7,} read, {pf_ms:>9.0f} ms")

    out = {
        "instance": rec["instance"],
        "rounds": ROUNDS,
        "policies": {
            n: {"measured_events": v[0], "ms": round(v[1], 1)} for n, v in best.items()
        },
        "postfilter": {"matched": pf_best[0], "scanned": pf_best[1],
                       "ms": round(pf_best[2], 1)},
    }
    dest = ROOT / "data" / "retime.json"
    dest.write_text(json.dumps(out, indent=1), encoding="utf-8")
    print(f"wrote {dest}")

    for name, p in rec["policies"].items():
        print(f"  {name:<10} bench {p['ms']:>9.0f} ms -> retimed "
              f"{out['policies'][name]['ms']:>9.0f} ms  "
              f"(events {p['measured_events']:,} vs "
              f"{out['policies'][name]['measured_events']:,})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
