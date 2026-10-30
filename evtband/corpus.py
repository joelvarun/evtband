"""Loading the measured channel volume tables and building target sets."""
from __future__ import annotations

import json
import random
from dataclasses import dataclass
from pathlib import Path

Key = tuple[str, int]


@dataclass
class Channel:
    name: str
    total: int
    capped: bool
    read_ms: int
    volumes: dict[Key, int]

    @property
    def keys(self) -> list[Key]:
        return sorted(self.volumes)

    @property
    def providers(self) -> list[str]:
        return sorted({p for p, _ in self.volumes})


def load(path: str | Path) -> tuple[dict, dict[str, Channel]]:
    raw = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    meta = {k: v for k, v in raw.items() if k != "channels"}
    out: dict[str, Channel] = {}
    for name, c in raw["channels"].items():
        volumes: dict[Key, int] = {}
        for key, n in c["counts"].items():
            provider, _, eid = key.rpartition("|")
            volumes[(provider, int(eid))] = int(n)
        out[name] = Channel(name, int(c["total"]), bool(c["capped"]), int(c["read_ms"]), volumes)
    return meta, out


# ------------------------------------------------------------ target sets

def rare_targets(ch: Channel, k: int) -> set[Key]:
    """The k lowest-volume event kinds: the triage case where waste hurts most."""
    order = sorted(ch.volumes.items(), key=lambda kv: (kv[1], kv[0]))
    return {key for key, _ in order[:k]}


def random_targets(ch: Channel, k: int, rng: random.Random) -> set[Key]:
    keys = ch.keys
    k = min(k, len(keys))
    return set(rng.sample(keys, k))


def tail_targets(ch: Channel, k: int, rng: random.Random, quantile: float = 0.6) -> set[Key]:
    """Sample k kinds from the low-volume tail: a realistic monitoring rule set.

    Rule sets name specific failures, which are rare by construction, while the
    noise that surrounds them in ID space is not.
    """
    order = sorted(ch.volumes.items(), key=lambda kv: (kv[1], kv[0]))
    pool = [key for key, _ in order[: max(k, int(len(order) * quantile))]]
    k = min(k, len(pool))
    return set(rng.sample(pool, k))


def targets_for(ch: Channel, mode: str, k: int) -> set[Key]:
    """Deterministic target set, so a plan can be rebuilt from its label."""
    rng = random.Random(f"{ch.name}|{mode}|{k}")
    if mode == "rare":
        return rare_targets(ch, k)
    if mode == "tail":
        return tail_targets(ch, k, rng)
    if mode == "random":
        return random_targets(ch, k, rng)
    raise ValueError(f"unknown mode {mode}")


def describe(ch: Channel, targets: set[Key]) -> str:
    hit = sum(ch.volumes.get(t, 0) for t in targets)
    return (
        f"{ch.name}: {len(targets)} target kinds across "
        f"{len({p for p, _ in targets})} providers, {hit} target events "
        f"of {ch.total} in channel"
    )
