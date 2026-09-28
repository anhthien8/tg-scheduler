#!/usr/bin/env python3
"""
Campaign Metrics Backtester CLI.

Data-driven replay of historical outreach performance, plus a reproducible
probabilistic simulator. Unlike the old version, DB/file modes:

  * do NOT fabricate an "open rate" (Telegram has no read receipts) — reach is
    labelled as a reply-based lower bound;
  * attribute replies to attempts by (account_id, user_id) with time ordering;
  * report per-variant 95% Wilson confidence intervals and a two-proportion
    z-test instead of a fake idx%2 split;
  * measure speed-to-reply (median / p90 latency).

Modes:
  db    — replay a SQLite DB (optionally scoped to one campaign).
  file  — replay a JSON/CSV export of attempt rows.
  sim   — reproducible probabilistic simulation (seed).
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import random
import sys

# Windows consoles default to cp1252 and choke on the report's ≥/—/α glyphs.
# Force UTF-8 (project rule AGENTS.md #2) when the stream allows it.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8")
        except Exception:
            pass

from backtest.analytics import Funnel
from backtest.replay import build_funnels, overall_funnel
from backtest.report import render_report

# ── File mode ───────────────────────────────────────────────────────────────

def _load_records(path: str) -> list[dict]:
    if not os.path.exists(path):
        print(f"Error: File not found {path}", file=sys.stderr)
        sys.exit(1)
    if path.endswith(".json"):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    if path.endswith(".csv"):
        with open(path, encoding="utf-8-sig") as f:
            return list(csv.DictReader(f))
    print("Error: Unsupported file format. Provide .json or .csv", file=sys.stderr)
    sys.exit(1)


def funnels_from_records(records: list[dict]) -> dict[int, Funnel]:
    """Build funnels from pre-aggregated rows: one row per attempt.

    Expected keys (all optional except sent/replied):
      template_variant_index, sent, replied, converted, onboarded, reply_latency_sec
    """
    funnels: dict[int, Funnel] = {}
    for r in records:
        variant = int(r.get("template_variant_index", 0) or 0)
        funnel = funnels.setdefault(variant, Funnel())
        if not _truthy(r.get("sent", "true")):
            continue
        funnel.sent += 1
        if _truthy(r.get("replied", "false")):
            funnel.replied += 1
            lat = r.get("reply_latency_sec")
            if lat not in (None, ""):
                funnel.latencies.append(float(lat))
        if _truthy(r.get("converted", "false")):
            funnel.converted += 1
        if _truthy(r.get("onboarded", "false")):
            funnel.onboarded += 1
    return funnels


def _truthy(value) -> bool:
    return str(value).strip().lower() in ("true", "1", "yes", "y")


# ── Sim mode ────────────────────────────────────────────────────────────────

def simulate(targets: int, reply_p: float, convert_p: float,
             onboard_p: float, seed: int, variants: int = 2) -> dict[int, Funnel]:
    """Reproducible probabilistic funnel simulation.

    Note: no 'open' step is modelled — there is no honest open signal. reply_p
    is the probability a sent message yields a reply.
    """
    random.seed(seed)
    funnels: dict[int, Funnel] = {}
    for idx in range(targets):
        variant = idx % max(variants, 1)
        funnel = funnels.setdefault(variant, Funnel())
        funnel.sent += 1
        if random.random() < reply_p:
            funnel.replied += 1
            funnel.latencies.append(random.uniform(60, 7200))
            if random.random() < convert_p:
                funnel.converted += 1
                if random.random() < onboard_p:
                    funnel.onboarded += 1
    return funnels


# ── Main ────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Campaign Metrics Backtester")
    sub = parser.add_subparsers(dest="mode", required=True)

    db_p = sub.add_parser("db", help="Replay a SQLite database")
    db_p.add_argument("--db-path", default=os.path.join("data", "scheduler.db"))
    db_p.add_argument("--campaign-id", type=int, default=None,
                      help="Scope to one campaign (omit for all outreach)")

    file_p = sub.add_parser("file", help="Replay a JSON/CSV export")
    file_p.add_argument("--input", "-i", required=True)

    sim_p = sub.add_parser("sim", help="Probabilistic simulation")
    sim_p.add_argument("--targets", type=int, default=1000)
    sim_p.add_argument("--reply-rate", type=float, default=0.30,
                       help="P(reply | sent), 0.0-1.0")
    sim_p.add_argument("--convert-rate", type=float, default=0.30,
                       help="P(convert | replied), 0.0-1.0")
    sim_p.add_argument("--onboard-rate", type=float, default=0.20,
                       help="P(onboard | converted), 0.0-1.0")
    sim_p.add_argument("--variants", type=int, default=2)
    sim_p.add_argument("--seed", type=int, default=42)

    args = parser.parse_args()

    if args.mode == "db":
        if not os.path.exists(args.db_path):
            print(f"Error: Database not found at {args.db_path}", file=sys.stderr)
            sys.exit(1)
        funnels = build_funnels(args.db_path, campaign_id=args.campaign_id)
        scope = f"campaign {args.campaign_id}" if args.campaign_id else "all outreach"
        title = f"Backtest — {scope} ({os.path.basename(args.db_path)})"
    elif args.mode == "file":
        funnels = funnels_from_records(_load_records(args.input))
        title = f"Backtest — {os.path.basename(args.input)}"
    else:
        funnels = simulate(args.targets, args.reply_rate, args.convert_rate,
                           args.onboard_rate, args.seed, args.variants)
        title = f"Simulation — targets={args.targets}, seed={args.seed}"

    overall = overall_funnel(funnels)
    print(render_report(title, funnels, overall))


if __name__ == "__main__":
    main()
