import asyncio
from typing import Optional
from ..utils.logging import Icons, pretty_log

async def tool_postgres_admin(action: str, connection_string: str, query: Optional[str] = None, table_name: Optional[str] = None):
    pretty_log("Postgres Admin", f"Action: {action}", icon=Icons.TOOL_CODE)
    try:
        import psycopg2
        import psycopg2.extras
        from tabulate import tabulate
    except ImportError:
        return "Error: psycopg2 or tabulate library is missing."

    if not connection_string:
        return "Error: connection_string is required. Ask the user for the DB URI."

    def execute_db():
        try:
            with psycopg2.connect(connection_string) as conn:
                conn.autocommit = True
                with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                    if action == "schema":
                        sql = "SELECT table_name, column_name, data_type FROM information_schema.columns WHERE table_schema = 'public'"
                        if table_name:
                            sql += f" AND table_name = '{table_name}'"
                        cur.execute(sql)
                        rows = cur.fetchall()
                        if not rows: return "No schema found."
                        return tabulate(rows, headers="keys", tablefmt="pipe")
                    
                    elif action == "activity":
                        cur.execute("SELECT pid, state, query, extract(epoch from now() - query_start) as duration_sec FROM pg_stat_activity WHERE state != 'idle';")
                        rows = cur.fetchall()
                        if not rows: return "No active queries."
                        return tabulate(rows, headers="keys", tablefmt="pipe")
                    
                    elif action in ["query", "explain_analyze"]:
                        if not query: return "Error: query parameter required."
                        sql = query
                        if action == "explain_analyze" and not sql.upper().startswith("EXPLAIN"):
                            sql = f"EXPLAIN (ANALYZE, BUFFERS, FORMAT TEXT) {sql}"
                        
                        cur.execute(sql)
                        if cur.description:
                            rows = cur.fetchall()
                            output = tabulate(rows[:100], headers="keys", tablefmt="pipe")
                            if len(rows) > 100:
                                output += f"\n\n... [Truncated {len(rows)-100} rows]"
                            return output
                        return "Query executed successfully. No rows returned."
                    else:
                        return f"Unknown action: {action}"
        except Exception as e:
            return f"Postgres Error: {str(e)}"

    result = await asyncio.to_thread(execute_db)
    return f"### POSTGRES RESULT ###\n{result}"
