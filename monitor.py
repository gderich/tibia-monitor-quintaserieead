"""
MONITOR DE GUILD - TIBIA (v3 - com notificações no Telegram)
==============================================================
Mesma lógica do v2 (usa /v4/world/{world} para checar quem está online),
mas agora também envia mensagens no Telegram quando alguém loga/desloga.

CONFIGURAÇÃO NECESSÁRIA (feita via "Secrets" no GitHub, não no código):
- TELEGRAM_BOT_TOKEN -> token do seu bot (recebido do @BotFather)
- TELEGRAM_CHAT_ID   -> ID do grupo/chat onde as mensagens serão enviadas

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
from datetime import datetime, timezone, timedelta

BRASILIA_TZ = timezone(timedelta(hours=-3))

GUILD_NAME = "Quinta Serie Ead"
WORLD_NAME = "Jadebra"

REFRESH_MEMBERS_EVERY_RUNS = 24  # ~6h se rodar a cada 15 min

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = os.path.join(BASE_DIR, "state.json")
MEMBERS_FILE = os.path.join(BASE_DIR, "members.json")
SESSIONS_FILE = os.path.join(BASE_DIR, "sessions.csv")
STATUS_LOG_FILE = os.path.join(BASE_DIR, "status_log.csv")

API_GUILD_URL = "https://api.tibiadata.com/v4/guild/{}"
API_WORLD_URL = "https://api.tibiadata.com/v4/world/{}"
HEADERS = {"User-Agent": "Tibia-Guild-Monitor/1.0 (personal use)"}

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
TELEGRAM_API_URL = "https://api.telegram.org/bot{}/sendMessage"


def now_str():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def to_brasilia_hour(utc_timestamp_str):
    """Recebe 'YYYY-MM-DD HH:MM:SS' em UTC e retorna 'HH:MM' em horário de Brasília"""
    dt_utc = datetime.strptime(utc_timestamp_str, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    dt_brasilia = dt_utc.astimezone(BRASILIA_TZ)
    return dt_brasilia.strftime("%H:%M")


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

    return duration_min


def send_telegram_message(text):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("Telegram não configurado (faltam TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID). Pulando notificação.")
        return

    url = TELEGRAM_API_URL.format(TELEGRAM_BOT_TOKEN)
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
    }
    try:
        resp = requests.post(url, json=payload, timeout=15)
        if resp.status_code != 200:
            print(f"Erro ao enviar mensagem Telegram: {resp.status_code} {resp.text}")
    except requests.exceptions.RequestException as e:
        print(f"Erro de rede ao enviar Telegram: {e}")


def format_duration(minutes):
    if minutes < 60:
        return f"~{int(minutes)}min"
    hours = minutes / 60
    return f"~{hours:.1f}h".replace(".0h", "h")


def main():
    ensure_csv_headers()
    state = load_json(STATE_FILE, {})
    members_data = load_json(MEMBERS_FILE, {"members": [], "run_count": 0})

    members = members_data.get("members", [])
    run_count = members_data.get("run_count", 0)

    timestamp = now_str()
    is_first_run = (run_count == 0)

    if not members or run_count % REFRESH_MEMBERS_EVERY_RUNS == 0:
        try:
            members = fetch_guild_members(GUILD_NAME)
            print(f"[{timestamp}] Lista de membros atualizada: {len(members)} players.")
        except requests.exceptions.RequestException as e:
            print(f"[{timestamp}] Erro ao buscar membros da guild: {e}")
            if not members:
                return

    run_count += 1
    save_json(MEMBERS_FILE, {"members": members, "run_count": run_count})

    try:
        online_players = fetch_online_players(WORLD_NAME)
        print(f"[{timestamp}] DEBUG: {len(online_players)} players online no mundo {WORLD_NAME} no total.")
        print(f"[{timestamp}] DEBUG: amostra de nomes online: {list(online_players)[:10]}")
        print(f"[{timestamp}] DEBUG: amostra de nomes da guild: {[m['name'] for m in members[:5]]}")
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
            # Não notifica na primeira vez que vê o player (evita spam inicial)
            continue

        if prev["status"] != status:
            if status == "online":
                print(f"[{timestamp}] {name} LOGOU")
                if not is_first_run:
                    send_telegram_message(
                        f"🟢 <b>{name}</b> está online! ({vocation} · Lv {level})\n"
                        f"🕒 Logou às {to_brasilia_hour(timestamp)}"
                    )
                state[name] = {"status": "online", "since": timestamp, "vocation": vocation, "level": level}
            else:
                print(f"[{timestamp}] {name} DESLOGOU (online desde {prev['since']})")
                duration_min = log_session(name, vocation, level, prev["since"], timestamp)
                if not is_first_run:
                    login_hour = to_brasilia_hour(prev["since"])
                    logout_hour = to_brasilia_hour(timestamp)
                    send_telegram_message(
                        f"🔴 <b>{name}</b> deslogou. 👋\n"
                        f"🕒 Logou às {login_hour} • Deslogou às {logout_hour}\n"
                        f"⏱ Ficou online por {format_duration(duration_min)}"
                    )
                state[name] = {"status": "offline", "since": timestamp, "vocation": vocation, "level": level}
        else:
            state[name]["vocation"] = vocation
            state[name]["level"] = level

    save_json(STATE_FILE, state)
    print(f"[{timestamp}] Checagem ok. {online_count}/{len(members)} online (mundo {WORLD_NAME}).")


if __name__ == "__main__":
    main()
