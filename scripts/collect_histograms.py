"""Build per-channel (provider, EventID) volume tables from the live event logs.

Uses wevtutil's raw XML stream rather than Get-WinEvent: the service hands over
the record XML with no .NET event object construction, which is roughly two
orders of magnitude faster and makes a full-channel pass affordable.

Only the provider name and the event ID are kept. No event message, no user
data, no payload of any kind is read or stored.
"""
from __future__ import annotations

import json
import platform
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

RE_PROVIDER = re.compile(r"<Provider Name='([^']*)'")
RE_EVENTID = re.compile(r"<EventID[^>]*>(\d+)</EventID>")

DEFAULT_CHANNELS = [
    "System",
    "Application",
    "Microsoft-Windows-SMBServer/Operational",
    "Microsoft-Windows-Ntfs/Operational",
    "Microsoft-Windows-GroupPolicy/Operational",
    "Microsoft-Windows-Kernel-PnP/Device Management",
    "Microsoft-Windows-Storage-ClassPnP/Operational",
    "Microsoft-Windows-StateRepository/Operational",
    "Microsoft-Windows-AppReadiness/Admin",
    "Microsoft-Windows-Hyper-V-VmSwitch-Operational",
    "Microsoft-Windows-SmbClient/Connectivity",
    "Microsoft-Windows-Windows Defender/Operational",
    "Microsoft-Windows-Storage-Storport/Operational",
    "Microsoft-Windows-Store/Operational",
]


def tally(channel: str) -> tuple[dict[str, int], int, float]:
    """Return ((provider|id) -> count, records, seconds) for a whole channel."""
    t0 = time.time()
    proc = subprocess.run(
        ["wevtutil.exe", "qe", channel, "/f:xml"],
        capture_output=True,
        text=True,
        errors="replace",
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip()[:200] or f"wevtutil exit {proc.returncode}")
    counts: dict[str, int] = {}
    n = 0
    for chunk in proc.stdout.split("</Event>"):
        m_id = RE_EVENTID.search(chunk)
        if not m_id:
            continue
        m_p = RE_PROVIDER.search(chunk)
        key = f"{m_p.group(1) if m_p else ''}|{int(m_id.group(1))}"
        counts[key] = counts.get(key, 0) + 1
        n += 1
    return counts, n, time.time() - t0


def main(argv: list[str]) -> int:
    out_path = ROOT / "data" / "histograms.json"
    channels = argv[1:] or DEFAULT_CHANNELS
    result: dict[str, dict] = {}
    for ch in channels:
        try:
            counts, n, secs = tally(ch)
        except Exception as exc:  # a channel may be disabled or need elevation
            print(f"skip {ch}: {exc}")
            continue
        if n == 0:
            print(f"skip {ch}: empty")
            continue
        result[ch] = {
            "total": n,
            "capped": False,
            "read_ms": int(secs * 1000),
            "counts": counts,
        }
        print(f"{ch:<52} records={n:<7} kinds={len(counts):<5} {secs:6.2f}s")

    payload = {
        "os_caption": platform.platform(),
        "os_build": platform.version(),
        "collected": time.strftime("%Y-%m-%d"),
        "reader": "wevtutil qe /f:xml",
        "channels": result,
    }
    out_path.write_text(json.dumps(payload), encoding="utf-8")
    print(f"wrote {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
