"""Print the numbers the README quotes, straight from data/bench.json."""
from __future__ import annotations

import json
import statistics
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    b = json.loads((ROOT / "data" / "bench.json").read_text(encoding="utf-8"))
    print("budget:", b["budget"])
    print("os:", b["meta"].get("os_caption"), b["meta"].get("collected"))
    print()
    binding = [r for r in b["instances"] if "enumerate" not in r["policies"]]
    free = [r for r in b["instances"] if "enumerate" in r["policies"]]
    print(f"instances total={len(b['instances'])} binding={len(binding)} "
          f"enumeration-fits={len(free)} skipped={len(b['skipped'])}")

    ok = off = 0
    for r in b["instances"]:
        for name, p in r["policies"].items():
            if p["predicted_waste"] is None:
                continue
            if p["measured_waste"] == p["predicted_waste"]:
                ok += 1
            else:
                off += 1
                print(f"  MISMATCH {r['instance']}/{name}: "
                      f"predicted {p['predicted_waste']} measured {p['measured_waste']}")
    print(f"prediction exact on {ok} of {ok + off} measured selectors")
    print()

    ratios, sec_ratios = [], []
    print(f"{'instance':<44} {'width':>9} {'evtband':>9} {'x':>7} "
          f"{'w ms':>8} {'e ms':>8}")
    for r in sorted(binding, key=lambda r: r["instance"]):
        w, e = r["policies"]["width"], r["policies"]["evtband"]
        ratio = (w["measured_waste"] + 1) / (e["measured_waste"] + 1)
        ratios.append(ratio)
        sec_ratios.append(w["ms"] / max(e["ms"], 1e-9))
        print(f"{r['instance']:<44} {w['measured_waste']:9,} "
              f"{e['measured_waste']:9,} {ratio:7.1f} {w['ms']:8.0f} {e['ms']:8.0f}")
    if ratios:
        print()
        print(f"waste ratio width/evtband: median {statistics.median(ratios):.1f}x  "
              f"min {min(ratios):.1f}x  max {max(ratios):.1f}x")
        print(f"time ratio  width/evtband: median {statistics.median(sec_ratios):.1f}x")
    print()
    for name, c in b["channels"].items():
        print(f"{name:<48} records={c['total']:<7} kinds={c['kinds']:<4} "
              f"providers={c['providers']:<4} postfilter={c['postfilter_ms']/1000:.1f}s "
              f"wevtutil_collect={c['collect_ms']/1000:.2f}s")
    print()
    for s in b["skipped"]:
        print("skipped:", s)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
