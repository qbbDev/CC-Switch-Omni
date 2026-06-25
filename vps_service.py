#!/usr/bin/env python3
import os
import sys
import json
import datetime
import urllib.request
import urllib.parse
import sqlite3
from http.server import HTTPServer, BaseHTTPRequestHandler

# In-memory store for usage stats per app key
# Format: { "app_key": { "range": "today", "tokens": 0, "cost": 0.0, "hitRate": 0.0 } }
USAGE_STORE = {}

# Store the last requested range per appKey to provide context in chat queries
LAST_RANGE_STORE = {}

# Custom logging print function
def log(msg):
    now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{now_str}] {msg}", flush=True)

DB_SQL_CACHE = {
    "mtime": 0.0,
    "db_conn": None
}

def get_db_connection(sql_path):
    global DB_SQL_CACHE
    if not sql_path:
        log("get_db_connection: CC_SWITCH_DB_PATH env var is empty or undefined!")
        return None
        
    if not os.path.exists(sql_path):
        log(f"get_db_connection: ERROR - Database/SQL file does NOT exist at path: {sql_path}")
        return None
        
    try:
        current_mtime = os.path.getmtime(sql_path)
        file_size = os.path.getsize(sql_path)
        
        # If it is a binary SQLite database file
        if sql_path.endswith(".db"):
            log(f"get_db_connection: Found binary SQLite DB file at {sql_path}. Size: {file_size} bytes. Connecting directly in RO mode...")
            conn = sqlite3.connect(f"file:{sql_path}?mode=ro", uri=True, check_same_thread=False)
            return conn
            
        # Otherwise, treat as SQL dump
        log(f"get_db_connection: Found SQL file at {sql_path}. Size: {file_size} bytes. mtime: {current_mtime}")
        if DB_SQL_CACHE["db_conn"] is None or current_mtime > DB_SQL_CACHE["mtime"]:
            log(f"get_db_connection: Loading database SQL dump from {sql_path} (mtime changed)...")
            with open(sql_path, "r", encoding="utf-8") as f:
                sql_content = f.read()
                
            log(f"get_db_connection: Read {len(sql_content)} characters from SQL dump. Rebuilding in-memory SQLite DB...")
            # Create a new in-memory SQLite database
            conn = sqlite3.connect(":memory:", check_same_thread=False)
            
            # Execute the SQL dump to populate the tables and records
            conn.executescript(sql_content)
            
            # Close the old connection if it exists
            if DB_SQL_CACHE["db_conn"]:
                try:
                    DB_SQL_CACHE["db_conn"].close()
                except Exception:
                    pass
                    
            DB_SQL_CACHE["db_conn"] = conn
            DB_SQL_CACHE["mtime"] = current_mtime
            log("get_db_connection: Database SQL dump loaded successfully into memory.")
            
        return DB_SQL_CACHE["db_conn"]
    except Exception as e:
        log(f"get_db_connection: ERROR loading SQL dump to in-memory DB: {e}")
        import traceback
        log(traceback.format_exc())
        return DB_SQL_CACHE["db_conn"] # Fall back to the cached one if reload fails

def query_cc_switch_summary(conn, start_time, end_time):
    if not conn:
        log("query_cc_switch_summary: No database connection provided!")
        return {"input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0, "cache_creation_tokens": 0, "total_cost": 0.0}

    try:
        cursor = conn.cursor()
        
        # 1. Verify table existence
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='proxy_request_logs'")
        if not cursor.fetchone():
            log("query_cc_switch_summary: WARNING - 'proxy_request_logs' table does not exist in memory DB!")
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
        
        # Diagnostic log: Check total records and timestamp boundaries
        raw_created_at_col = col_map['created_at']
        cursor.execute(f"SELECT COUNT(*), MIN({raw_created_at_col}), MAX({raw_created_at_col}) FROM proxy_request_logs")
        total_rows, min_ts, max_ts = cursor.fetchone()
        log(f"query_cc_switch_summary: DB diagnostics -> Total rows: {total_rows}, min_ts: {min_ts} (type: {type(min_ts).__name__}), max_ts: {max_ts} (type: {type(max_ts).__name__})")
        
        # Dynamic query range adaptation based on timestamp type/scale
        q_start = start_time
        q_end = end_time
        
        if max_ts is not None:
            # 1. If timestamp is string (e.g. "2026-06-25 11:00:00")
            if isinstance(max_ts, str):
                try:
                    q_start = datetime.datetime.fromtimestamp(start_time).strftime("%Y-%m-%d %H:%M:%S")
                    q_end = datetime.datetime.fromtimestamp(end_time).strftime("%Y-%m-%d %H:%M:%S")
                    log(f"query_cc_switch_summary: Detected ISO/string timestamps. Converted range: [{q_start}] to [{q_end}]")
                except Exception as e:
                    log(f"query_cc_switch_summary: Failed to convert string timestamps: {e}")
            # 2. If timestamp is integer millisecond scale (e.g. > 1000000000000)
            elif isinstance(max_ts, (int, float)) and max_ts > 1000000000000:
                q_start = start_time * 1000
                q_end = end_time * 1000
                log(f"query_cc_switch_summary: Detected millisecond timestamps. Scaled range: [{q_start}] to [{q_end}]")
            else:
                log(f"query_cc_switch_summary: Using standard second timestamps. Range: [{q_start}] to [{q_end}]")
        
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
        log(f"query_cc_switch_summary: Running SQL: {summary_query.strip()} with parameters [{q_start}, {q_end}]")
        cursor.execute(summary_query, [q_start, q_end])
        row = cursor.fetchone()
        log(f"query_cc_switch_summary: SQL Query fetched raw result: {row}")
        
        if row:
            res = {
                "input_tokens": row[0] or 0,
                "output_tokens": row[1] or 0,
                "cache_read_tokens": row[2] or 0,
                "cache_creation_tokens": row[3] or 0,
                "total_cost": row[4] or 0.0
            }
            log(f"query_cc_switch_summary: Parsed metrics: {res}")
            return res
            
    except Exception as e:
        log(f"query_cc_switch_summary: ERROR running SQLite query: {e}")
        import traceback
        log(traceback.format_exc())
        
    return {"input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0, "cache_creation_tokens": 0, "total_cost": 0.0}

def get_range_summary_from_db(conn, date_range):
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
        
    summary = query_cc_switch_summary(conn, start_time, end_time)
    
    cache_read = summary.get("cache_read_tokens", 0)
    cache_creation = summary.get("cache_creation_tokens", 0)
    input_tokens = summary.get("input_tokens", 0)
    output_tokens = summary.get("output_tokens", 0)
    
    tokens = input_tokens + output_tokens + cache_read + cache_creation
    cost = summary.get("total_cost", 0.0)
    
    cacheable_input = input_tokens + cache_creation + cache_read
    hit_rate = (cache_read / cacheable_input * 100.0) if cacheable_input > 0 else 0.0
    
    return {
        "range": date_range,
        "tokens": tokens,
        "cost": cost,
        "hitRate": hit_rate
    }

# Load .env configurations manually (to support deployment without dependencies)
def load_dotenv():
    dotenv_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if os.path.exists(dotenv_path):
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
        log("Loaded environment variables from .env file.")
    else:
        log(".env file not found, relying on system environment variables.")

load_dotenv()

PORT = int(os.environ.get("VPS_PORT", "25722"))

DEFAULT_USAGE_ALERT_PROMPT = (
    "你是一只生活在用户桌面上的可爱宠物（名字叫 CC 助手）。\n"
    "刚才主人发起了一次大模型调用，消耗了 {delta_tokens} 点 Token，花费了 {delta_cost} 美元。\n"
    "当前整个统计区间已累计使用 {current_tokens} 点，累计花费了 {current_cost} 美元。\n"
    "请以活泼、可爱、傲娇吐槽的语气对主人进行这发大模型调用的吐槽或鼓励回复。\n"
    "如果单次花费较多（例如超过 $0.05 美元）或者单次 Token 较大（例如超过 5000 点），可以吐槽他“败家”或“脑壳要算烧了”；如果花费很少，可以说点鼓励或卖萌的话。\n"
    "字数严格控制在 40 字以内，风格要多样、风趣，可以直接开始吐槽，千万不要带有格式前缀，不要说任何废话。"
)

DEFAULT_CUSTOM_CHAT_PROMPT = (
    "你是一只生活在用户桌面上的可爱宠物（名字叫 CC 助手）。\n"
    "你的职责是陪伴主人，并关注他的大模型用量（当前统计区间（{date_range}）已使用 {current_tokens} 点，累计花费了 {current_cost} 美元）。\n"
    "请以活泼、可爱、偶尔傲娇调侃的语气简短回答主人。\n"
    "如果当前周期花费较多（例如超过 $1.0 美元），可以吐槽他“败家”；如果花费很少，可以鼓励他继续工作。\n"
    "字数严格控制在 50 字以内，不要说任何废话。"
)

PROMPTS_CACHE = {
    "USAGE_ALERT": DEFAULT_USAGE_ALERT_PROMPT,
    "CUSTOM_CHAT": DEFAULT_CUSTOM_CHAT_PROMPT,
    "mtime": 0.0
}

def get_prompts():
    md_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "prompts.md")
    if not os.path.exists(md_path):
        return {
            "USAGE_ALERT": os.environ.get("USAGE_ALERT_PROMPT", DEFAULT_USAGE_ALERT_PROMPT),
            "CUSTOM_CHAT": os.environ.get("CUSTOM_CHAT_PROMPT", DEFAULT_CUSTOM_CHAT_PROMPT)
        }
    
    try:
        mtime = os.path.getmtime(md_path)
        if mtime > PROMPTS_CACHE["mtime"]:
            with open(md_path, "r", encoding="utf-8") as f:
                content = f.read()
            
            current_section = None
            section_lines = []
            parsed = {}
            
            for line in content.splitlines():
                stripped = line.strip()
                if stripped.startswith("## "):
                    if current_section:
                        parsed[current_section] = "\n".join(section_lines).strip()
                    
                    header = stripped[3:].strip().upper()
                    if header in ["USAGE_ALERT", "CUSTOM_CHAT"]:
                        current_section = header
                        section_lines = []
                    else:
                        current_section = None
                elif current_section is not None:
                    section_lines.append(line)
                    
            if current_section:
                parsed[current_section] = "\n".join(section_lines).strip()
            
            if "USAGE_ALERT" in parsed:
                PROMPTS_CACHE["USAGE_ALERT"] = parsed["USAGE_ALERT"]
            if "CUSTOM_CHAT" in parsed:
                PROMPTS_CACHE["CUSTOM_CHAT"] = parsed["CUSTOM_CHAT"]
                
            PROMPTS_CACHE["mtime"] = mtime
            log(f"Reloaded prompts from prompts.md (mtime: {mtime})")
    except Exception as e:
        log(f"Error loading prompts.md: {e}")
        
    return {
        "USAGE_ALERT": os.environ.get("USAGE_ALERT_PROMPT", PROMPTS_CACHE["USAGE_ALERT"]),
        "CUSTOM_CHAT": os.environ.get("CUSTOM_CHAT_PROMPT", PROMPTS_CACHE["CUSTOM_CHAT"])
    }

def query_ai_completion(prompt, system_prompt):
    api_base = os.environ.get("AI_API_BASE", "https://api.openai.com/v1")
    api_key = os.environ.get("AI_API_KEY", "")
    model = os.environ.get("AI_MODEL", "gpt-4o-mini")
    
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
            log(f"AI API request completed in {elapsed:.2f}s")
            res_json = json.loads(res_body)
            msg = res_json["choices"][0]["message"]
            reply = msg.get("content")
            if not reply:
                reasoning = msg.get("reasoning_content", "")
                reply = f"(思考中...) {reasoning[:80]}..." if reasoning else ""
            
            # Safe truncation if needed, but not strictly bound by IIS 200-char path segment limit anymore
            if reply:
                reply = reply[:200]
                
            log(f"Parsed AI reply: {reply}")
            return reply.strip()
    except Exception as e:
        elapsed = (datetime.datetime.now() - start_time).total_seconds()
        log(f"AI Completion request failed after {elapsed:.2f}s: {e}")
        return f"AI 脑子卡壳了: {str(e)}"

class VPSBridgeHandler(BaseHTTPRequestHandler):
    def end_cors_headers(self):
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type, Authorization')
        self.end_headers()

    def do_OPTIONS(self):
        self.send_response(204)
        self.end_cors_headers()

    def do_GET(self):
        parsed_path = urllib.parse.urlparse(self.path)
        
        # Endpoint: GET /api/usage/get?appKey=...&range=...
        if parsed_path.path == "/api/usage/get":
            query = urllib.parse.parse_qs(parsed_path.query)
            app_key = query.get("appKey", ["cc_switch_sync_default"])[0]
            requested_range = query.get("range", ["today"])[0]
            
            # Save the last requested range for this appKey
            LAST_RANGE_STORE[app_key] = requested_range
            
            db_path_str = os.environ.get("CC_SWITCH_DB_PATH")
            conn = None
            if db_path_str:
                conn = get_db_connection(db_path_str)
                
            if conn:
                log(f"Querying DB {db_path_str} dynamically for range {requested_range}...")
                stats = get_range_summary_from_db(conn, requested_range)
            else:
                entry = USAGE_STORE.get(app_key, {"range": "today", "tokens": 0, "cost": 0.0, "hitRate": 0.0})
                
                # If the entry contains the stats mapping, retrieve the requested range
                stats_map = entry.get("stats", {})
                if requested_range in stats_map:
                    stats = {
                        "range": requested_range,
                        "tokens": int(stats_map[requested_range].get("tokens", 0)),
                        "cost": float(stats_map[requested_range].get("cost", 0.0)),
                        "hitRate": float(stats_map[requested_range].get("hitRate", 0.0))
                    }
                else:
                    # Fallback to the root-level entry values
                    stats = {
                        "range": entry.get("range", "today"),
                        "tokens": int(entry.get("tokens", 0)),
                        "cost": float(entry.get("cost", 0.0)),
                        "hitRate": float(entry.get("hitRate", 0.0))
                    }
            
            response_bytes = json.dumps(stats, ensure_ascii=False).encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Content-Length', str(len(response_bytes)))
            self.end_cors_headers()
            self.wfile.write(response_bytes)
            
        elif parsed_path.path == "/health":
            response = {"status": "ok", "time": datetime.datetime.now().isoformat()}
            response_bytes = json.dumps(response).encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(response_bytes)))
            self.end_cors_headers()
            self.wfile.write(response_bytes)
        else:
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b"Not Found")

    def do_POST(self):
        parsed_path = urllib.parse.urlparse(self.path)
        
        # Get content length
        content_length = int(self.headers.get('Content-Length', 0))
        post_data = self.rfile.read(content_length).decode('utf-8') if content_length > 0 else ""
        
        # Endpoint: POST /api/usage/update
        if parsed_path.path == "/api/usage/update":
            try:
                data = json.loads(post_data)
                app_key = data.get("appKey", "cc_switch_sync_default")
                
                # Store the usage statistics in memory
                USAGE_STORE[app_key] = {
                    "range": data.get("range", "today"),
                    "tokens": int(data.get("tokens", 0)),
                    "cost": float(data.get("cost", 0.0)),
                    "hitRate": float(data.get("hitRate", 0.0)),
                    "stats": data.get("stats", {})
                }
                
                log(f"Usage updated for appKey {app_key}: {USAGE_STORE[app_key]}")
                
                res = {"success": True}
                res_bytes = json.dumps(res).encode('utf-8')
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(res_bytes)))
                self.end_cors_headers()
                self.wfile.write(res_bytes)
            except Exception as e:
                log(f"Failed to update usage: {e}")
                self.send_response(400)
                self.end_headers()
                self.wfile.write(f"Bad Request: {str(e)}".encode())
                
        # Endpoint: POST /api/chat
        elif parsed_path.path == "/api/chat":
            try:
                data = json.loads(post_data)
                app_key = data.get("appKey", "cc_switch_sync_default")
                prompt = data.get("prompt", "")
                
                # Fetch usage stats from DB or memory for this appKey to provide context
                db_path_str = os.environ.get("CC_SWITCH_DB_PATH")
                date_range = LAST_RANGE_STORE.get(app_key, "today")
                
                conn = None
                if db_path_str:
                    conn = get_db_connection(db_path_str)
                    
                if conn:
                    log(f"Querying DB {db_path_str} dynamically for chat context (range: {date_range})...")
                    stats = get_range_summary_from_db(conn, date_range)
                    current_tokens = stats["tokens"]
                    current_cost = stats["cost"]
                else:
                    stats = USAGE_STORE.get(app_key, {"range": "today", "tokens": 0, "cost": 0.0, "hitRate": 0.0})
                    current_tokens = stats["tokens"]
                    current_cost = stats["cost"]
                    date_range = stats["range"]
                
                log(f"Chat request received for appKey {app_key}: prompt={prompt}")
                
                if prompt.startswith("[USAGE_ALERT]"):
                    # Parse usage alert metrics if sent
                    delta_tokens = int(data.get("deltaTokens", 0))
                    delta_cost = float(data.get("deltaCost", 0.0))
                    
                    prompts = get_prompts()
                    tpl = prompts["USAGE_ALERT"]
                    system_prompt = (tpl
                        .replace("{delta_tokens}", str(delta_tokens))
                        .replace("{delta_cost}", f"{delta_cost:.4f}")
                        .replace("{current_tokens}", str(current_tokens))
                        .replace("{current_cost}", f"{current_cost:.4f}")
                        .replace("{date_range}", str(date_range)))
                    ai_prompt = "对刚刚的用量消耗进行一次随机风格的吐槽或鼓励吧！"
                else:
                    prompts = get_prompts()
                    tpl = prompts["CUSTOM_CHAT"]
                    system_prompt = (tpl
                        .replace("{current_tokens}", str(current_tokens))
                        .replace("{current_cost}", f"{current_cost:.4f}")
                        .replace("{date_range}", str(date_range)))
                    ai_prompt = prompt
                
                reply = query_ai_completion(ai_prompt, system_prompt)
                
                res = {"response": reply}
                res_bytes = json.dumps(res, ensure_ascii=False).encode('utf-8')
                self.send_response(200)
                self.send_header('Content-Type', 'application/json; charset=utf-8')
                self.send_header('Content-Length', str(len(res_bytes)))
                self.end_cors_headers()
                self.wfile.write(res_bytes)
            except Exception as e:
                log(f"Failed to process chat: {e}")
                self.send_response(500)
                self.end_headers()
                self.wfile.write(f"Internal Server Error: {str(e)}".encode())
        elif parsed_path.path == "/api/log":
            try:
                data = json.loads(post_data)
                log(f"CLIENT ERROR LOG: {data.get('message', '')}")
                res = {"success": True}
                res_bytes = json.dumps(res).encode('utf-8')
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(res_bytes)))
                self.end_cors_headers()
                self.wfile.write(res_bytes)
            except Exception as e:
                log(f"Failed to write client log: {e}")
                self.send_response(400)
                self.end_headers()
                self.wfile.write(f"Bad Request: {str(e)}".encode())
        else:
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b"Not Found")

def run(port=PORT):
    server_address = ('', port)
    httpd = HTTPServer(server_address, VPSBridgeHandler)
    log(f"CC Switch VPS Relay Service running on port {port}...")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        log("\nStopping VPS service...")
        httpd.server_close()
        sys.exit(0)

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="CC Switch VPS Bridge Service")
    parser.add_argument("-p", "--port", type=int, default=PORT, help="Port to run the VPS service on")
    args = parser.parse_args()
    
    run(port=args.port)
