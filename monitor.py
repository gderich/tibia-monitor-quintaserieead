"""
MONITOR DE GUILD - TIBIA (v2 - usando lista de online do servidor)
=====================================================================
A API de guild não retorna o status online em tempo real de forma
confiável. Esse script usa o endpoint /v4/world/{world}, que traz a
lista de TODOS os players online no servidor agora, e cruza essa lista
com os membros da guild "Quinta Serie Ead".

Roda UMA VEZ por execução (chamado pelo GitHub Actions a cada X minutos).

Arquivos gerados:
- members.json      -> lista de membros da guild (atualizada periodicamente)
- sessions.csv      -> log de logins/logouts completos (exportável)
- status_log.csv    -> log bruto de cada checagem
- state.json        -> estado atual de cada player
"""

import requests
import csv
import json
import os
from datetime import datetime, timezone

GUILD_NAME = "Quinta Serie Ead"
WORLD_NAME = "Jadebra"

# Recarrega a lista de membros da guild a cada N execuções
# (não precisa toda vez, a lista de membros muda pouco)
REFRESH_MEMBERS_EVERY_RUNS = 24  # ex: 24 execuções de 15min = a cada ~6h

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = os.path.join(BASE_DIR, "state.json")
MEMBERS_FILE = os.path.join(BASE_DIR, "members.json")
SESSIONS_FILE = os.path.join(BASE_DIR, "sessions.csv")
STATUS_LOG_FILE = os.path.join(BASE_DIR, "status_log.csv")

API_GUILD_URL = "https://api.tibiadata.com/v4/guild/{}"
API_WORLD_URL = "https://api.tibiadata.com/v4/world/{}"
HEADERS = {"User-Agent": "Tibia-Guild-Monitor/1.0 (personal use)"}


def now_str():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def load_json(path, default):
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return default


def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


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
    """Retorna lista de dicts: {name, vocation, level}"""
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
        })
    return result


def fetch_online_players(world_name):
    """Retorna set com nomes de players online no mundo agora"""
    url = API_WORLD_URL.format(world_name)
    resp = requests.get(url, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    data = resp.json()

    world = data.get("world", {})
    online_players = world.get("online_players", [])

    return {p.get("name") for p in online_players}


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
    state = load_json(STATE_FILE, {})
    members_data = load_json(MEMBERS_FILE, {"members": [], "run_count": 0})

    members = members_data.get("members", [])
    run_count = members_data.get("run_count", 0)

    timestamp = now_str()

    # Atualiza lista de membros se necessário (primeira vez ou periodicamente)
    if not members or run_count % REFRESH_MEMBERS_EVERY_RUNS == 0:
        try:
            members = fetch_guild_members(GUILD_NAME)
            print(f"[{timestamp}] Lista de membros atualizada: {len(members)} players.")
        except requests.exceptions.RequestException as e:
            print(f"[{timestamp}] Erro ao buscar membros da guild: {e}")
            if not members:
                return  # sem lista de membros não há o que fazer

    run_count += 1
    save_json(MEMBERS_FILE, {"members": members, "run_count": run_count})

    # Busca players online no mundo agora
    try:
        online_players = fetch_online_players(WORLD_NAME)
    except requests.exceptions.RequestException as e:
        print(f"[{timestamp}] Erro ao buscar players online do mundo: {e}")
        return

    online_count = 0

    for m in members:
        name = m["name"]
        vocation = m["vocation"]
        level = m["level"]

        status = "online" if name in online_players else "offline"

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

    save_json(STATE_FILE, state)
    print(f"[{timestamp}] Checagem ok. {online_count}/{len(members)} online (mundo {WORLD_NAME}).")


if __name__ == "__main__":
    main()
