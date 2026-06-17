#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Logicoin / LOGIC Wallet v0.8

Kann:
- Test-Wallet erstellen
- Mining-Testadresse benutzen
- Balance vom Node abfragen
- Transaktion erstellen
- Transaktion an Node-Mempool senden

Hinweis:
Noch keine echte Kryptosignatur. Lokales Lern-Testnet.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict

from logicoin_core import (
    PROJECT_NAME,
    COIN_NAME,
    TICKER,
    NETWORK_ID,
    NETWORK_NAME,
    DEFAULT_MINER_ADDRESS,
    create_transfer_transaction,
    generate_keypair_wallet,
)

WALLET_VERSION = "0.12.15.3"
DEFAULT_NODE_URL = "http://127.0.0.1:8080"
WALLET_FILE = Path(__file__).with_name("logic_wallet.json")


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


def ask_text(prompt: str, default: str = "") -> str:
    if default:
        text = input(f"{prompt} [{default}]: ").strip()
        return text if text else default
    return input(f"{prompt}: ").strip()


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

        return round(value, 8)


def press_enter() -> None:
    input("\nEnter drücken, um zurück zum Menü zu gehen...")


def generate_wallet() -> Dict[str, Any]:
    return generate_keypair_wallet(WALLET_VERSION)


def create_test_mining_wallet() -> Dict[str, Any]:
    wallet = generate_keypair_wallet(WALLET_VERSION)
    wallet["note"] = (
        "Signierte Public-Testnet-Mining-Wallet. "
        "Private Key geheim halten und sichern."
    )
    return wallet


def load_wallet() -> Dict[str, Any] | None:
    if not WALLET_FILE.exists():
        return None

    try:
        with WALLET_FILE.open("r", encoding="utf-8") as f:
            wallet = json.load(f)
        if (
            isinstance(wallet, dict)
            and wallet.get("address")
            and wallet.get("network_id") == NETWORK_ID
        ):
            return wallet
    except Exception:
        return None

    return None


def save_wallet(wallet: Dict[str, Any]) -> None:
    with WALLET_FILE.open("w", encoding="utf-8") as f:
        json.dump(wallet, f, indent=2, ensure_ascii=False)


def ensure_wallet() -> Dict[str, Any] | None:
    wallet = load_wallet()
    if wallet:
        return wallet

    print("\nNoch keine Wallet-Datei gefunden.")
    print("1 = Neue signierte Public-Testnet-Wallet erstellen")
    print("2 = Neue signierte Mining-Wallet erstellen")
    print("0 = Abbrechen")
    choice = input("Auswahl: ").strip()

    if choice == "1":
        wallet = generate_wallet()
        save_wallet(wallet)
        print(f"\nNeue Wallet erstellt: {wallet['address']}")
        return wallet

    if choice == "2":
        wallet = create_test_mining_wallet()
        save_wallet(wallet)
        print(f"\nMining-Testwallet erstellt: {wallet['address']}")
        return wallet

    return None


def show_wallet(wallet: Dict[str, Any]) -> None:
    print("\nWallet:")
    print(f"- Datei: {WALLET_FILE}")
    print(f"- Coin: {wallet.get('coin')} / {wallet.get('ticker')}")
    print(f"- Adresse: {wallet.get('address')}")
    print(f"- Signatur: {'aktiv' if wallet.get('private_key') and wallet.get('public_key') else 'Legacy/ohne Private Key'}")
    print(f"- Public Key: {wallet.get('public_key', '-')}")
    print(f"- Hinweis: {wallet.get('note')}")


def show_node_info(node_url: str) -> None:
    try:
        info = get_json(node_url + "/info")
    except Exception as e:
        print(f"\nNode nicht erreichbar: {e}")
        return

    print("\nNode:")
    print(f"- Version: {info.get('version')}")
    print(f"- Höhe: #{info.get('height')}")
    print(f"- Blöcke: {info.get('blocks_total')}")
    print(f"- Mempool: {info.get('mempool_count')} TXs")
    print(f"- Chain gültig: {info.get('chain_valid')} - {info.get('chain_status')}")


def get_address_info(node_url: str, address: str) -> Dict[str, Any] | None:
    try:
        encoded = urllib.parse.quote(address)
        data = get_json(node_url + f"/address?address={encoded}")
    except Exception as e:
        print(f"\nNode nicht erreichbar: {e}")
        return None

    if not data.get("ok"):
        print(data)
        return None

    return data.get("address_info")


def show_balance(node_url: str, wallet: Dict[str, Any]) -> None:
    address = wallet["address"]
    info = get_address_info(node_url, address)
    if not info:
        return

    print("\nBalance:")
    print(f"- Adresse: {address}")
    print(f"- Bestätigt: {float(info.get('confirmed_balance', 0.0)):.8f} {TICKER}")
    print(f"- Ausstehend rein: {float(info.get('pending_in', 0.0)):.8f} {TICKER}")
    print(f"- Ausstehend raus: {float(info.get('pending_out', 0.0)):.8f} {TICKER}")
    print(f"- Ausstehende Fees: {float(info.get('pending_fees', 0.0)):.8f} {TICKER}")
    print(f"- Spendable: {float(info.get('spendable_balance', 0.0)):.8f} {TICKER}")
    print(f"- Nächster Nonce: {info.get('next_nonce')}")


def send_transaction(node_url: str, wallet: Dict[str, Any]) -> None:
    from_addr = wallet["address"]
    address_info = get_address_info(node_url, from_addr)

    if not address_info:
        return

    print("\nNeue LOGIC-Transaktion")
    print(f"Von: {from_addr}")
    print(f"Spendable: {float(address_info.get('spendable_balance', 0.0)):.8f} {TICKER}")
    print(f"Nächster Nonce: {address_info.get('next_nonce')}")

    to_addr = ask_text("Empfänger-Adresse")
    amount = ask_float("Amount LOGIC", 1.0, minimum=0.00000001)
    fee = ask_float("Fee LOGIC", 0.01, minimum=0.0)
    memo = ask_text("Memo optional", "")

    nonce = int(address_info.get("next_nonce", 0))

    private_key = wallet.get("private_key")
    public_key = wallet.get("public_key", "")

    if not private_key and from_addr != DEFAULT_MINER_ADDRESS:
        print("\nDiese Wallet hat keinen Private Key und kann keine v0.12.15.3-Signatur erstellen.")
        print("Erstelle eine neue v0.12.15.3-Wallet.")
        return

    tx = create_transfer_transaction(
        from_address=from_addr,
        to_address=to_addr,
        amount=amount,
        fee=fee,
        nonce=nonce,
        memo=memo,
        public_key=public_key,
        private_key=private_key,
    )

    print("\nTX-Vorschau:")
    print(f"- TXID: {tx['txid']}")
    print(f"- Von: {tx['from']}")
    print(f"- An: {tx['to']}")
    print(f"- Amount: {tx['amount']:.8f} {TICKER}")
    print(f"- Fee: {tx['fee']:.8f} {TICKER}")
    print(f"- Nonce: {tx['nonce']}")
    print(f"- Signatur: {'ja' if tx.get('signature') else 'Legacy/keine'}")

    confirm = input("Senden? JA eingeben: ").strip()
    if confirm != "JA":
        print("Abgebrochen.")
        return

    response = post_json(node_url + "/submit_tx", tx)

    if response.get("accepted"):
        print("\nTransaktion wurde in den Mempool aufgenommen.")
        print(f"- TXID: {response.get('txid')}")
        print("Jetzt muss ein Miner einen neuen Block minen, damit sie bestätigt wird.")
    else:
        print("\nTransaktion abgelehnt.")
        print(f"Grund: {response.get('error')}")


def show_mempool(node_url: str) -> None:
    try:
        data = get_json(node_url + "/mempool")
    except Exception as e:
        print(f"\nNode nicht erreichbar: {e}")
        return

    print("\nMempool:")
    print(f"- Anzahl: {data.get('mempool_count')} TXs")

    for tx in data.get("transactions", []):
        print("-" * 72)
        print(f"TXID: {tx.get('txid')}")
        print(f"Von: {tx.get('from')}")
        print(f"An: {tx.get('to')}")
        print(f"Amount: {float(tx.get('amount', 0.0)):.8f} {TICKER}")
        print(f"Fee: {float(tx.get('fee', 0.0)):.8f} {TICKER}")
        print(f"Nonce: {tx.get('nonce')}")
        if tx.get("memo"):
            print(f"Memo: {tx.get('memo')}")


def main_menu() -> None:
    node_url = normalize_node_url(ask_text("Node-URL", DEFAULT_NODE_URL))
    wallet = ensure_wallet()

    if not wallet:
        print("Keine Wallet geladen.")
        return

    while True:
        print("\n" + "=" * 72)
        print(f"{PROJECT_NAME} / {COIN_NAME} Wallet v{WALLET_VERSION}")
        print("=" * 72)
        print(f"Node: {node_url}")
        print(f"Wallet: {wallet['address']}")
        print("1 = Wallet anzeigen")
        print("2 = Node-Info anzeigen")
        print("3 = Balance anzeigen")
        print("4 = LOGIC senden")
        print("5 = Mempool anzeigen")
        print("6 = Node-URL ändern")
        print("7 = Wallet neu erstellen/wechseln")
        print("0 = Beenden")

        choice = input("\nAuswahl: ").strip()

        if choice == "1":
            show_wallet(wallet)
            press_enter()
        elif choice == "2":
            show_node_info(node_url)
            press_enter()
        elif choice == "3":
            show_balance(node_url, wallet)
            press_enter()
        elif choice == "4":
            send_transaction(node_url, wallet)
            press_enter()
        elif choice == "5":
            show_mempool(node_url)
            press_enter()
        elif choice == "6":
            node_url = normalize_node_url(ask_text("Node-URL", node_url))
        elif choice == "7":
            print("1 = Neue zufällige Wallet")
            print("2 = Mining-Testadresse")
            print("0 = Abbrechen")
            sub = input("Auswahl: ").strip()
            if sub == "1":
                wallet = generate_wallet()
                save_wallet(wallet)
                print(f"Neue Wallet gespeichert: {wallet['address']}")
            elif sub == "2":
                wallet = create_test_mining_wallet()
                save_wallet(wallet)
                print(f"Mining-Testwallet gespeichert: {wallet['address']}")
            else:
                print("Abgebrochen.")
            press_enter()
        elif choice == "0":
            print("\nWallet beendet.")
            break
        else:
            print("Ungültige Auswahl.")


def main() -> None:
    main_menu()


if __name__ == "__main__":
    main()
