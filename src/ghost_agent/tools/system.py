import datetime
import urllib.parse
import httpx
from ..utils.logging import Icons, pretty_log

async def tool_get_current_time():
    pretty_log("System Time", "Querying local time", icon=Icons.TOOL_FILE_I)
    now = datetime.datetime.now()
    return f"Current System Time: {now.strftime('%Y-%m-%d %H:%M:%S')} (Day: {now.strftime('%A')})"

async def tool_get_weather(tor_proxy: str, profile_memory=None, location: str = None):
    if not location and profile_memory:
        try:
            data = profile_memory.load()
            found_loc = (
                data.get("root", {}).get("location") or 
                data.get("root", {}).get("city") or 
                data.get("personal", {}).get("location")
            )
            if found_loc:
                location = found_loc
                pretty_log("Weather", f"Using profile location: {location}", icon=Icons.MEM_MATCH)
        except: pass

    pretty_log("System Weather", f"Location: {location}", icon=Icons.TOOL_SEARCH)
    if not location:
        return "SYSTEM ERROR: No location provided. You MUST specify a city (e.g., 'London') or update your profile."

    proxy_url = tor_proxy
    if proxy_url.startswith("socks5://"):
        proxy_url = proxy_url.replace("socks5://", "socks5h://")
    
    try:
        async with httpx.AsyncClient(proxy=proxy_url, timeout=20.0) as client:
            geo_url = f"https://geocoding-api.open-meteo.com/v1/search?name={urllib.parse.quote(location)}&count=1&language=en&format=json"
            geo_resp = await client.get(geo_url)
            if geo_resp.status_code == 200 and geo_resp.json().get("results"):
                res = geo_resp.json()["results"][0]
                lat, lon, name = res["latitude"], res["longitude"], res["name"]
                w_url = (
                    f"https://api.open-meteo.com/v1/forecast?"
                    f"latitude={lat}&longitude={lon}&"
                    f"current=temperature_2m,relative_humidity_2m,weather_code,wind_speed_10m&"
                    f"wind_speed_unit=kmh"
                )
                w_resp = await client.get(w_url)
                if w_resp.status_code == 200:
                    curr = w_resp.json().get("current", {})
                    wmo_map = {0: "Clear", 1: "Mainly Clear", 2: "Partly Cloudy", 3: "Overcast", 45: "Fog", 61: "Rain", 63: "Heavy Rain", 71: "Snow", 95: "Thunderstorm"}
                    cond = wmo_map.get(curr.get("weather_code"), "Variable")
                    return (
                        f"REPORT (Source: Open-Meteo): Weather in {name}\n"
                        f"Condition: {cond}\n"
                        f"Temp: {curr.get('temperature_2m')}°C\n"
                        f"Wind: {curr.get('wind_speed_10m')} km/h\n"
                        f"Humidity: {curr.get('relative_humidity_2m')}%"
                    )
    except Exception as e:
        pretty_log("Weather Warn", f"Open-Meteo failed: {e}", level="WARN", icon=Icons.WARN)

    try:
        url = f"https://wttr.in/{urllib.parse.quote(location)}?format=3"
        async with httpx.AsyncClient(proxy=proxy_url, timeout=20.0) as client:
            resp = await client.get(url)
            if resp.status_code == 200 and "<html" not in resp.text.lower():
                return f"REPORT (Source: wttr.in): {resp.text.strip()}"
    except Exception as e:
        pretty_log("Weather Error", str(e), level="ERROR", icon=Icons.FAIL)

    return "SYSTEM ERROR: Connection failed to all weather providers via Tor."

async def tool_system_utility(action: str, tor_proxy: str, profile_memory=None, location: str = None):
    if action == "check_time":
        return await tool_get_current_time()
    elif action == "check_weather":
        return await tool_get_weather(tor_proxy, profile_memory, location)
    else:
        return f"Error: Unknown action '{action}'"

async def tool_system_health(upstream_url: str, http_client, sandbox_manager=None, memory_system=None):
    pretty_log("System Health", "Running diagnostic", icon=Icons.SYSTEM_READY)

    report = ["SYSTEM HEALTH REPORT", "=" * 30]



    try:

        resp = await http_client.get("/health")

        status = "✅ Online" if resp.status_code == 200 else f"⚠️ Code {resp.status_code}"

        report.append(f"LLM Server      : {status} ({upstream_url})")

    except:

        report.append(f"LLM Server      : ❌ Connection Failed")



    if sandbox_manager:

        try:

            sandbox_manager.ensure_running()

            out, code = await asyncio.to_thread(sandbox_manager.execute, "timeout 1s sleep 0.1")

            if code == 0:

                report.append(f"Execution Engine: ✅ Active")

            else:

                report.append(f"Execution Engine: ⚠️ Ready (Timeout util failed)")

        except Exception as e:

            report.append(f"Execution Engine: ❌ Critical Error")

    else:

        report.append("Execution Engine: ❌ Sandbox Manager Not Loaded")



    if memory_system:

        try:

            def get_count():

                return memory_system.collection.count()

            count = await asyncio.to_thread(get_count)

            report.append(f"Memory System   : ✅ Active ({count} items)")

        except Exception as e:

            report.append(f"Memory System   : ❌ DB Error")

    else:

        report.append("Memory System   : ⚠️ Disabled")



    return "\n".join(report)
