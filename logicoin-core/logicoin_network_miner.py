#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, Tuple

from logicoin_core import (
    PROJECT_NAME,
    COIN_NAME,
    TICKER,
    DEFAULT_MINER_ADDRESS,
    DIFFICULTY_RULE_V2,
    DIFFICULTY_RULE_V3,
    create_candidate_block_from_tip,
    mine_candidate_block,
)

MINER_VERSION = "0.12.15.3"
DEFAULT_NODE_URL = "http://127.0.0.1:8080"


def ask_text(prompt: str, default: str) -> str:
    text = input(f"{prompt} [{default}]: ").strip()
    return text if text else default


def ask_int(prompt: str, default: int, minimum: int = 1, maximum: int = 100) -> int:
    while True:
        text = input(f"{prompt} [{default}]: ").strip()
        if text == "":
            value = default
        else:
            try:
                value = int(text)
            except ValueError:
                print("Bitte eine ganze Zahl eingeben.")
                continue

        if value < minimum or value > maximum:
            print(f"Bitte Zahl zwischen {minimum} und {maximum} eingeben.")
            continue

        return value


def press_enter() -> None:
    input("\nEnter drücken, um zurück zum Menü zu gehen...")


def normalize_node_url(url: str) -> str:
    url = url.strip().rstrip("/")
    if not url.startswith("http://") and not url.startswith("https://"):
        url = "http://" + url
    return url


def get_json(url: str, timeout: int = 10) -> Dict[str, Any]:
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def post_json(url: str, data: Dict[str, Any], timeout: int = 15) -> Dict[str, Any]:
    payload = json.dumps(data, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        try:
            return json.loads(body)
        except Exception:
            return {"ok": False, "error": body}


def is_stale_rejection(response: Dict[str, Any]) -> bool:
    error = str(response.get("error", "")).lower()
    stale_markers = [
        "falscher index",
        "previous_hash passt nicht",
        "anderer block gefunden",
        "node-tip",
    ]
    return any(marker in error for marker in stale_markers)


def show_node_info(node_url: str) -> None:
    try:
        info = get_json(node_url + "/info")
    except Exception as e:
        print(f"\nNode nicht erreichbar: {e}")
        return

    params = info.get("network_params", {})
    diff_stats = info.get("difficulty_stats", {})
    chain_stats = info.get("chain_stats", {})

    print("\nNode-Info:")
    print(f"- Projekt: {info.get('project')}")
    print(f"- Coin: {info.get('coin')} / {info.get('ticker')}")
    print(f"- Node-Version: {info.get('version')}")
    print(f"- Miner-Version: {MINER_VERSION}")
    print(f"- Höhe: #{info.get('height')}")
    print(f"- Blöcke insgesamt: {info.get('blocks_total')}")
    print(f"- Mempool: {info.get('mempool_count')} TXs")
    print(f"- Tip-Hash: {info.get('tip_hash')}")
    print(f"- Chain gültig: {info.get('chain_valid')} - {info.get('chain_status')}")

    print("\nAuto-Difficulty:")
    print(f"- Nächste Difficulty: {params.get('next_difficulty')}")
    print(f"- Min/Max Difficulty: {params.get('min_difficulty')} / {params.get('max_difficulty')}")
    print(f"- Blockreward: {params.get('block_reward')} {TICKER}")
    print(f"- Min TX Fee: {params.get('min_tx_fee')} {TICKER}")
    print(f"- Max TXs pro Block: {params.get('max_transactions_per_block')}")

    print("\nDifficulty-Statistik:")
    print(f"- Geminte Blöcke: {diff_stats.get('mined_blocks')}")
    print(f"- Aktuelle Difficulty: {diff_stats.get('current_difficulty')}")
    print(f"- Nächste Difficulty: {diff_stats.get('next_difficulty')}")
    print(f"- Durchschnittszeit letzter Abschnitt: {float(diff_stats.get('avg_time_last_interval', 0.0)):.2f} Sekunden")
    print(f"- Durchschnitts-H/s letzter Abschnitt: {float(diff_stats.get('avg_hashrate_last_interval', 0.0)):,.2f} H/s")

    print("\nChain-Statistik:")
    print(f"- Gesamt-Reward: {float(chain_stats.get('total_reward', 0.0)):.8f} {TICKER}")
    print(f"- Gesamt-Fees: {float(chain_stats.get('total_fees', 0.0)):.8f} {TICKER}")
    print(f"- Transfer-TXs: {chain_stats.get('transfer_transactions')}")
    print(f"- Durchschnittliche Blockzeit: {float(chain_stats.get('average_block_time_seconds', 0.0)):.2f} Sekunden")
    print(f"- Durchschnittliche H/s: {float(chain_stats.get('average_hashrate_hs', 0.0)):,.2f} H/s")

    print("\nBalances:")
    balances = info.get("balances", {})
    if not balances:
        print("- Noch keine Rewards.")
    else:
        for address, balance in sorted(balances.items()):
            print(f"- {address}: {balance:.8f} {TICKER}")


def mine_once_and_submit(node_url: str, miner_address: str) -> Tuple[bool, bool]:
    try:
        encoded = urllib.parse.quote(miner_address)
        template = get_json(node_url + f"/mining_template?miner={encoded}")
    except Exception as e:
        print(f"\nNode nicht erreichbar: {e}")
        return False, False

    if not template.get("ok"):
        print(f"\nNode-Fehler: {template}")
        return False, False

    if not template.get("chain_valid"):
        print("\nNode-Chain ist ungültig. Mining abgebrochen.")
        print(template.get("chain_status"))
        return False, False

    tip = template["tip"]
    params = template.get("network_params", {})
    difficulty_rule = str(
        params.get(
            "difficulty_rule",
            DIFFICULTY_RULE_V3,
        )
    )
    difficulty_bits = int(
        params.get(
            "next_difficulty_bits",
            int(params.get("next_difficulty", 4)) * 4,
        )
    )
    difficulty = int(
        params.get(
            "next_difficulty",
            (difficulty_bits + 3) // 4,
        )
    )
    reward = float(params.get("block_reward", 50.0))
    transactions = template.get("transactions", [])

    print("\nVom Node erhalten:")
    print(f"- Aktueller Tip: #{tip.get('index')} {tip.get('hash')}")
    print(f"- Netzwerk-Difficulty für nächsten Block: {difficulty}")
    print(f"- Blockreward: {reward} {TICKER}")
    print(f"- Mempool-TXs für Block: {len(transactions)}")
    print(f"- Gebühren im Block: {float(template.get('total_fees', 0.0)):.8f} {TICKER}")

    candidate = create_candidate_block_from_tip(
        tip_block=tip,
        miner_address=miner_address,
        difficulty=difficulty,
        reward=reward,
        transactions=transactions,
        difficulty_rule=difficulty_rule,
        difficulty_bits=difficulty_bits,
    )

    try:
        block = mine_candidate_block(candidate, verbose=True)
    except KeyboardInterrupt:
        print("\nMining abgebrochen. Kein Block gesendet.")
        return False, False

    print("\nSende Block an Node...")
    response = post_json(node_url + "/submit_block", block)

    if response.get("accepted"):
        params_after = response.get("network_params", {})
        print("BLOCK WURDE VOM NODE AKZEPTIERT!")
        print(f"- Neue Höhe: #{response.get('height')}")
        print(f"- Tip-Hash: {response.get('tip_hash')}")
        print(f"- Bestätigte TXs: {response.get('confirmed_transactions')}")
        print(f"- Fees: {float(response.get('fees', 0.0)):.8f} {TICKER}")
        print(f"- Mempool übrig: {response.get('mempool_count')}")
        print(f"- Nächste Difficulty: {params_after.get('next_difficulty')}")
        return True, False

    print("Block wurde abgelehnt.")
    print(f"Grund: {response.get('error')}")

    if is_stale_rejection(response):
        print("Stale Block erkannt: Ein anderer Miner war schneller oder der Node ist weiter.")
        print("Hole neuen Tip und versuche es automatisch erneut.")
        return False, True

    return False, False


def mine_and_submit_with_retry(node_url: str, miner_address: str, max_stale_retries: int = 3) -> bool:
    stale_retries = 0

    while True:
        accepted, stale = mine_once_and_submit(node_url, miner_address)

        if accepted:
            return True

        if stale and stale_retries < max_stale_retries:
            stale_retries += 1
            print(f"\nStale-Retry {stale_retries}/{max_stale_retries}")
            continue

        if stale:
            print("\nZu viele stale Blöcke hintereinander. Dieser Blockversuch wird abgebrochen.")
        return False


def auto_mine_menu(node_url: str) -> None:
    miner_address = ask_text("Miner-Adresse", DEFAULT_MINER_ADDRESS)
    amount = ask_int("Wie viele akzeptierte Blöcke minen und senden?", 5, minimum=1, maximum=100)
    max_stale_retries = ask_int("Max. Stale-Retries pro Block", 3, minimum=0, maximum=20)

    print("\nNetzwerk-Auto-Mining gestartet.")
    print(f"- Node: {node_url}")
    print(f"- Miner: {miner_address}")
    print(f"- Ziel akzeptierte Blöcke: {amount}")
    print(f"- Max. Stale-Retries pro Block: {max_stale_retries}")
    print("- Difficulty, Reward und Transaktionen kommen automatisch vom Node.")

    accepted_blocks = 0
    failed_attempts = 0
    start = time.time()

    while accepted_blocks < amount:
        print("\n" + "=" * 72)
        print(f"Ziel-Block {accepted_blocks + 1}/{amount}")
        print("=" * 72)

        ok = mine_and_submit_with_retry(
            node_url=node_url,
            miner_address=miner_address,
            max_stale_retries=max_stale_retries,
        )

        if ok:
            accepted_blocks += 1
        else:
            failed_attempts += 1
            print("\nDieser Ziel-Block wurde nicht akzeptiert.")
            stop = input("Weiter versuchen? JA = weiter, Enter = stoppen: ").strip()
            if stop != "JA":
                break

    elapsed = time.time() - start
    print("\n" + "=" * 72)
    print("NETZWERK-MINING STATISTIK")
    print("=" * 72)
    print(f"- Akzeptierte Blöcke: {accepted_blocks}/{amount}")
    print(f"- Fehlgeschlagene Ziel-Versuche: {failed_attempts}")
    print(f"- Gesamtzeit: {elapsed:.2f} Sekunden")


def main_menu() -> None:
    node_url = normalize_node_url(ask_text("Node-URL", DEFAULT_NODE_URL))

    while True:
        print("\n" + "=" * 72)
        print(f"{PROJECT_NAME} / {COIN_NAME} Netzwerk-Miner v{MINER_VERSION}")
        print("=" * 72)
        print(f"Aktuelle Node: {node_url}")
        print("1 = Node-Info anzeigen")
        print("2 = Einen Block mit Mempool-TXs minen")
        print("3 = Mehrere akzeptierte Blöcke mit Mempool-TXs minen")
        print("4 = Node-URL ändern")
        print("0 = Beenden")

        choice = input("\nAuswahl: ").strip()

        if choice == "1":
            show_node_info(node_url)
            press_enter()
        elif choice == "2":
            miner_address = ask_text("Miner-Adresse", DEFAULT_MINER_ADDRESS)
            max_stale_retries = ask_int("Max. Stale-Retries", 3, minimum=0, maximum=20)
            mine_and_submit_with_retry(node_url, miner_address, max_stale_retries=max_stale_retries)
            press_enter()
        elif choice == "3":
            auto_mine_menu(node_url)
            press_enter()
        elif choice == "4":
            node_url = normalize_node_url(ask_text("Node-URL", node_url))
        elif choice == "0":
            print("\nNetzwerk-Miner beendet.")
            break
        else:
            print("Ungültige Auswahl.")


def main() -> None:
    main_menu()


if __name__ == "__main__":
    main()
