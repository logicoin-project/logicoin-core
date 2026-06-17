#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

from logicoin_core import CONFIG_FILE, load_config, save_config, DEFAULT_CONFIG


def ask_int(prompt: str, default: int, minimum: int = 1, maximum: int = 100) -> int:
    while True:
        text = input(f"{prompt} [{default}]: ").strip()
        if text == "":
            value = default
        else:
            try:
                value = int(text)
            except ValueError:
                print("Bitte ganze Zahl eingeben.")
                continue

        if value < minimum or value > maximum:
            print(f"Bitte zwischen {minimum} und {maximum} eingeben.")
            continue

        return value


def ask_bool(prompt: str, default: bool) -> bool:
    while True:
        default_text = "j" if default else "n"
        text = input(f"{prompt} (j/n) [{default_text}]: ").strip().lower()
        if text == "":
            return default
        if text in {"j", "ja", "y", "yes", "1", "true"}:
            return True
        if text in {"n", "nein", "no", "0", "false"}:
            return False
        print("Bitte j oder n eingeben.")


def ask_float(prompt: str, default: float, minimum: float = 0.0) -> float:
    while True:
        text = input(f"{prompt} [{default}]: ").strip().replace(",", ".")
        if text == "":
            value = default
        else:
            try:
                value = float(text)
            except ValueError:
                print("Bitte gültige Zahl eingeben.")
                continue

        if value < minimum:
            print(f"Bitte mindestens {minimum} eingeben.")
            continue

        return value


def print_config(config: dict) -> None:
    print("\nAktuelle Config:")
    print(f"- Datei: {CONFIG_FILE}")
    print(f"- Modus: automatisch")
    print(f"- start_difficulty: {config['start_difficulty']}")
    print(f"- min_difficulty: {config['min_difficulty']}")
    print(f"- max_difficulty: {config['max_difficulty']}")
    print(f"- target_block_time_seconds: {config['target_block_time_seconds']}")
    print(f"- difficulty_adjustment_interval: {config['difficulty_adjustment_interval']}")
    print(f"- increase_if_avg_below_seconds: {config['increase_if_avg_below_seconds']}")
    print(f"- decrease_if_avg_above_seconds: {config['decrease_if_avg_above_seconds']}")
    print("- Alte Hex-Regel: nur für historische Blöcke")
    print(f"- difficulty_v2_enabled: {config['difficulty_v2_enabled']}")
    print(f"- difficulty_v2_target_block_time_seconds: {config['difficulty_v2_target_block_time_seconds']}")
    print(f"- difficulty_v2_adjustment_interval: {config['difficulty_v2_adjustment_interval']}")
    print(f"- difficulty_v2_min_bits: {config['difficulty_v2_min_bits']}")
    print(f"- difficulty_v2_max_bits: {config['difficulty_v2_max_bits']}")
    print(f"- difficulty_v2_increase_below_seconds: {config['difficulty_v2_increase_below_seconds']}")
    print(f"- difficulty_v2_decrease_above_seconds: {config['difficulty_v2_decrease_above_seconds']}")
    print("\nDifficulty v3-fast – neue Blöcke")
    print(f"- difficulty_v3_enabled: {config['difficulty_v3_enabled']}")
    print(f"- difficulty_v3_target_block_time_seconds: {config['difficulty_v3_target_block_time_seconds']}")
    print(f"- difficulty_v3_adjustment_interval: {config['difficulty_v3_adjustment_interval']}")
    print(f"- difficulty_v3_min_bits: {config['difficulty_v3_min_bits']}")
    print(f"- difficulty_v3_max_bits: {config['difficulty_v3_max_bits']}")
    print(f"- difficulty_v3_increase_below_seconds: {config['difficulty_v3_increase_below_seconds']}")
    print(f"- difficulty_v3_decrease_above_seconds: {config['difficulty_v3_decrease_above_seconds']}")
    print(f"- difficulty_v3_max_step_up_bits: {config['difficulty_v3_max_step_up_bits']}")
    print(f"- difficulty_v3_max_step_down_bits: {config['difficulty_v3_max_step_down_bits']}")
    print("\nPublic Testnet")
    print(f"- network_name: {config['network_name']}")
    print(f"- network_id: {config['network_id']}")
    print(f"- release_channel: {config['release_channel']}")
    print(f"- public_testnet: {config['public_testnet']}")
    print(f"- seed_nodes: {config.get('seed_nodes', [])}")
    print(f"- block_reward: {config['block_reward']}")
    print(f"- max_transactions_per_block: {config['max_transactions_per_block']}")
    print(f"- min_tx_fee: {config['min_tx_fee']}")
    print(f"- node_bind_host: {config['node_bind_host']}")
    print(f"- node_port: {config['node_port']}")
    print(f"- peer_sync_enabled: {config['peer_sync_enabled']}")
    print(f"- peer_sync_interval_seconds: {config['peer_sync_interval_seconds']}")
    print(f"- peer_request_timeout_seconds: {config['peer_request_timeout_seconds']}")
    print(f"- max_peers: {config['max_peers']}")


def quick_profile_menu(config: dict) -> dict:
    print("\nProfile:")
    print("1 = Ausgeglichen, CPU-Testnet")
    print("2 = Leichter / schneller")
    print("3 = Etwas schwerer")
    choice = input("Auswahl [1]: ").strip() or "1"

    if choice == "1":
        config.update({
            "start_difficulty": 4,
            "min_difficulty": 3,
            "max_difficulty": 5,
            "target_block_time_seconds": 35.0,
            "difficulty_adjustment_interval": 5,
            "increase_if_avg_below_seconds": 18.0,
            "decrease_if_avg_above_seconds": 75.0,
            "difficulty_v2_enabled": True,
            "difficulty_v2_target_block_time_seconds": 30.0,
            "difficulty_v2_adjustment_interval": 8,
            "difficulty_v2_min_bits": 18,
            "difficulty_v2_max_bits": 30,
            "difficulty_v2_increase_below_seconds": 18.0,
            "difficulty_v2_decrease_above_seconds": 54.0,
            "difficulty_v3_enabled": True,
            "difficulty_v3_target_block_time_seconds": 30.0,
            "difficulty_v3_adjustment_interval": 4,
            "difficulty_v3_min_bits": 18,
            "difficulty_v3_max_bits": 42,
            "difficulty_v3_increase_below_seconds": 24.0,
            "difficulty_v3_decrease_above_seconds": 45.0,
            "difficulty_v3_max_step_up_bits": 6,
            "difficulty_v3_max_step_down_bits": 4,
            "block_reward": 50.0,
            "max_transactions_per_block": 25,
            "min_tx_fee": 0.01
        })
    elif choice == "2":
        config.update({
            "start_difficulty": 4,
            "min_difficulty": 3,
            "max_difficulty": 4,
            "target_block_time_seconds": 25.0,
            "difficulty_adjustment_interval": 5,
            "increase_if_avg_below_seconds": 12.0,
            "decrease_if_avg_above_seconds": 55.0,
            "block_reward": 50.0,
            "max_transactions_per_block": 25,
            "min_tx_fee": 0.01
        })
    elif choice == "3":
        config.update({
            "start_difficulty": 5,
            "min_difficulty": 4,
            "max_difficulty": 5,
            "target_block_time_seconds": 45.0,
            "difficulty_adjustment_interval": 5,
            "increase_if_avg_below_seconds": 20.0,
            "decrease_if_avg_above_seconds": 90.0,
            "block_reward": 50.0,
            "max_transactions_per_block": 25,
            "min_tx_fee": 0.01
        })
    else:
        print("Ungültig, Profil 1 wird genutzt.")
        config.update(DEFAULT_CONFIG)

    return config


def manual_menu(config: dict) -> dict:
    print("\nManuelle Regeln einstellen")
    config["min_difficulty"] = ask_int("Min Difficulty", int(config["min_difficulty"]), 1, 12)
    config["max_difficulty"] = ask_int("Max Difficulty", int(config["max_difficulty"]), config["min_difficulty"], 12)
    config["start_difficulty"] = ask_int("Start Difficulty", int(config["start_difficulty"]), config["min_difficulty"], config["max_difficulty"])
    config["target_block_time_seconds"] = ask_float("Ziel-Blockzeit Sekunden", float(config["target_block_time_seconds"]), 1.0)
    config["difficulty_adjustment_interval"] = ask_int("Difficulty-Anpassung alle X Blöcke", int(config["difficulty_adjustment_interval"]), 1, 100)
    config["increase_if_avg_below_seconds"] = ask_float("Difficulty erhöhen wenn Ø-Zeit unter Sekunden", float(config["increase_if_avg_below_seconds"]), 0.1)
    config["decrease_if_avg_above_seconds"] = ask_float("Legacy Difficulty senken wenn Ø-Zeit über Sekunden", float(config["decrease_if_avg_above_seconds"]), 1.0)

    print("\nBit-Difficulty v2 – neue Blöcke")
    config["difficulty_v2_enabled"] = ask_bool(
        "Bit-Difficulty v2 aktiv",
        bool(config["difficulty_v2_enabled"]),
    )
    config["difficulty_v2_target_block_time_seconds"] = ask_float(
        "Ziel-Blockzeit v2 in Sekunden",
        float(config["difficulty_v2_target_block_time_seconds"]),
        1.0,
    )
    config["difficulty_v2_adjustment_interval"] = ask_int(
        "v2-Anpassung alle X Blöcke",
        int(config["difficulty_v2_adjustment_interval"]),
        2,
        100,
    )
    config["difficulty_v2_min_bits"] = ask_int(
        "Min Difficulty Bits",
        int(config["difficulty_v2_min_bits"]),
        4,
        256,
    )
    config["difficulty_v2_max_bits"] = ask_int(
        "Max Difficulty Bits",
        int(config["difficulty_v2_max_bits"]),
        int(config["difficulty_v2_min_bits"]),
        256,
    )
    config["difficulty_v2_increase_below_seconds"] = ask_float(
        "Bits erhöhen wenn Ø-Blockzeit unter Sekunden",
        float(config["difficulty_v2_increase_below_seconds"]),
        0.1,
    )
    config["difficulty_v2_decrease_above_seconds"] = ask_float(
        "Bits senken wenn Ø-Blockzeit über Sekunden",
        float(config["difficulty_v2_decrease_above_seconds"]),
        1.0,
    )

    print("\nDifficulty v3-fast – neue Blöcke")
    config["difficulty_v3_enabled"] = ask_bool(
        "Difficulty v3-fast aktiv",
        bool(config["difficulty_v3_enabled"]),
    )
    config["difficulty_v3_target_block_time_seconds"] = ask_float(
        "v3 Ziel-Blockzeit in Sekunden",
        float(config["difficulty_v3_target_block_time_seconds"]),
        1.0,
    )
    config["difficulty_v3_adjustment_interval"] = ask_int(
        "v3 Anpassung alle X Blöcke",
        int(config["difficulty_v3_adjustment_interval"]),
        2,
        100,
    )
    config["difficulty_v3_min_bits"] = ask_int(
        "v3 Minimum Bits",
        int(config["difficulty_v3_min_bits"]),
        4,
        62,
    )
    config["difficulty_v3_max_bits"] = ask_int(
        "v3 Maximum Bits",
        int(config["difficulty_v3_max_bits"]),
        int(config["difficulty_v3_min_bits"]),
        62,
    )
    config["difficulty_v3_increase_below_seconds"] = ask_float(
        "v3 erhöhen unter Ø-Sekunden",
        float(config["difficulty_v3_increase_below_seconds"]),
        0.1,
    )
    config["difficulty_v3_decrease_above_seconds"] = ask_float(
        "v3 senken über Ø-Sekunden",
        float(config["difficulty_v3_decrease_above_seconds"]),
        1.0,
    )
    config["difficulty_v3_max_step_up_bits"] = ask_int(
        "v3 maximaler Sprung nach oben in Bits",
        int(config["difficulty_v3_max_step_up_bits"]),
        1,
        12,
    )
    config["difficulty_v3_max_step_down_bits"] = ask_int(
        "v3 maximaler Sprung nach unten in Bits",
        int(config["difficulty_v3_max_step_down_bits"]),
        1,
        12,
    )

    print("\nPublic-Testnet Seed-Nodes")
    current_seeds = ", ".join(config.get("seed_nodes", []))
    seed_text = input(
        f"Seed-Nodes, mit Komma getrennt [{current_seeds}]: "
    ).strip()
    if seed_text:
        config["seed_nodes"] = [
            item.strip()
            for item in seed_text.split(",")
            if item.strip()
        ]

    # Diese Sicherheitswerte sind für das öffentliche Testnet fest.
    config["require_signed_transactions"] = True
    config["allow_legacy_unsigned_test_wallet"] = False

    config["block_reward"] = ask_float("Blockreward LOGIC", float(config["block_reward"]), 0.0)
    config["max_transactions_per_block"] = ask_int("Max Transaktionen pro Block", int(config["max_transactions_per_block"]), 0, 1000)
    config["min_tx_fee"] = ask_float("Mindest-Fee pro TX", float(config["min_tx_fee"]), 0.0)

    print("\nLAN-Testnet / Peer-Sync")
    config["node_port"] = ask_int("Node TCP-Port", int(config["node_port"]), 1, 65535)
    config["peer_sync_enabled"] = ask_bool("Automatische Peer-Synchronisierung", bool(config["peer_sync_enabled"]))
    config["peer_sync_interval_seconds"] = ask_float(
        "Peer-Sync Intervall in Sekunden",
        float(config["peer_sync_interval_seconds"]),
        1.0,
    )
    config["peer_request_timeout_seconds"] = ask_float(
        "Peer-Timeout in Sekunden",
        float(config["peer_request_timeout_seconds"]),
        1.0,
    )
    config["max_peers"] = ask_int("Maximale Peers", int(config["max_peers"]), 1, 256)
    return config


def main() -> None:
    config = load_config()

    while True:
        print("\n" + "=" * 72)
        print("Logicoin / LOGIC Config Editor v0.12.15.3")
        print("=" * 72)
        print("1 = Config anzeigen")
        print("2 = Schnell-Profil wählen")
        print("3 = Manuell einstellen")
        print("4 = Standard-Config wiederherstellen")
        print("0 = Beenden")

        choice = input("\nAuswahl: ").strip()

        if choice == "1":
            print_config(config)
            input("\nEnter...")
        elif choice == "2":
            config = quick_profile_menu(config)
            save_config(config)
            config = load_config()
            print("\nConfig gespeichert. Node danach neu starten.")
            input("\nEnter...")
        elif choice == "3":
            config = manual_menu(config)
            save_config(config)
            config = load_config()
            print("\nConfig gespeichert. Node danach neu starten.")
            input("\nEnter...")
        elif choice == "4":
            confirm = input("Wirklich Standard wiederherstellen? JA eingeben: ").strip()
            if confirm == "JA":
                save_config(dict(DEFAULT_CONFIG))
                config = load_config()
                print("Standard-Config wiederhergestellt. Node danach neu starten.")
            else:
                print("Abgebrochen.")
            input("\nEnter...")
        elif choice == "0":
            print("Config Editor beendet.")
            break
        else:
            print("Ungültige Auswahl.")


if __name__ == "__main__":
    main()
