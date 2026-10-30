"""Talking to the Windows Event Log service, and measuring what it will accept.

Nothing here is quoted from documentation. The per-selector expression budget is
discovered by probing the live service: a selector with a provider name that
does not exist in the channel is rejected instantly by the channel's provider
index, so a probe costs no scan. The service then distinguishes the two answers
we care about, "the specified query is invalid" (over budget) from "no events
were found" (accepted), without reading a single record.
"""
from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass

PROBE_PROVIDER = "evtband-probe-provider-does-not-exist"


class InvalidQuery(RuntimeError):
    """The service refused the selector, almost always an over-budget one."""


def powershell(script: str, timeout: int = 1800) -> str:
    proc = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if proc.returncode != 0 and not proc.stdout.strip():
        err = proc.stderr.strip()
        if "query is invalid" in err:
            raise InvalidQuery(err[:200])
        raise RuntimeError(f"powershell failed: {err[:400]}")
    return proc.stdout


def _probe_script(channel: str, xpath: str) -> str:
    xp = xpath.replace("'", "''")
    return (
        "$ErrorActionPreference='Stop'; "
        f"try {{ $null = Get-WinEvent -LogName '{channel}' -FilterXPath '{xp}' "
        "-MaxEvents 1 -ErrorAction Stop; 'ACCEPTED' } "
        "catch { if ($_.Exception.Message -match 'No events were found') "
        "{ 'ACCEPTED' } elseif ($_.Exception.Message -match 'query is invalid') "
        "{ 'REJECTED' } else { 'ERROR:' + $_.Exception.Message } }"
    )


def accepts(channel: str, xpath: str) -> bool:
    out = powershell(_probe_script(channel, xpath), timeout=300).strip()
    if out.startswith("ERROR"):
        raise RuntimeError(out)
    return out == "ACCEPTED"


def _flat(n_eq: int, n_range: int, provider: bool) -> str:
    terms = [f"EventID={9000 + i}" for i in range(n_eq)]
    terms += [
        f"(EventID>={20000 + 4 * i} and EventID<={20001 + 4 * i})" for i in range(n_range)
    ]
    body = " or ".join(terms)
    if provider:
        return f"*[System[Provider[@Name='{PROBE_PROVIDER}'] and ({body})]]"
    return f"*[System[({body})]]"


@dataclass
class Budget:
    """Calibrated expression budget for a flat, single-chain selector.

    Measured on Windows 11 build 26200: a chain accepts 23 operands, a
    parenthesised group operand costs one extra, so 23 bare equalities fit but
    only 22 ranges, and a provider term plus 22 equalities fit. Nested
    multi-provider selectors are limited per chain instead and accept more; this
    budget is a conservative bound for those.
    """

    max_equalities: int
    max_ranges: int
    max_with_provider: int
    total: int
    range_surcharge: int

    def cost(self, n_terms: int, n_providers: int, has_range: bool) -> int:
        return n_terms + n_providers + (self.range_surcharge if has_range else 0)

    def to_dict(self) -> dict:
        return {
            "max_equalities": self.max_equalities,
            "max_ranges": self.max_ranges,
            "max_with_provider": self.max_with_provider,
            "total": self.total,
            "range_surcharge": self.range_surcharge,
        }


def calibrate(channel: str = "System", hi: int = 48, verbose: bool = True) -> Budget:
    """Find the host's selector budget by bisection on three selector shapes."""

    def largest(make) -> int:
        lo, hi_ = 1, hi
        if accepts(channel, make(hi_)):
            return hi_
        while lo < hi_:
            mid = (lo + hi_ + 1) // 2
            if accepts(channel, make(mid)):
                lo = mid
            else:
                hi_ = mid - 1
        return lo

    max_eq = largest(lambda n: _flat(n, 0, False))
    max_rg = largest(lambda n: _flat(0, n, False))
    max_pv = largest(lambda n: _flat(n, 0, True))
    surcharge = max_eq - max_rg
    b = Budget(max_eq, max_rg, max_pv, max_eq, surcharge)
    if verbose:
        print(
            f"calibrated on {channel}: equalities={max_eq} ranges={max_rg} "
            f"provider+equalities={max_pv} range_surcharge={surcharge}"
        )
    return b


# ------------------------------------------------------------------ timing

def time_xpath(channel: str, xpath: str, timeout: int = 1800) -> tuple[int, float]:
    """Return (events returned, wall-clock ms) for one XPath selector."""
    xp = xpath.replace("'", "''")
    script = (
        "$sw=[Diagnostics.Stopwatch]::StartNew(); $n=0; "
        f"try {{ $n = (Get-WinEvent -LogName '{channel}' -FilterXPath '{xp}' "
        "-ErrorAction Stop | Measure-Object).Count } "
        "catch { if ($_.Exception.Message -match 'No events were found') { $n = 0 } "
        "else { throw } } "
        "$sw.Stop(); "
        "ConvertTo-Json -Compress @{n=$n; ms=$sw.Elapsed.TotalMilliseconds}"
    )
    out = powershell(script, timeout=timeout)
    d = json.loads(out.strip().splitlines()[-1])
    return int(d["n"]), float(d["ms"])


def time_postfilter(
    channel: str, targets: set[tuple[str, int]], timeout: int = 3600
) -> tuple[int, int, float]:
    """Fetch the whole channel and filter client side: the common practice.

    Returns (target events matched, records read, wall-clock ms). The records
    read are counted rather than taken from the volume table, so the caller can
    see the channel grow underneath it.
    """
    keys = sorted({f"{p}|{e}" for p, e in targets})
    arr = ",".join("'" + k.replace("'", "''") + "'" for k in keys)
    script = (
        f"$want = [System.Collections.Generic.HashSet[string]]@({arr}); "
        "$sw=[Diagnostics.Stopwatch]::StartNew(); $n=0; $seen=0; "
        f"Get-WinEvent -LogName '{channel}' -ErrorAction SilentlyContinue | "
        "ForEach-Object { $seen = $seen + 1; "
        "if ($want.Contains(\"$($_.ProviderName)|$($_.Id)\")) "
        "{ $n = $n + 1 } }; "
        "$sw.Stop(); "
        "ConvertTo-Json -Compress @{n=$n; seen=$seen; ms=$sw.Elapsed.TotalMilliseconds}"
    )
    out = powershell(script, timeout=timeout)
    d = json.loads(out.strip().splitlines()[-1])
    return int(d["n"]), int(d["seen"]), float(d["ms"])
