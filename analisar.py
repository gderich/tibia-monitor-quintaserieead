"""
ANALISADOR DE PADRÕES - GUILD TIBIA
=====================================
Lê o arquivo status_log.csv (gerado pelo monitor.py) e calcula:

1. Horários de pico de cada player (em que horas costuma logar)
2. Correlação entre pares de players: probabilidade de serem
   a mesma pessoa (multi-char), baseado em:
   - Quanto tempo MAIS FICAM ONLINE JUNTOS (mesma pessoa não joga 2 chars
     ao mesmo tempo normalmente -> se NUNCA aparecem online juntos, mas
     sempre alternam, é suspeito)
   - Padrão de troca: A desloga e B loga pouco depois (e vice-versa)

COMO USAR:
1. Deixe o monitor.py rodando por pelo menos alguns dias para ter dados.
2. Rode:
   python analisar.py

3. Será gerado um arquivo "relatorio_padroes.csv" com o ranking de pares
   de players mais suspeitos de serem multis, e um resumo no terminal.
"""

import csv
import os
from collections import defaultdict
from datetime import datetime
from itertools import combinations

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATUS_LOG_FILE = os.path.join(BASE_DIR, "status_log.csv")
REPORT_FILE = os.path.join(BASE_DIR, "relatorio_padroes.csv")

# Janela de tempo (em minutos) para considerar "troca rápida" entre logout
# de um player e login de outro como suspeito
SWAP_WINDOW_MINUTES = 5


def load_status_log():
    """Retorna lista de (timestamp_dt, player, status)"""
    rows = []
    if not os.path.exists(STATUS_LOG_FILE):
        print("Nenhum dado encontrado ainda. Deixe o monitor.py rodando primeiro.")
        return rows

    with open(STATUS_LOG_FILE, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            ts = datetime.strptime(row["timestamp_utc"], "%Y-%m-%d %H:%M:%S")
            rows.append((ts, row["player"], row["status"]))
    return rows


def build_online_intervals(rows):
    """
    A partir do log bruto, reconstrói intervalos (login, logout) por player.
    Retorna dict: player -> lista de (login_dt, logout_dt)
    """
    last_seen_status = {}
    last_seen_time = {}
    intervals = defaultdict(list)

    # ordenar por tempo
    rows_sorted = sorted(rows, key=lambda r: r[0])

    for ts, player, status in rows_sorted:
        prev_status = last_seen_status.get(player)

        if prev_status is None:
            if status == "online":
                last_seen_time[player] = ts
            last_seen_status[player] = status
            continue

        if prev_status == "online" and status == "offline":
            login_time = last_seen_time.get(player)
            if login_time:
                intervals[player].append((login_time, ts))
            last_seen_status[player] = "offline"

        elif prev_status == "offline" and status == "online":
            last_seen_time[player] = ts
            last_seen_status[player] = "online"

    return intervals


def intervals_overlap(a_start, a_end, b_start, b_end):
    latest_start = max(a_start, b_start)
    earliest_end = min(a_end, b_end)
    return (earliest_end - latest_start).total_seconds() if earliest_end > latest_start else 0


def login_hour_histogram(intervals):
    """player -> dict hora(0-23) -> contagem de logins"""
    hist = defaultdict(lambda: defaultdict(int))
    for player, sessions in intervals.items():
        for login, _ in sessions:
            hist[player][login.hour] += 1
    return hist


def compute_pair_score(player_a, player_b, intervals):
    """
    Calcula um score 0-100 de probabilidade de A e B serem a mesma pessoa,
    baseado em:
    - 0% overlap (nunca online ao mesmo tempo) = +pontos
    - swaps rápidos (um desloga, outro loga em seguida) = +pontos
    """
    sessions_a = intervals.get(player_a, [])
    sessions_b = intervals.get(player_b, [])

    if not sessions_a or not sessions_b:
        return 0, 0, 0

    total_overlap_seconds = 0
    for a_start, a_end in sessions_a:
        for b_start, b_end in sessions_b:
            total_overlap_seconds += intervals_overlap(a_start, a_end, b_start, b_end)

    # contar swaps: logout de A seguido de login de B (ou vice-versa) dentro da janela
    swap_count = 0
    window = SWAP_WINDOW_MINUTES * 60

    for a_start, a_end in sessions_a:
        for b_start, b_end in sessions_b:
            if 0 <= (b_start - a_end).total_seconds() <= window:
                swap_count += 1
            if 0 <= (a_start - b_end).total_seconds() <= window:
                swap_count += 1

    total_sessions = len(sessions_a) + len(sessions_b)

    # Score heurístico simples (0-100):
    # - Penaliza overlap (se ficaram online juntos por muito tempo, dificilmente é multi)
    # - Premia swaps (trocas rápidas entre os dois)
    overlap_penalty = min(total_overlap_seconds / 3600, 10) * 5  # até -50 pontos se overlap >= 10h
    swap_bonus = min(swap_count, 20) * 4  # até +80 pontos

    score = max(0, min(100, swap_bonus - overlap_penalty + 20))

    # se não há dados suficientes, reduzir confiança
    if total_sessions < 4:
        score = score * 0.5

    return round(score, 1), swap_count, round(total_overlap_seconds / 60, 1)


def main():
    rows = load_status_log()
    if not rows:
        return

    intervals = build_online_intervals(rows)

    if not intervals:
        print("Ainda não há sessões completas (login+logout) registradas. Aguarde mais tempo.")
        return

    players = list(intervals.keys())
    print(f"Players com dados: {len(players)}")
    print()

    # Histograma de horários
    hist = login_hour_histogram(intervals)
    print("=== HORÁRIOS DE LOGIN MAIS COMUNS POR PLAYER ===")
    for player in players:
        h = hist[player]
        if not h:
            continue
        top_hours = sorted(h.items(), key=lambda x: -x[1])[:3]
        top_str = ", ".join([f"{hour}h ({count}x)" for hour, count in top_hours])
        print(f"  {player}: {top_str}")
    print()

    # Pares - score de "mesma pessoa"
    print("=== ANÁLISE DE PARES (POSSÍVEL MESMA PESSOA) ===")
    results = []
    for a, b in combinations(players, 2):
        score, swaps, overlap_min = compute_pair_score(a, b, intervals)
        if score > 0:
            results.append((a, b, score, swaps, overlap_min))

    results.sort(key=lambda x: -x[2])

    with open(REPORT_FILE, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["player_a", "player_b", "score_mesma_pessoa_%", "trocas_rapidas", "minutos_online_juntos"])
        for a, b, score, swaps, overlap_min in results:
            writer.writerow([a, b, score, swaps, overlap_min])

    for a, b, score, swaps, overlap_min in results[:15]:
        print(f"  {a} <-> {b}: score {score}%  (trocas rápidas: {swaps}, juntos online: {overlap_min}min)")

    print()
    print(f"Relatório completo salvo em: {REPORT_FILE}")


if __name__ == "__main__":
    main()
