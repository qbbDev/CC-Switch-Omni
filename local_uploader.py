#!/usr/bin/env python3
import os
import sys
import json
import time
import sqlite3
import datetime
import urllib.request
import urllib.parse
from pathlib import Path

# Custom print to auto-inject datetime timestamps in logs
def print_log(msg):
    now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{now_str}] {msg}", flush=True)

# Dynamic environment variable loader
def load_config():
    config = {
        "vps_url": "http://localhost:25722",  # Default fallback
        "sync_app_key": "cc_switch_sync_default",
        "token_range": "today",
        "db_path": None
    }
    
    dotenv_path = Path(__file__).resolve().parent / ".env"
    if dotenv_path.exists():
        with open(dotenv_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" in line:
                    key, val = line.split("=", 1)
                    key = key.strip()
                    val = val.strip()
                    if (val.startswith('"') and val.endswith('"')) or (val.startswith("'") and val.endswith("'")):
                        val = val[1:-1]
                    os.environ[key] = val
                    
    config["vps_url"] = os.environ.get("VPS_URL", config["vps_url"])
    config["sync_app_key"] = os.environ.get("SYNC_APP_KEY", config["sync_app_key"])
    config["token_range"] = os.environ.get("TOKEN_RANGE", config["token_range"])
    
    env_db = os.environ.get("CC_SWITCH_DB")
    if env_db:
        config["db_path"] = Path(env_db)
    else:
        config["db_path"] = Path.home() / ".cc-switch" / "cc-switch.db"
        
    return config

def query_cc_switch_summary(db_path, start_time, end_time):
    if not db_path.exists():
        return {"input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0, "cache_creation_tokens": 0, "total_cost": 0.0}

    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        cursor = conn.cursor()
        
        # 1. Verify table existence
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='proxy_request_logs'")
        if not cursor.fetchone():
            conn.close()
            return {"input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0, "cache_creation_tokens": 0, "total_cost": 0.0}
        
        # 2. Inspect columns to map them correctly
        cursor.execute("PRAGMA table_info(proxy_request_logs)")
        columns = [row[1] for row in cursor.fetchall()]
        
        col_map = {
            "provider_id": "provider_id" if "provider_id" in columns else None,
            "app_type": "app_type" if "app_type" in columns else None,
            "input_tokens": "input_tokens" if "input_tokens" in columns else None,
            "output_tokens": "output_tokens" if "output_tokens" in columns else None,
            "cache_read_tokens": "cache_read_tokens" if "cache_read_tokens" in columns else None,
            "cache_creation_tokens": "cache_creation_tokens" if "cache_creation_tokens" in columns else None,
            "total_cost_usd": "total_cost_usd" if "total_cost_usd" in columns else ("cost" if "cost" in columns else None),
            "status_code": "status_code" if "status_code" in columns else ("status" if "status" in columns else None),
            "created_at": "created_at" if "created_at" in columns else ("timestamp" if "timestamp" in columns else None)
        }
        
        input_col = f"l.{col_map['input_tokens']}" if col_map['input_tokens'] else "0"
        output_col = f"l.{col_map['output_tokens']}" if col_map['output_tokens'] else "0"
        cache_read_col = f"l.{col_map['cache_read_tokens']}" if col_map['cache_read_tokens'] else "0"
        cache_creation_col = f"l.{col_map['cache_creation_tokens']}" if col_map['cache_creation_tokens'] else "0"
        cost_col = f"l.{col_map['total_cost_usd']}" if col_map['total_cost_usd'] else "0.0"
        status_col = f"l.{col_map['status_code']}" if col_map['status_code'] else "200"
        created_at_col = f"l.{col_map['created_at']}"
        app_type_col = f"l.{col_map['app_type']}" if col_map['app_type'] else "NULL"
        l_provider_id_col = f"l.{col_map['provider_id']}" if col_map['provider_id'] else "NULL"
        
        if col_map['input_tokens'] and col_map['cache_read_tokens']:
            fresh_input_col = f"(CASE WHEN {app_type_col} IN ('codex', 'gemini') AND {input_col} >= {cache_read_col} THEN ({input_col} - {cache_read_col}) ELSE {input_col} END)"
        else:
            fresh_input_col = input_col
            
        summary_query = f"""
            SELECT 
                SUM(COALESCE({fresh_input_col}, 0)),
                SUM(COALESCE({output_col}, 0)),
                SUM(COALESCE({cache_read_col}, 0)),
                SUM(COALESCE({cache_creation_col}, 0)),
                SUM(COALESCE({cost_col}, 0.0))
            FROM proxy_request_logs l
            WHERE {created_at_col} >= ? AND {created_at_col} <= ?
        """
        cursor.execute(summary_query, [start_time, end_time])
        row = cursor.fetchone()
        conn.close()
        
        if row:
            return {
                "input_tokens": row[0] or 0,
                "output_tokens": row[1] or 0,
                "cache_read_tokens": row[2] or 0,
                "cache_creation_tokens": row[3] or 0,
                "total_cost": row[4] or 0.0
            }
    except Exception as e:
        print_log(f"Database query failed: {e}")
        
    return {"input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0, "cache_creation_tokens": 0, "total_cost": 0.0}

def send_vps_update(vps_url, payload):
    url = f"{vps_url.rstrip('/')}/api/usage/update"
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    
    req_body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=req_body, headers=headers, method="POST")
    
    start_time = time.time()
    try:
        with urllib.request.urlopen(req, timeout=5) as res:
            res_body = res.read().decode().strip()
            elapsed = time.time() - start_time
            print_log(f"Uploaded usage to VPS successfully (took {elapsed:.2f}s, response: {res_body})")
            return True
    except Exception as e:
        elapsed = time.time() - start_time
        print_log(f"Failed to upload usage to VPS after {elapsed:.2f}s: {e}")
        return False

def get_range_summary(db_path, date_range, now, today_midnight):
    end_time = int(now.timestamp())
    if date_range == "today":
        start_time = int(today_midnight.timestamp())
    elif date_range == "1d":
        start_time = end_time - 24 * 3600
    elif date_range == "7d":
        start_time = int((today_midnight - datetime.timedelta(days=6)).timestamp())
    elif date_range == "14d":
        start_time = int((today_midnight - datetime.timedelta(days=13)).timestamp())
    elif date_range == "30d":
        start_time = int((today_midnight - datetime.timedelta(days=29)).timestamp())
    else:
        start_time = int(today_midnight.timestamp())
        
    summary = query_cc_switch_summary(db_path, start_time, end_time)
    
    cache_read = summary.get("cache_read_tokens", 0)
    cache_creation = summary.get("cache_creation_tokens", 0)
    input_tokens = summary.get("input_tokens", 0)
    output_tokens = summary.get("output_tokens", 0)
    
    tokens = input_tokens + output_tokens + cache_read + cache_creation
    cost = summary.get("total_cost", 0.0)
    
    cacheable_input = input_tokens + cache_creation + cache_read
    hit_rate = (cache_read / cacheable_input * 100.0) if cacheable_input > 0 else 0.0
    
    return {
        "tokens": tokens,
        "cost": cost,
        "hitRate": hit_rate
    }

def monitor_database_loop():
    print_log("CC Switch Local Uploader monitoring loop started.")
    
    last_payload_str = ""
    last_push_time = 0
    
    while True:
        try:
            time.sleep(5)
            
            # Load config dynamically so that edits to .env are picked up in real time
            cfg = load_config()
            db_path = cfg["db_path"]
            vps_url = cfg["vps_url"]
            sync_app_key = cfg["sync_app_key"]
            date_range = cfg["token_range"]
            
            now = datetime.datetime.now()
            today_midnight = datetime.datetime(now.year, now.month, now.day)
            
            # Calculate all ranges
            all_ranges = ["today", "1d", "7d", "14d", "30d"]
            stats_payload = {}
            for r in all_ranges:
                stats_payload[r] = get_range_summary(db_path, r, now, today_midnight)
                
            # Get default range from .env for backwards compatibility
            default_stats = stats_payload.get(date_range, stats_payload["today"])
            
            payload = {
                "appKey": sync_app_key,
                "range": date_range,
                "tokens": default_stats["tokens"],
                "cost": default_stats["cost"],
                "hitRate": default_stats["hitRate"],
                "stats": stats_payload
            }
            
            payload_str = json.dumps(payload, sort_keys=True)
            
            current_time = time.time()
            value_changed = (payload_str != last_payload_str)
            heartbeat_elapsed = (current_time - last_push_time) >= 60.0
            
            if value_changed or heartbeat_elapsed:
                success = send_vps_update(vps_url, payload)
                if success:
                    last_payload_str = payload_str
                    last_push_time = current_time
                    
        except Exception as e:
            print_log(f"Error in uploader monitor loop: {e}")
                    
        except Exception as e:
            print_log(f"Error in uploader monitor loop: {e}")

if __name__ == "__main__":
    monitor_database_loop()
