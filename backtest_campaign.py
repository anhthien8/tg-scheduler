#!/usr/bin/env python3
"""
Campaign Metrics Backtester CLI Script
Generates KPI reports (open-rate, reply-rate, conversion, onboard rate) from campaign data.
Supports:
1. DB Mode: Queries historical SQLite database (logs, replies, followup chats).
2. Sim Mode: Runs a probabilistic simulation based on configured rates (reproducible via seed).
3. File Mode: Parses a JSON/CSV file of historical or mock campaign logs.
"""

import os
import sys
import json
import csv
import random
import sqlite3
import argparse
from typing import Dict, Any, List

def calculate_rates(sent: int, opened: int, replied: int, converted: int, onboarded: int) -> Dict[str, float]:
    """Helper to calculate rate percentages safely."""
    return {
        "open_rate": round((opened / sent * 100), 2) if sent > 0 else 0.0,
        "reply_rate": round((replied / sent * 100), 2) if sent > 0 else 0.0,
        "conversion_rate": round((converted / sent * 100), 2) if sent > 0 else 0.0,
        "onboard_rate": round((onboarded / sent * 100), 2) if sent > 0 else 0.0,
    }

def print_markdown_report(title: str, stats: Dict[str, Any], variants: Dict[int, Dict[str, Any]] = None):
    """Outputs a clean, user-friendly markdown report to stdout."""
    print(f"\n# {title}")
    print("\n## Campaign Overview")
    print("| Metric | Count | Rate (of Sent) |")
    print("| :--- | :---: | :---: |")
    
    sent = stats["sent"]
    rates = calculate_rates(sent, stats["opened"], stats["replied"], stats["converted"], stats["onboarded"])
    
    print(f"| Total Messages Sent | {sent} | - |")
    print(f"| Messages Opened | {stats['opened']} | {rates['open_rate']}% |")
    print(f"| Unique User Replies | {stats['replied']} | {rates['reply_rate']}% |")
    print(f"| Leads Converted (High Intent) | {stats['converted']} | {rates['conversion_rate']}% |")
    print(f"| Successfully Onboarded | {stats['onboarded']} | {rates['onboard_rate']}% |")
    
    if variants:
        print("\n## A/B Template Variant Performance")
        print("| Variant | Sent | Opened | Replied | Converted | Onboarded | Open % | Reply % | Conversion % | Onboard % |")
        print("| :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |")
        for v_idx, v_stats in sorted(variants.items()):
            v_rates = calculate_rates(
                v_stats["sent"], v_stats["opened"], v_stats["replied"], v_stats["converted"], v_stats["onboarded"]
            )
            print(f"| {v_idx} | {v_stats['sent']} | {v_stats['opened']} | {v_stats['replied']} | {v_stats['converted']} | {v_stats['onboarded']} | "
                  f"{v_rates['open_rate']}% | {v_rates['reply_rate']}% | {v_rates['conversion_rate']}% | {v_rates['onboard_rate']}% |")
    print("\n" + "=" * 50 + "\n")

def run_simulation(targets: int, open_p: float, reply_p: float, convert_p: float, onboard_p: float, seed: int) -> Dict[str, Any]:
    """Runs a reproducible probabilistic campaign simulation."""
    random.seed(seed)
    stats = {"sent": targets, "opened": 0, "replied": 0, "converted": 0, "onboarded": 0}
    variants = {}
    
    # Assume 2 template variants distributed 50/50
    for idx in range(targets):
        v_idx = idx % 2
        if v_idx not in variants:
            variants[v_idx] = {"sent": 0, "opened": 0, "replied": 0, "converted": 0, "onboarded": 0}
            
        variants[v_idx]["sent"] += 1
        
        # Open check
        if random.random() < open_p:
            stats["opened"] += 1
            variants[v_idx]["opened"] += 1
            
            # Reply check
            if random.random() < reply_p:
                stats["replied"] += 1
                variants[v_idx]["replied"] += 1
                
                # Conversion check (e.g. high intent)
                if random.random() < convert_p:
                    stats["converted"] += 1
                    variants[v_idx]["converted"] += 1
                    
                    # Onboard check
                    if random.random() < onboard_p:
                        stats["onboarded"] += 1
                        variants[v_idx]["onboarded"] += 1
                        
    return {"overall": stats, "variants": variants}

def process_file_data(file_path: str) -> Dict[str, Any]:
    """Reads campaign metrics from a JSON or CSV file."""
    if not os.path.exists(file_path):
        print(f"Error: File not found {file_path}", file=sys.stderr)
        sys.exit(1)
        
    records = []
    if file_path.endswith('.json'):
        with open(file_path, 'r', encoding='utf-8') as f:
            records = json.load(f)
    elif file_path.endswith('.csv'):
        with open(file_path, 'r', encoding='utf-8-sig') as f:
            reader = csv.DictReader(f)
            for row in reader:
                records.append(row)
    else:
        print("Error: Unsupported file format. Please provide .json or .csv", file=sys.stderr)
        sys.exit(1)
        
    stats = {"sent": 0, "opened": 0, "replied": 0, "converted": 0, "onboarded": 0}
    variants = {}
    
    for r in records:
        v_idx = int(r.get("template_variant_index", 0))
        if v_idx not in variants:
            variants[v_idx] = {"sent": 0, "opened": 0, "replied": 0, "converted": 0, "onboarded": 0}
            
        # Parse boolean or status fields
        is_sent = str(r.get("sent", "true")).lower() in ("true", "1")
        is_opened = str(r.get("opened", "false")).lower() in ("true", "1")
        is_replied = str(r.get("replied", "false")).lower() in ("true", "1")
        
        # Conversion by intent score (>= 70) or explicit converted field
        intent_score = int(r.get("intent_score", 0))
        is_converted = str(r.get("converted", "false")).lower() in ("true", "1") or intent_score >= 70
        
        # Onboarding by status == 'onboarded' or explicit onboarded field
        status = str(r.get("status", "")).lower()
        is_onboarded = str(r.get("onboarded", "false")).lower() in ("true", "1") or status == "onboarded"
        
        if is_sent:
            stats["sent"] += 1
            variants[v_idx]["sent"] += 1
            
            if is_opened or is_replied:  # If replied, must have opened
                stats["opened"] += 1
                variants[v_idx]["opened"] += 1
                
            if is_replied:
                stats["replied"] += 1
                variants[v_idx]["replied"] += 1
                
                if is_converted:
                    stats["converted"] += 1
                    variants[v_idx]["converted"] += 1
                    
                    if is_onboarded:
                        stats["onboarded"] += 1
                        variants[v_idx]["onboarded"] += 1
                        
    return {"overall": stats, "variants": variants}

def process_db_data(db_path: str, campaign_id: int) -> Dict[str, Any]:
    """Queries campaign metrics directly from the SQLite database."""
    if not os.path.exists(db_path):
        print(f"Error: Database not found at {db_path}", file=sys.stderr)
        sys.exit(1)
        
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    # 1. Fetch Campaign info
    cursor.execute("SELECT name, sent_count, failed_count, skipped_count FROM dm_campaigns WHERE id = ?", (campaign_id,))
    campaign = cursor.fetchone()
    if not campaign:
        print(f"Error: Campaign ID {campaign_id} not found in database.", file=sys.stderr)
        conn.close()
        sys.exit(1)
        
    # 2. Get overall logs
    # Telegram has no direct email-like open tracking, so we estimate open-rate
    # as: opened = replies + random extra (mock read receipts). For strict reproducible behavior,
    # we define opened = replies.
    
    cursor.execute("""
        SELECT 
            template_variant_index,
            COUNT(*) as sent_cnt,
            SUM(CASE WHEN status = 'success' THEN 1 ELSE 0 END) as success_cnt
        FROM dm_campaign_logs
        WHERE campaign_id = ?
        GROUP BY template_variant_index
    """, (campaign_id,))
    log_rows = cursor.fetchall()
    
    variants = {}
    overall_sent = 0
    
    for row in log_rows:
        v_idx = row["template_variant_index"]
        variants[v_idx] = {
            "sent": row["success_cnt"],
            "opened": 0, # To be populated by replies
            "replied": 0,
            "converted": 0,
            "onboarded": 0
        }
        overall_sent += row["success_cnt"]
        
    # 3. Get replies
    cursor.execute("""
        SELECT l.template_variant_index, COUNT(DISTINCT r.sender_user_id) as reply_cnt
        FROM dm_replies r
        JOIN dm_campaign_logs l ON r.sender_user_id = l.target_user_id
        WHERE l.campaign_id = ? AND l.status = 'success'
        GROUP BY l.template_variant_index
    """, (campaign_id,))
    reply_rows = cursor.fetchall()
    for row in reply_rows:
        v_idx = row["template_variant_index"]
        if v_idx in variants:
            variants[v_idx]["replied"] = row["reply_cnt"]
            # Assume opened is at least equal to replies
            variants[v_idx]["opened"] = row["reply_cnt"]
            
    # 4. Get conversions and onboarding from ai_followup_chats
    cursor.execute("""
        SELECT 
            l.template_variant_index,
            SUM(CASE WHEN c.intent_score >= 70 THEN 1 ELSE 0 END) as convert_cnt,
            SUM(CASE WHEN c.status = 'onboarded' THEN 1 ELSE 0 END) as onboard_cnt
        FROM ai_followup_chats c
        JOIN dm_campaign_logs l ON c.user_id = l.target_user_id AND c.account_id = l.account_id
        WHERE l.campaign_id = ? AND l.status = 'success'
        GROUP BY l.template_variant_index
    """, (campaign_id,))
    followup_rows = cursor.fetchall()
    for row in followup_rows:
        v_idx = row["template_variant_index"]
        if v_idx in variants:
            variants[v_idx]["converted"] = row["convert_cnt"]
            variants[v_idx]["onboarded"] = row["onboard_cnt"]
            
    conn.close()
    
    # Aggregate overall stats
    overall = {"sent": overall_sent, "opened": 0, "replied": 0, "converted": 0, "onboarded": 0}
    for v_stats in variants.values():
        overall["opened"] += v_stats["opened"]
        overall["replied"] += v_stats["replied"]
        overall["converted"] += v_stats["converted"]
        overall["onboarded"] += v_stats["onboarded"]
        
    return {"overall": overall, "variants": variants, "campaign_name": campaign["name"]}

def main():
    parser = argparse.ArgumentParser(description="Campaign Metrics Backtester CLI tool")
    subparsers = parser.add_subparsers(dest="mode", required=True, help="Backtesting execution mode")
    
    # DB parser
    db_parser = subparsers.add_parser("db", help="Run backtest against SQLite database")
    db_parser.add_argument("--db-path", default="tg_scheduler.db", help="Path to SQLite database file")
    db_parser.add_argument("--campaign-id", type=int, required=True, help="Database Campaign ID to analyze")
    
    # Simulation parser
    sim_parser = subparsers.add_parser("sim", help="Run a probabilistic campaign simulation")
    sim_parser.add_argument("--targets", type=int, default=1000, help="Number of simulated targets")
    sim_parser.add_argument("--open-rate", type=float, default=0.60, help="Probability of opening message (0.0 - 1.0)")
    sim_parser.add_argument("--reply-rate", type=float, default=0.30, help="Probability of replying if opened (0.0 - 1.0)")
    sim_parser.add_argument("--convert-rate", type=float, default=0.15, help="Probability of converting if replied (0.0 - 1.0)")
    sim_parser.add_argument("--onboard-rate", type=float, default=0.05, help="Probability of onboarding if converted (0.0 - 1.0)")
    sim_parser.add_argument("--seed", type=int, default=42, help="Random seed for reproducibility")
    
    # File parser
    file_parser = subparsers.add_parser("file", help="Run backtest using custom JSON or CSV log file")
    file_parser.add_argument("--input", "-i", required=True, help="Path to input campaign logs (.json or .csv)")
    
    args = parser.parse_args()
    
    if args.mode == "db":
        res = process_db_data(args.db_path, args.campaign_id)
        title = f"Campaign Backtest Report: {res.get('campaign_name', 'Unknown')}"
        print_markdown_report(title, res["overall"], res["variants"])
        
    elif args.mode == "sim":
        res = run_simulation(args.targets, args.open_rate, args.reply_rate, args.convert_rate, args.onboard_rate, args.seed)
        title = f"Reproducible Simulation Report (Targets={args.targets}, Seed={args.seed})"
        print_markdown_report(title, res["overall"], res["variants"])
        
    elif args.mode == "file":
        res = process_file_data(args.input)
        title = f"Campaign File Analytics Report: {os.path.basename(args.input)}"
        print_markdown_report(title, res["overall"], res["variants"])

if __name__ == "__main__":
    main()
