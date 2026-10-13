"""Reproduce the report's ablations without changing the submission config.

Run: python scripts/run_experiments.py --seed 25
Only the deliberately unsafe tool-routing experiment disables compatibility.
"""

from __future__ import annotations

import argparse
import json
import sys
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from gameday_lab.client import ResilientClient
from gameday_lab.factory import build_client
from gameday_lab.sim.config import LabConfig, load_config
from gameday_lab.sim.contracts import BaseClient
from gameday_lab.sim.engine import evaluate_checks, run_scenario


class UnfilteredClient(ResilientClient):
    """Negative control used solely by the Part F compatibility experiment."""

    def ladder(self, task: str) -> list[str]:
        return list(self.order)


def replay(cfg: LabConfig, scenario: str, seed: int, cached: bool = False,
           unfiltered: bool = False) -> dict:
    client: BaseClient
    if unfiltered:
        client = UnfilteredClient(cfg.provider_infos(), cfg.fallback_order, cfg.client)
    else:
        client = build_client("cached" if cached else "resilient", cfg)
    metrics = run_scenario(cfg, scenario, client, seed).metrics()
    checks = evaluate_checks(metrics, cfg.scenarios[scenario].checks)
    return {**metrics, "checks": checks, "pass": all(checks.values())}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=25)
    parser.add_argument("--out", default="reports/experiments.json")
    args = parser.parse_args()
    cfg = load_config("configs/gameday.yaml", "configs/scenarios.yaml")
    cache_cfg = load_config("configs/gameday.yaml", "configs/scenarios_cache.yaml")
    results: dict = {"seed": args.seed, "budget": {}, "timeout": {}, "compatibility": {}, "cache": {}}

    for label, disabled in [("bounded", False), ("disabled", True)]:
        settings = deepcopy(cfg.client)
        settings["breaker"]["min_calls"] = 1_000_000_000
        settings["max_attempts_per_provider"] = 5
        if disabled:
            settings.update(retry_budget_ratio=100, retry_budget_max_tokens=1e9, retry_budget_min_per_s=1e6)
        variant = replace(cfg, client=settings)
        results["budget"][label] = {s: replay(variant, s, args.seed)
                                    for s in ["G2_overload_storm", "G3_brownout"]}

    for label, timeout in [("configured", cfg.client["attempt_timeout_s"]), ("30s", 30.0)]:
        variant = replace(cfg, client={**cfg.client, "attempt_timeout_s": timeout})
        results["timeout"][label] = {s: replay(variant, s, args.seed)
                                     for s in ["G6_provider_outage", "G4_slow_region"]}

    for label, unfiltered in [("filtered", False), ("unfiltered", True)]:
        results["compatibility"][label] = replay(cfg, "G6_provider_outage", args.seed, unfiltered=unfiltered)

    variants: dict[str, dict[str, Any]] = {
        "configured": {},
        "threshold_0.80": {"similarity_threshold": 0.80},
        "threshold_0.97": {"similarity_threshold": 0.97},
        "ttl_60": {"ttl_s": 60},
        "ttl_300": {"ttl_s": 300},
        "no_stale": {"max_stale_s": 0},
        "starter": {"similarity_threshold": 0.80, "ttl_s": 600, "max_stale_s": 0},
    }
    for label, overrides in variants.items():
        variant = replace(cache_cfg, cache={**cache_cfg.cache, **overrides})
        results["cache"][label] = replay(variant, "C1_faq_outage", args.seed, cached=True)

    output = Path(args.out)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(results, indent=2), encoding="utf-8")
    for group in ["budget", "timeout", "compatibility", "cache"]:
        print(group)
        for label, runs in results[group].items():
            if "availability" in runs:
                runs = {runs["scenario"]: runs}
            for scenario, metrics in runs.items():
                columns = ["availability", "latency_p95_s", "amplification", "retry_ratio", "recovery_s",
                           "incompatible_routes", "faq_availability", "cache_hit_ratio", "wrong_answers",
                           "outdated_answers", "stale_served", "cost_per_good_turn", "give_up_reasons"]
                print(label, scenario, {key: metrics[key] for key in columns if key in metrics})
    print("wrote", output)


if __name__ == "__main__":
    main()
