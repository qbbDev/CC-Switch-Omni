#!/usr/bin/env python3
import os
import sys
import json
import sqlite3
import datetime
from pathlib import Path
from http.server import HTTPServer, BaseHTTPRequestHandler
import urllib.parse

# Custom print to auto-inject datetime timestamps in logs
def print(*args, **kwargs):
    import builtins
    import datetime
    now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    msg = " ".join(str(arg) for arg in args)
    builtins.print(f"[{now_str}] {msg}", **kwargs)

# Load .env file configurations
def load_dotenv():
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
                    # Remove surrounding quotes
                    if (val.startswith('"') and val.endsWith('"')) or (val.startswith("'") and val.endsWith("'")):
                        val = val[1:-1]
                    os.environ[key] = val
                    print(f"Loaded config from .env: {key}={val if 'KEY' not in key else '********'}")

load_dotenv()

# Port for the aggregator agent (default is 25722)
PORT = 25722

# Find the default cc-switch SQLite database path
def get_db_path():
    env_path = os.environ.get("CC_SWITCH_DB")
    if env_path:
        return Path(env_path)
    home = Path.home()
    return home / ".cc-switch" / "cc-switch.db"


def query_cc_switch_data(db_path, start_time=None, end_time=None, app_type=None, provider_name=None, model=None):
    if not db_path.exists():
        return {
            "error": f"Database not found at {db_path}",
            "summary": {"total_requests": 0, "total_tokens": 0, "total_cost": 0.0, "success_rate": 100.0},
            "trends": [],
            "recent_logs": [],
            "active_configs": [],
            "model_stats": [],
            "provider_stats": [],
            "providers_list": [],
            "models_list": []
        }
    
    # Defaults if not provided
    now = datetime.datetime.now()
    if end_time is None:
        end_time = int(now.timestamp())
    if start_time is None:
        today_midnight = datetime.datetime(now.year, now.month, now.day)
        start_time = int((today_midnight - datetime.timedelta(days=30)).timestamp())

    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        cursor = conn.cursor()
        
        # 1. Verify table existence
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='proxy_request_logs'")
        if not cursor.fetchone():
            return {
                "summary": {"total_requests": 0, "total_tokens": 0, "total_cost": 0.0, "success_rate": 100.0},
                "trends": [],
                "recent_logs": [],
                "active_configs": [],
                "model_stats": [],
                "provider_stats": [],
                "providers_list": [],
                "models_list": [],
                "info": "Table 'proxy_request_logs' does not exist yet."
            }
        
        # 2. Inspect columns of proxy_request_logs
        cursor.execute("PRAGMA table_info(proxy_request_logs)")
        columns = [row[1] for row in cursor.fetchall()]
        
        col_map = {
            "id": "id" if "id" in columns else (columns[0] if columns else "rowid"),
            "provider_id": "provider_id" if "provider_id" in columns else None,
            "app_type": "app_type" if "app_type" in columns else None,
            "request_model": "request_model" if "request_model" in columns else ("model" if "model" in columns else None),
            "input_tokens": "input_tokens" if "input_tokens" in columns else None,
            "output_tokens": "output_tokens" if "output_tokens" in columns else None,
            "cache_read_tokens": "cache_read_tokens" if "cache_read_tokens" in columns else None,
            "cache_creation_tokens": "cache_creation_tokens" if "cache_creation_tokens" in columns else None,
            "total_cost_usd": "total_cost_usd" if "total_cost_usd" in columns else ("cost" if "cost" in columns else None),
            "duration_ms": "duration_ms" if "duration_ms" in columns else ("duration" if "duration" in columns else None),
            "first_token_ms": "first_token_ms" if "first_token_ms" in columns else None,
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
        request_model_col = f"l.{col_map['request_model']}" if col_map['request_model'] else "NULL"
        app_type_col = f"l.{col_map['app_type']}" if col_map['app_type'] else "NULL"
        l_provider_id_col = f"l.{col_map['provider_id']}" if col_map['provider_id'] else "NULL"
        
        # SQL expression for cache-normalized fresh input tokens (仿 cc-switch)
        if col_map['input_tokens'] and col_map['cache_read_tokens']:
            fresh_input_col = f"(CASE WHEN {app_type_col} IN ('codex', 'gemini') AND {input_col} >= {cache_read_col} THEN ({input_col} - {cache_read_col}) ELSE {input_col} END)"
        else:
            fresh_input_col = input_col
            
        # Build dynamic where clause
        clauses = [f"{created_at_col} >= ?", f"{created_at_col} <= ?"]
        params = [start_time, end_time]
        
        if app_type and app_type != 'all':
            clauses.append(f"{app_type_col} = ?")
            params.append(app_type)
            
        if provider_name and provider_name != 'all':
            clauses.append(f"(p.name = ? OR {l_provider_id_col} = ?)")
            params.extend([provider_name, provider_name])
            
        if model and model != 'all':
            clauses.append(f"{request_model_col} = ?")
            params.append(model)
            
        where_clause = "WHERE " + " AND ".join(clauses)

        # 3. Query Summary Metrics
        summary_query = f"""
            SELECT 
                COUNT(*),
                SUM(COALESCE({fresh_input_col}, 0)),
                SUM(COALESCE({output_col}, 0)),
                SUM(COALESCE({cache_read_col}, 0)),
                SUM(COALESCE({cache_creation_col}, 0)),
                SUM(COALESCE({cost_col}, 0.0)),
                SUM(CASE WHEN {status_col} >= 200 AND {status_col} < 300 THEN 1 ELSE 0 END)
            FROM proxy_request_logs l
            LEFT JOIN providers p ON {l_provider_id_col} = p.id
            {where_clause}
        """
        cursor.execute(summary_query, params)
        row = cursor.fetchone()
        
        requests = row[0] or 0
        input_tokens = row[1] or 0
        output_tokens = row[2] or 0
        cache_read = row[3] or 0
        cache_creation = row[4] or 0
        cost = row[5] or 0.0
        success = row[6] or 0
        success_rate = (success / requests * 100) if requests > 0 else 100.0
        
        display_tokens = input_tokens + output_tokens
        real_tokens = input_tokens + output_tokens + cache_read + cache_creation
        
        summary = {
            "total_requests": requests,
            "total_tokens": display_tokens,
            "real_tokens": real_tokens,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cache_read_tokens": cache_read,
            "cache_creation_tokens": cache_creation,
            "total_cost": round(cost, 6),
            "success_rate": round(success_rate, 2)
        }

        # 4. Query Trends
        duration = end_time - start_time
        group_by_hour = duration <= 86400
        trends = []
        
        if col_map['created_at']:
            if group_by_hour:
                date_format_clause = f"""
                    CASE 
                        WHEN TYPEOF({created_at_col}) = 'integer' AND {created_at_col} > 1000000000000 THEN 
                            strftime('%m-%d %H:00', datetime({created_at_col}/1000, 'unixepoch', 'localtime'))
                        WHEN TYPEOF({created_at_col}) = 'integer' THEN 
                            strftime('%m-%d %H:00', datetime({created_at_col}, 'unixepoch', 'localtime'))
                        ELSE 
                            strftime('%m-%d %H:00', datetime({created_at_col}))
                    END
                """
            else:
                date_format_clause = f"""
                    CASE 
                        WHEN TYPEOF({created_at_col}) = 'integer' AND {created_at_col} > 1000000000000 THEN 
                            strftime('%Y-%m-%d', datetime({created_at_col}/1000, 'unixepoch', 'localtime'))
                        WHEN TYPEOF({created_at_col}) = 'integer' THEN 
                            strftime('%Y-%m-%d', datetime({created_at_col}, 'unixepoch', 'localtime'))
                        ELSE 
                            SUBSTR(date({created_at_col}), 1, 10)
                    END
                """

            trends_query = f"""
                SELECT 
                    {date_format_clause} as time_bucket,
                    COUNT(*) as requests,
                    SUM(COALESCE({fresh_input_col}, 0)) as input_tokens,
                    SUM(COALESCE({output_col}, 0)) as output_tokens,
                    SUM(COALESCE({cache_read_col}, 0)) as cache_read_tokens,
                    SUM(COALESCE({cache_creation_col}, 0)) as cache_creation_tokens,
                    SUM(COALESCE({cost_col}, 0.0)) as cost
                FROM proxy_request_logs l
                LEFT JOIN providers p ON {l_provider_id_col} = p.id
                {where_clause}
                GROUP BY time_bucket
                ORDER BY time_bucket ASC
            """
            cursor.execute(trends_query, params)
            for r in cursor.fetchall():
                if r[0]:
                    trends.append({
                        "date": r[0],
                        "requests": r[1] or 0,
                        "input_tokens": r[2] or 0,
                        "output_tokens": r[3] or 0,
                        "cache_read_tokens": r[4] or 0,
                        "cache_creation_tokens": r[5] or 0,
                        "cost": round(r[6] or 0.0, 6)
                    })

        # 5. Query Model Stats
        model_stats = []
        if col_map['request_model']:
            model_query = f"""
                SELECT 
                    {request_model_col} as model_name,
                    {app_type_col} as app_type,
                    COUNT(*),
                    SUM(COALESCE({fresh_input_col}, 0)),
                    SUM(COALESCE({output_col}, 0)),
                    SUM(COALESCE({cache_read_col}, 0)),
                    SUM(COALESCE({cache_creation_col}, 0)),
                    SUM(COALESCE({cost_col}, 0.0))
                FROM proxy_request_logs l
                LEFT JOIN providers p ON {l_provider_id_col} = p.id
                {where_clause}
                GROUP BY model_name, {app_type_col}
                ORDER BY COUNT(*) DESC
            """
            cursor.execute(model_query, params)
            for r in cursor.fetchall():
                model_stats.append({
                    "model_name": r[0] or "Unknown",
                    "app_type": r[1] or "Unknown",
                    "requests": r[2] or 0,
                    "input_tokens": r[3] or 0,
                    "output_tokens": r[4] or 0,
                    "cache_read": r[5] or 0,
                    "cache_creation": r[6] or 0,
                    "total_tokens": (r[3] or 0) + (r[4] or 0),
                    "cost": round(r[7] or 0.0, 6)
                })

        conn.close()
        
        return {
            "summary": summary,
            "trends": trends,
            "model_stats": model_stats
        }
        
    except Exception as e:
        return {
            "error": f"Failed to query database: {str(e)}",
            "summary": {"total_requests": 0, "total_tokens": 0, "total_cost": 0.0, "success_rate": 100.0},
            "trends": [],
            "model_stats": []
        }

class AggregatorAgentHandler(BaseHTTPRequestHandler):
    def end_headers(self):
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        super().end_headers()

    def do_OPTIONS(self):
        self.send_response(204)
        self.end_headers()

    def do_GET(self):
        parsed_path = urllib.parse.urlparse(self.path)
        query_params = urllib.parse.parse_qs(parsed_path.query)
        
        # Check start_time and end_time overrides
        start_time_param = query_params.get("start_time", [None])[0]
        end_time_param = query_params.get("end_time", [None])[0]
        
        start_time = None
        end_time = None
        if start_time_param and end_time_param:
            try:
                start_time = int(start_time_param)
                end_time = int(end_time_param)
            except ValueError:
                pass

        # Fallback to date_range if timestamps are not provided
        if start_time is None or end_time is None:
            date_range = query_params.get("range", ["30d"])[0]
            now = datetime.datetime.now()
            today_midnight = datetime.datetime(now.year, now.month, now.day)
            
            end_time = int(now.timestamp())
            if date_range == "today":
                start_time = int(today_midnight.timestamp())
            elif date_range == "1d":
                start_time = end_time - 24 * 3600
            elif date_range in ("7d", "7days"):
                start_time = int((today_midnight - datetime.timedelta(days=6)).timestamp())
            elif date_range == "14d":
                start_time = int((today_midnight - datetime.timedelta(days=13)).timestamp())
            elif date_range in ("30d", "30days"):
                start_time = int((today_midnight - datetime.timedelta(days=29)).timestamp())
            else: # "all" or other fallback
                start_time = 0
        
        if parsed_path.path == "/api/usage":
            app_type = query_params.get("app_type", [None])[0]
            provider_name = query_params.get("provider_name", [None])[0]
            model = query_params.get("model", [None])[0]
            
            db_path = get_db_path()
            data = query_cc_switch_data(
                db_path, 
                start_time=start_time, 
                end_time=end_time,
                app_type=app_type,
                provider_name=provider_name,
                model=model
            )
            
            response_bytes = json.dumps(data, ensure_ascii=False).encode('utf-8')
            
            self.send_response(200)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Content-Length', str(len(response_bytes)))
            self.end_headers()
            self.wfile.write(response_bytes)
        elif parsed_path.path == "/health":
            response = {"status": "ok", "db_path": str(get_db_path()), "db_exists": get_db_path().exists()}
            response_bytes = json.dumps(response).encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(response_bytes)))
            self.end_headers()
            self.wfile.write(response_bytes)
        else:
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b"Not Found")

def get_openpets_config():
    try:
        path = Path.home() / "Library" / "Application Support" / "@open-pets" / "desktop" / "openpets-plugin-state.json"
        if path.exists():
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            plugin_data = data.get("plugins", {}).get("openpets.cc-switch", {})
            return plugin_data.get("config", {})
    except Exception as e:
        print(f"Error reading OpenPets config: {e}")
    return {}

def send_kv_update(sync_app_key, date_range, tokens, cost, hit_rate):
    import urllib.request
    
    # Format floating point stats with hyphens instead of dots to prevent IIS 404 error
    cost_str = f"{cost:.4f}".replace(".", "-")
    hit_rate_str = f"{hit_rate:.1f}".replace(".", "-")
    
    val_str = f"{date_range}_{tokens}_{cost_str}_{hit_rate_str}"
    
    url = f"https://keyvalue.immanuel.co/api/KeyVal/UpdateValue/{sync_app_key}/usage/{val_str}"
    req = urllib.request.Request(url, data=b"", method="POST")
    req.add_header("Content-Length", "0")
    kv_start = datetime.datetime.now()
    try:
        with urllib.request.urlopen(req, timeout=5) as response:
            res_text = response.read().decode().strip()
            kv_elapsed = (datetime.datetime.now() - kv_start).total_seconds()
            print(f"KV update successful for appKey {sync_app_key}: {val_str} (response: {res_text}, took {kv_elapsed:.2f}s)")
            return True
    except Exception as e:
        kv_elapsed = (datetime.datetime.now() - kv_start).total_seconds()
        print(f"KV update failed for appKey {sync_app_key} after {kv_elapsed:.2f}s: {e}")
        return False

def monitor_database_loop():
    import time
    
    db_path = get_db_path()
    print("CC Switch Aggregator database monitoring thread started.")
    
    last_kv_tokens = -1
    last_kv_cost = -1.0
    last_push_time = 0
    
    while True:
        try:
            time.sleep(5)
            
            config = get_openpets_config()
            date_range = config.get("tokenRange", "today")
            
            now = datetime.datetime.now()
            today_midnight = datetime.datetime(now.year, now.month, now.day)
            
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
            
            data = query_cc_switch_data(db_path, start_time=start_time, end_time=end_time)
            summary = data.get("summary", {})
            
            cache_read = summary.get("cache_read_tokens", 0)
            cache_creation = summary.get("cache_creation_tokens", 0)
            input_tokens = summary.get("input_tokens", 0)
            output_tokens = summary.get("output_tokens", 0)
            
            current_tokens = input_tokens + output_tokens + cache_read + cache_creation
            current_cost = summary.get("total_cost", 0.0)
            
            cacheable_input = input_tokens + cache_creation + cache_read
            hit_rate = (cache_read / cacheable_input * 100.0) if cacheable_input > 0 else 0.0
            
            current_time = time.time()
            value_changed = (current_tokens != last_kv_tokens) or (abs(current_cost - last_kv_cost) > 0.00001)
            heartbeat_elapsed = (current_time - last_push_time) >= 60.0
            
            if value_changed or heartbeat_elapsed:
                sync_app_key = config.get("syncAppKey", "cc_switch_sync_default")
                
                success = send_kv_update(
                    sync_app_key, 
                    date_range, 
                    current_tokens, 
                    current_cost,
                    hit_rate
                )
                if success:
                    last_kv_tokens = current_tokens
                    last_kv_cost = current_cost
                    last_push_time = current_time
                    
        except Exception as e:
            print(f"Error in database monitor loop: {e}")

import base64

def to_b64url(s):
    data = s.encode('utf-8')
    encoded = base64.urlsafe_b64encode(data).decode('utf-8')
    return encoded.rstrip('=')

def from_b64url(s):
    try:
        rem = len(s) % 4
        if rem > 0:
            s += '=' * (4 - rem)
        data = base64.urlsafe_b64decode(s.encode('utf-8'))
        return data.decode('utf-8')
    except Exception as e:
        print(f"Base64URL decode failed: {e}")
        return s

def parse_kv_raw_val(raw):
    if not raw:
        return ""
    clean = raw.strip()
    clean = urllib.parse.unquote(clean)
    if clean.startswith('"') and clean.endswith('"'):
        try:
            clean = json.loads(clean)
        except Exception:
            clean = clean[1:-1]
    return clean

def query_ai_completion(prompt, system_prompt):
    api_base = os.environ.get("AI_API_BASE", "https://api.openai.com/v1")
    api_key = os.environ.get("AI_API_KEY", "")
    model = os.environ.get("AI_MODEL", "gpt-4o-mini")
    
    import urllib.request
    url = f"{api_base.rstrip('/')}/chat/completions"
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
        
    data = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt}
        ],
        "temperature": 0.7,
        "max_tokens": 20000
    }
    
    req_body = json.dumps(data).encode("utf-8")
    req = urllib.request.Request(url, data=req_body, headers=headers, method="POST")
    
    start_time = datetime.datetime.now()
    try:
        with urllib.request.urlopen(req, timeout=60) as response:
            res_body = response.read().decode("utf-8")
            elapsed = (datetime.datetime.now() - start_time).total_seconds()
            print(f"AI API request completed in {elapsed:.2f}s")
            print(f"Raw AI response body: {res_body}")
            res_json = json.loads(res_body)
            msg = res_json["choices"][0]["message"]
            reply = msg.get("content")
            if not reply:
                reasoning = msg.get("reasoning_content", "")
                reply = f"(思考中...) {reasoning[:80]}..." if reasoning else ""
            
            # Truncate to 150 chars max to stay safe with KV URL path length
            if reply:
                reply = reply[:150]
                
            print(f"Parsed AI reply: {reply}")
            return reply.strip()
    except Exception as e:
        elapsed = (datetime.datetime.now() - start_time).total_seconds()
        print(f"AI Completion request failed after {elapsed:.2f}s: {e}")
        return f"AI 脑子卡壳了: {str(e)}"

def ai_bridge_loop():
    import time
    import urllib.request
    
    print("CC Switch AI Bridge thread started.")
    last_processed_id = None
    
    while True:
        try:
            time.sleep(0.5)
            
            config = get_openpets_config()
            sync_app_key = config.get("syncAppKey", "cc_switch_sync_default")
            
            url = f"https://keyvalue.immanuel.co/api/KeyVal/GetValue/{sync_app_key}/chat_request"
            try:
                with urllib.request.urlopen(url, timeout=5) as response:
                    raw_val = response.read().decode().strip()
            except Exception:
                continue
                
            clean_val = parse_kv_raw_val(raw_val)
            if not clean_val:
                continue
                
            try:
                # Robustly decode request from Base64URL, hex, or raw JSON
                decoded_val = None
                
                # 1. Try Base64URL
                try:
                    decoded_val = from_b64url(clean_val)
                except Exception:
                    decoded_val = None
                
                # 2. Try Hex
                if decoded_val is None:
                    try:
                        decoded_val = bytes.fromhex(clean_val).decode('utf-8')
                    except Exception:
                        decoded_val = None
                
                # 3. Fallback to raw string
                if decoded_val is None:
                    decoded_val = clean_val
                
                # Now parse the decoded request (either pipe-delimited "id|prompt" or JSON)
                req_id = None
                prompt = None
                
                if "|" in decoded_val:
                    parts = decoded_val.split("|", 1)
                    if len(parts) == 2 and len(parts[0]) <= 10:  # alphanumeric random short id
                        req_id = parts[0]
                        prompt = parts[1]
                
                if not req_id or not prompt:
                    try:
                        req_data = json.loads(decoded_val)
                        req_id = req_data.get("id")
                        prompt = req_data.get("prompt")
                    except Exception:
                        pass
            except Exception:
                continue
                
            if not req_id or not prompt:
                continue
                
            if req_id != last_processed_id:
                print(f"New chat request received (id: {req_id}): {prompt}")
                
                db_path = get_db_path()
                date_range = config.get("tokenRange", "today")
                
                now = datetime.datetime.now()
                today_midnight = datetime.datetime(now.year, now.month, now.day)
                
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
                
                data = query_cc_switch_data(db_path, start_time=start_time, end_time=end_time)
                summary = data.get("summary", {})
                
                cache_read = summary.get("cache_read_tokens", 0)
                cache_creation = summary.get("cache_creation_tokens", 0)
                input_tokens = summary.get("input_tokens", 0)
                output_tokens = summary.get("output_tokens", 0)
                current_tokens = input_tokens + output_tokens + cache_read + cache_creation
                current_cost = summary.get("total_cost", 0.0)
                
                if prompt.startswith("[USAGE_ALERT]"):
                    parts = prompt.split(",")
                    delta_tokens = 0
                    delta_cost = 0.0
                    for p in parts:
                        p = p.strip()
                        if p.startswith("[USAGE_ALERT] delta_tokens:"):
                            try: delta_tokens = int(p.split(":")[1].strip())
                            except: pass
                        elif p.startswith("delta_cost:"):
                            try: delta_cost = float(p.split(":")[1].strip())
                            except: pass
                            
                    tpl = config.get("usageAlertPrompt", "")
                    if not tpl:
                        tpl = (
                            "你是一只生活在用户桌面上的可爱宠物（名字叫 CC 助手）。\n"
                            "刚才主人发起了一次大模型调用，消耗了 {delta_tokens} 点 Token，花费了 {delta_cost} 美元。\n"
                            "当前整个统计区间已累计使用 {current_tokens} 点，累计花费了 {current_cost} 美元。\n"
                            "请以活泼、可爱、傲娇吐槽的语气对主人进行这发大模型调用的吐槽或鼓励回复。\n"
                            "如果单次花费较多（例如超过 $0.05 美元）或者单次 Token 较大（例如超过 5000 点），可以吐槽他“败家”或“脑壳要算烧了”；如果花费很少，可以说点鼓励或卖萌的话。\n"
                            "字数严格控制在 40 字以内，风格要多样、风趣，可以直接开始吐槽，千万不要带有格式前缀，不要说任何废话。"
                        )
                    
                    system_prompt = (tpl
                        .replace("{delta_tokens}", str(delta_tokens))
                        .replace("{delta_cost}", f"{delta_cost:.4f}")
                        .replace("{current_tokens}", str(current_tokens))
                        .replace("{current_cost}", f"{current_cost:.4f}")
                        .replace("{date_range}", str(date_range)))
                    ai_prompt = "对刚刚的用量消耗进行一次随机风格的吐槽或鼓励吧！"
                else:
                    tpl = config.get("customChatPrompt", "")
                    if not tpl:
                        tpl = (
                            "你是一只生活在用户桌面上的可爱宠物（名字叫 CC 助手）。\n"
                            "你的职责是陪伴主人，并关注他的大模型用量（当前统计区间（{date_range}）已使用 {current_tokens} 点，累计花费了 {current_cost} 美元）。\n"
                            "请以活泼、可爱、偶尔傲娇调侃的语气简短回答主人。\n"
                            "如果当前周期花费较多（例如超过 $1.0 美元），可以吐槽他“败家”；如果花费很少，可以鼓励他继续工作。\n"
                            "字数严格控制在 50 字以内，不要说任何废话。"
                        )
                    
                    system_prompt = (tpl
                        .replace("{current_tokens}", str(current_tokens))
                        .replace("{current_cost}", f"{current_cost:.4f}")
                        .replace("{date_range}", str(date_range)))
                    ai_prompt = prompt
                
                reply = query_ai_completion(ai_prompt, system_prompt)
                print(f"AI bridge loop reply: {reply}")
                
                # Use a compact pipe-delimited format to save space
                res_payload = f"{req_id}|{reply}"
                b64_res = to_b64url(res_payload)
                
                # If it exceeds the 200-character IIS segment limit, truncate the reply until it fits
                if len(b64_res) > 200:
                    print(f"Response payload base64url length ({len(b64_res)}) exceeds 200. Truncating...")
                    while len(b64_res) > 200 and len(reply) > 0:
                        reply = reply[:-1]
                        res_payload = f"{req_id}|{reply}"
                        b64_res = to_b64url(res_payload)
                    print(f"Truncated reply to: {reply} (base64url length: {len(b64_res)})")
                
                print(f"Res payload to update: {res_payload}")
                update_url = f"https://keyvalue.immanuel.co/api/KeyVal/UpdateValue/{sync_app_key}/chat_response/{b64_res}"
                
                req = urllib.request.Request(update_url, data=b"", method="POST")
                req.add_header("Content-Length", "0")
                kv_start = datetime.datetime.now()
                try:
                    with urllib.request.urlopen(req, timeout=5) as res:
                        kv_elapsed = (datetime.datetime.now() - kv_start).total_seconds()
                        print(f"Chat response updated successfully for id {req_id} (took {kv_elapsed:.2f}s)")
                except Exception as e:
                    kv_elapsed = (datetime.datetime.now() - kv_start).total_seconds()
                    print(f"Failed to update chat response after {kv_elapsed:.2f}s: {e}")
                    
                last_processed_id = req_id
                
        except Exception as e:
            print(f"Error in AI bridge loop: {e}")

def run(server_class=HTTPServer, handler_class=AggregatorAgentHandler, port=PORT):
    import threading
    
    # Start database monitoring thread
    t = threading.Thread(target=monitor_database_loop, daemon=True)
    t.start()
    
    # Start AI bridge thread
    t_ai = threading.Thread(target=ai_bridge_loop, daemon=True)
    t_ai.start()
    
    # Start HTTP server
    server_address = ('', port)
    httpd = server_class(server_address, handler_class)
    print(f"CC Switch Aggregator HTTP Agent running on port {port}...")
    print(f"Reading database from: {get_path_str() if 'get_path_str' in globals() else get_db_path()}")
    print("Press Ctrl+C to stop.")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping agent...")
        httpd.server_close()
        sys.exit(0)

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="CC Switch Aggregator Agent")
    parser.add_argument("-p", "--port", type=int, default=PORT, help="Port to run the HTTP agent on")
    parser.add_argument("-d", "--db", type=str, default=None, help="Path to cc-switch.db file override")
    args = parser.parse_args()
    
    if args.db:
        os.environ["CC_SWITCH_DB"] = args.db
        
    run(port=args.port)
