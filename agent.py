#!/usr/bin/env python3
import os
import sys
import json
import sqlite3
import datetime
from pathlib import Path
from http.server import HTTPServer, BaseHTTPRequestHandler
import urllib.parse

# Port for the aggregator agent (default is 35722)
PORT = 35722

# Find the default cc-switch SQLite database path
def get_db_path():
    env_path = os.environ.get("CC_SWITCH_DB")
    if env_path:
        return Path(env_path)
    home = Path.home()
    return home / ".cc-switch" / "cc-switch.db"

def get_active_providers(cursor):
    try:
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='providers'")
        if not cursor.fetchone():
            return []
        
        cursor.execute("SELECT id, app_type, name FROM providers WHERE is_current = 1")
        return cursor.fetchall()
    except Exception as e:
        print(f"Error fetching active providers: {e}")
        return []

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

        # 4. Get active configurations (always query current settings config)
        active_providers = get_active_providers(cursor)
        active_map = {p[1]: p[2] for p in active_providers}
        
        cursor.execute(f"SELECT DISTINCT {app_type_col} FROM proxy_request_logs l")
        app_types = [r[0] for r in cursor.fetchall() if r[0]]
        
        active_configs = []
        for app in app_types:
            # We want current configurations summary for this app
            app_clauses = [f"{created_at_col} >= ?", f"{created_at_col} <= ?", f"{app_type_col} = ?"]
            app_params = [start_time, end_time, app]
            app_where = "WHERE " + " AND ".join(app_clauses)
            
            sub_query = f"""
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
                {app_where}
            """
            cursor.execute(sub_query, app_params)
            r_row = cursor.fetchone()
            
            reqs = r_row[0] or 0
            if reqs == 0:
                continue
                
            in_t = r_row[1] or 0
            out_t = r_row[2] or 0
            c_read = r_row[3] or 0
            c_write = r_row[4] or 0
            cst = r_row[5] or 0.0
            succ = r_row[6] or 0
            succ_rate = (succ / reqs * 100) if reqs > 0 else 100.0
            
            provider_name = active_map.get(app, "默认配置")
            if app == "claude-desktop" and "claude-desktop" not in active_map:
                provider_name = active_map.get("claude", provider_name)
                
            active_configs.append({
                "app_type": app,
                "provider_name": provider_name,
                "summary": {
                    "total_requests": reqs,
                    "total_tokens": in_t + out_t,
                    "real_tokens": in_t + out_t + c_read + c_write,
                    "input_tokens": in_t,
                    "output_tokens": out_t,
                    "cache_read_tokens": c_read,
                    "cache_creation_tokens": c_write,
                    "total_cost": round(cst, 6),
                    "success_rate": round(succ_rate, 2)
                }
            })

        # 5. Query Trends
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

        # 6. Query Recent Logs
        select_fields = [
            "l.request_id",
            f"COALESCE(p.name, {l_provider_id_col}) AS provider_name",
            f"COALESCE({app_type_col}, '') AS app_type",
            f"COALESCE({request_model_col}, '') AS request_model",
            f"COALESCE({input_col}, 0) AS input_tokens",
            f"COALESCE({output_col}, 0) AS output_tokens",
            f"COALESCE({cache_read_col}, 0) AS cache_read_tokens",
            f"COALESCE({cache_creation_col}, 0) AS cache_creation_tokens",
            f"COALESCE({cost_col}, 0.0) AS total_cost_usd",
            f"COALESCE(l.duration_ms, l.latency_ms, 0) AS duration_ms",
            f"COALESCE(l.first_token_ms, 0) AS first_token_ms",
            f"COALESCE({status_col}, 200) AS status_code",
            f"COALESCE({created_at_col}, 0) AS created_at"
        ]
        select_clause = ", ".join(select_fields)
        
        logs_query = f"""
            SELECT {select_clause} 
            FROM proxy_request_logs l
            LEFT JOIN providers p ON {l_provider_id_col} = p.id
            {where_clause}
            ORDER BY {created_at_col} DESC
            LIMIT 50
        """
        cursor.execute(logs_query, params)
        cols = ["id", "provider_name", "app_type", "request_model", "input_tokens", "output_tokens", 
                "cache_read_tokens", "cache_creation_tokens", "total_cost_usd", "duration_ms", 
                "first_token_ms", "status_code", "created_at"]
        recent_logs = []
        for row in cursor.fetchall():
            recent_logs.append({cols[i]: row[i] for i in range(len(cols))})

        # 7. Query Model Stats
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

        # 8. Query Provider Stats (grouped by provider)
        provider_stats = []
        provider_query = f"""
            SELECT 
                COALESCE(p.name, {l_provider_id_col}) AS prov_name,
                {app_type_col} AS app,
                COUNT(*),
                SUM(COALESCE({fresh_input_col}, 0)),
                SUM(COALESCE({output_col}, 0)),
                SUM(COALESCE({cache_read_col}, 0)),
                SUM(COALESCE({cache_creation_col}, 0)),
                SUM(COALESCE({cost_col}, 0.0)),
                SUM(CASE WHEN {status_col} >= 200 AND {status_col} < 300 THEN 1 ELSE 0 END),
                AVG(COALESCE(l.latency_ms, l.duration_ms, 0))
            FROM proxy_request_logs l
            LEFT JOIN providers p ON {l_provider_id_col} = p.id
            {where_clause}
            GROUP BY prov_name, {app_type_col}
            ORDER BY COUNT(*) DESC
        """
        cursor.execute(provider_query, params)
        for r in cursor.fetchall():
            reqs = r[2] or 0
            succ = r[8] or 0
            provider_stats.append({
                "provider_name": r[0] or "Unknown",
                "app_type": r[1] or "Unknown",
                "requests": reqs,
                "input_tokens": r[3] or 0,
                "output_tokens": r[4] or 0,
                "cache_read": r[5] or 0,
                "cache_creation": r[6] or 0,
                "total_tokens": (r[3] or 0) + (r[4] or 0),
                "cost": round(r[7] or 0.0, 6),
                "success_rate": round((succ / reqs * 100) if reqs > 0 else 100.0, 2),
                "avg_latency": round(r[9] or 0.0, 1)
            })

        # 9. Get Filter Lists (Unique Providers & Models in this time window, without other filters applied)
        cursor.execute(f"""
            SELECT DISTINCT COALESCE(p.name, {l_provider_id_col})
            FROM proxy_request_logs l
            LEFT JOIN providers p ON {l_provider_id_col} = p.id
            WHERE {created_at_col} >= ? AND {created_at_col} <= ?
        """, (start_time, end_time))
        providers_list = sorted([row[0] for row in cursor.fetchall() if row[0]])
        
        cursor.execute(f"""
            SELECT DISTINCT {request_model_col}
            FROM proxy_request_logs l
            WHERE {created_at_col} >= ? AND {created_at_col} <= ?
        """, (start_time, end_time))
        models_list = sorted([row[0] for row in cursor.fetchall() if row[0]])

        conn.close()
        
        return {
            "summary": summary,
            "trends": trends,
            "recent_logs": recent_logs,
            "active_configs": active_configs,
            "model_stats": model_stats,
            "provider_stats": provider_stats,
            "providers_list": providers_list,
            "models_list": models_list
        }
        
    except Exception as e:
        return {
            "error": f"Failed to query database: {str(e)}",
            "summary": {"total_requests": 0, "total_tokens": 0, "total_cost": 0.0, "success_rate": 100.0},
            "trends": [],
            "recent_logs": [],
            "active_configs": [],
            "model_stats": [],
            "provider_stats": [],
            "providers_list": [],
            "models_list": []
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

def run(server_class=HTTPServer, handler_class=AggregatorAgentHandler, port=PORT):
    server_address = ('', port)
    httpd = server_class(server_address, handler_class)
    print(f"CC Switch Aggregator Agent running on port {port}...")
    print(f"Reading database from: {get_db_path()}")
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
