"""
MONITOR DE GUILD - TIBIA (versão GitHub Actions)
==================================================
Roda UMA VEZ, compara com o estado salvo (state.json) e atualiza:
- sessions.csv      -> log de logins/logouts completos (exportável)
- status_log.csv    -> log bruto de cada checagem
- state.json        -> estado atual de cada player

Esse script é chamado automaticamente pelo GitHub Actions a cada
intervalo de tempo definido em .github/workflows/monitor.yml
"""

import requests
import csv
import json
import os
from datetime import datetime, timezone

GUILD_NAME = "Quinta Serie Ead"

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = os.path.join(BASE_DIR, "state.json")
SESSIONS_FILE = os.path.join(BASE_DIR, "sessions.csv")
STATUS_LOG_FILE = os.path.join(BASE_DIR, "status_log.csv")

API_GUILD_URL = "https://api.tibiadata.com/v4/guild/{}"
HEADERS = {"User-Agent": "Tibia-Guild-Monitor/1.0 (personal use)"}


def now_str():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)


def ensure_csv_headers():
    if not os.path.exists(SESSIONS_FILE):
        with open(SESSIONS_FILE, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["player", "vocation", "level", "login_time_utc", "logout_time_utc", "duration_minutes"])

    if not os.path.exists(STATUS_LOG_FILE):
        with open(STATUS_LOG_FILE, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["timestamp_utc", "player", "status", "vocation", "level"])


def fetch_guild_members(guild_name):
    url = API_GUILD_URL.format(guild_name.replace(" ", "%20"))
    resp = requests.get(url, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    data = resp.json()

    guild = data.get("guild", {})
    members = guild.get("members", [])

    result = []
    for m in members:
        result.append({
            "name": m.get("name"),
            "vocation": m.get("vocation"),
            "level": m.get("level"),
            "status": m.get("status"),
        })
    return result


def log_status(player, status, vocation, level, timestamp):
    with open(STATUS_LOG_FILE, "a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow([timestamp, player, status, vocation, level])


def log_session(player, vocation, level, login_time, logout_time):
    login_dt = datetime.strptime(login_time, "%Y-%m-%d %H:%M:%S")
    logout_dt = datetime.strptime(logout_time, "%Y-%m-%d %H:%M:%S")
    duration_min = round((logout_dt - login_dt).total_seconds() / 60, 1)

    with open(SESSIONS_FILE, "a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow([player, vocation, level, login_time, logout_time, duration_min])


def main():
    ensure_csv_headers()
    state = load_state()

    timestamp = now_str()

    try:
        members = fetch_guild_members(GUILD_NAME)
    except requests.exceptions.RequestException as e:
        print(f"[{timestamp}] Erro de rede: {e}")
        return

    online_count = 0

    for m in members:
        name = m["name"]
        status = m["status"]
        vocation = m["vocation"]
        level = m["level"]

        if status == "online":
            online_count += 1

        log_status(name, status, vocation, level, timestamp)

        prev = state.get(name)

        if prev is None:
            state[name] = {"status": status, "since": timestamp, "vocation": vocation, "level": level}
            continue

        if prev["status"] != status:
            if status == "online":
                print(f"[{timestamp}] {name} LOGOU")
                state[name] = {"status": "online", "since": timestamp, "vocation": vocation, "level": level}
            else:
                print(f"[{timestamp}] {name} DESLOGOU (online desde {prev['since']})")
                log_session(name, vocation, level, prev["since"], timestamp)
                state[name] = {"status": "offline", "since": timestamp, "vocation": vocation, "level": level}
        else:
            state[name]["vocation"] = vocation
            state[name]["level"] = level

    save_state(state)
    print(f"[{timestamp}] Checagem ok. {online_count}/{len(members)} online.")


if __name__ == "__main__":
    main()
