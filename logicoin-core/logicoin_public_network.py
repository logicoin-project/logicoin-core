#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

from logicoin_core import (
    NETWORK_ID,
    NETWORK_NAME,
    RELEASE_CHANNEL,
    VERSION,
    create_genesis_block,
)

BASE_DIR = (
    Path(sys.executable).resolve().parent
    if getattr(sys, "frozen", False)
    else Path(__file__).resolve().parent
)
PUBLIC_NETWORK_FILE = (
    BASE_DIR / "logicoin_public_network.json"
)


def default_public_network() -> dict[str, Any]:
    """
    Eingebetteter sicherer Fallback.

    Dadurch bleibt der Node erreichbar, selbst wenn beim EXE-Build die
    externe JSON-Datei versehentlich nicht neben die EXE kopiert wurde.
    Bridge und Wrapped Token bleiben dabei ausdrücklich deaktiviert.
    """
    block_reward = 50.0
    target_seconds = 30.0

    return {
        "schema_version": 1,
        "project": "Logicoin",
        "coin_name": "Logicoin",
        "native_symbol": "LOGIC",
        "network_id": NETWORK_ID,
        "network_name": NETWORK_NAME,
        "release_channel": RELEASE_CHANNEL,
        "native_asset": {
            "enabled": True,
            "type": "native-coin",
            "name": "Logicoin",
            "symbol": "LOGIC",
            "decimals": 8,
            "genesis_hash": create_genesis_block()["hash"],
            "rpc_urls": [
                "http://127.0.0.1:8080"
            ],
            "public_seed_nodes": [],
            "explorer_url": "",
        },
        "wrapped_token": {
            "enabled": False,
            "type": "wrapped-token",
            "name": "Wrapped Logicoin",
            "symbol": "wLOGIC",
            "standard": "ERC-20",
            "host_chain": "",
            "chain_id": None,
            "contract_address": "",
            "decimals": 8,
        },
        "bridge": {
            "enabled": False,
            "mode": "lock-native-mint-wrapped",
            "conversion_ratio": "1:1",
            "native_custody_address": "",
            "wrapped_burn_address": "",
            "minimum_native_confirmations": 20,
            "minimum_host_chain_confirmations": 20,
            "operator_model": "multisig-required-before-public",
            "proof_of_reserve_enabled": False,
            "emergency_pause_enabled": True,
        },
        "economics": {
            "block_reward_logic": block_reward,
            "target_block_time_seconds": target_seconds,
            "estimated_daily_emission_logic": (
                block_reward
                * (86400.0 / target_seconds)
            ),
            "estimated_annual_emission_logic": (
                block_reward
                * (86400.0 / target_seconds)
                * 365.0
            ),
            "maximum_supply_logic": None,
            "halving_schedule": None,
            "launch_price_eur": None,
            "launch_market_cap_eur": None,
        },
        "status": {
            "native_public_testnet": True,
            "native_mainnet": False,
            "wrapped_token_deployed": False,
            "bridge_audited": False,
            "public_trading_enabled": False,
        },
        "notice": (
            "Eingebettete Public-Testnet-Fallback-Konfiguration. "
            "Wrapped Token und Bridge sind deaktiviert."
        ),
    }


def _candidate_public_network_files() -> list[Path]:
    candidates = [PUBLIC_NETWORK_FILE]

    meipass = getattr(
        sys,
        "_MEIPASS",
        None,
    )
    if meipass:
        candidates.append(
            Path(meipass)
            / "logicoin_public_network.json"
        )

    unique: list[Path] = []
    seen: set[str] = set()

    for candidate in candidates:
        key = str(candidate).lower()
        if key not in seen:
            seen.add(key)
            unique.append(candidate)

    return unique


def read_public_network() -> dict[str, Any]:
    for candidate in _candidate_public_network_files():
        if not candidate.exists():
            continue

        try:
            data = json.loads(
                candidate.read_text(
                    encoding="utf-8"
                )
            )
        except Exception:
            continue

        if isinstance(data, dict):
            data = dict(data)
            data["_registry_source"] = str(candidate)
            data["_registry_fallback"] = False
            return data

    data = default_public_network()
    data["_registry_source"] = "embedded-default"
    data["_registry_fallback"] = True
    return data


def validate_public_network(
    data: dict[str, Any] | None = None,
) -> tuple[bool, list[str], list[str]]:
    data = data or read_public_network()
    errors: list[str] = []
    warnings: list[str] = []

    if int(data.get("schema_version", 0)) != 1:
        errors.append(
            "schema_version muss 1 sein."
        )

    if str(data.get("network_id", "")) != NETWORK_ID:
        errors.append(
            "network_id stimmt nicht mit "
            f"{NETWORK_ID} überein."
        )

    if (
        str(data.get("network_name", ""))
        != NETWORK_NAME
    ):
        errors.append(
            "network_name stimmt nicht überein."
        )

    if (
        str(data.get("release_channel", ""))
        != RELEASE_CHANNEL
    ):
        errors.append(
            "release_channel stimmt nicht überein."
        )

    native = data.get("native_asset")
    if not isinstance(native, dict):
        errors.append(
            "native_asset fehlt oder ist ungültig."
        )
        native = {}

    if str(native.get("symbol", "")) != "LOGIC":
        errors.append(
            "Native Symbol muss LOGIC sein."
        )

    try:
        decimals = int(native.get("decimals", -1))
    except Exception:
        decimals = -1

    if not (0 <= decimals <= 18):
        errors.append(
            "Native decimals müssen zwischen 0 "
            "und 18 liegen."
        )

    seeds = native.get(
        "public_seed_nodes",
        [],
    )
    if not isinstance(seeds, list):
        errors.append(
            "public_seed_nodes muss eine Liste sein."
        )
        seeds = []

    for seed in seeds:
        text = str(seed).strip()
        if not (
            text.startswith("http://")
            or text.startswith("https://")
        ):
            errors.append(
                f"Ungültige Seed-Node-URL: {text}"
            )

    if not seeds:
        warnings.append(
            "Noch kein öffentlicher Seed-Node "
            "eingetragen."
        )

    wrapped = data.get("wrapped_token")
    if not isinstance(wrapped, dict):
        errors.append(
            "wrapped_token fehlt oder ist ungültig."
        )
        wrapped = {}

    bridge = data.get("bridge")
    if not isinstance(bridge, dict):
        errors.append(
            "bridge fehlt oder ist ungültig."
        )
        bridge = {}

    wrapped_enabled = bool(
        wrapped.get("enabled", False)
    )
    bridge_enabled = bool(
        bridge.get("enabled", False)
    )

    if wrapped_enabled:
        required = (
            "host_chain",
            "chain_id",
            "contract_address",
            "symbol",
        )
        for key in required:
            if wrapped.get(key) in {
                None,
                "",
            }:
                errors.append(
                    "Wrapped Token ist aktiviert, "
                    f"aber {key} fehlt."
                )

    if bridge_enabled:
        if not wrapped_enabled:
            errors.append(
                "Bridge kann nicht ohne aktivierten "
                "Wrapped Token aktiviert werden."
            )

        if (
            str(
                bridge.get(
                    "conversion_ratio",
                    "",
                )
            )
            != "1:1"
        ):
            errors.append(
                "Öffentliche Bridge muss für diese "
                "Version 1:1 arbeiten."
            )

        if not str(
            bridge.get(
                "native_custody_address",
                "",
            )
        ).strip():
            errors.append(
                "Bridge ist aktiviert, aber "
                "native_custody_address fehlt."
            )

        if (
            str(
                bridge.get(
                    "operator_model",
                    "",
                )
            )
            == "single-key"
        ):
            errors.append(
                "Single-Key-Bridge ist für die "
                "öffentliche Freigabe gesperrt."
            )

        if not bool(
            bridge.get(
                "emergency_pause_enabled",
                False,
            )
        ):
            errors.append(
                "Aktive Bridge benötigt eine "
                "Emergency-Pause."
            )

        if not bool(
            bridge.get(
                "proof_of_reserve_enabled",
                False,
            )
        ):
            warnings.append(
                "Bridge ist aktiv, aber Proof of "
                "Reserve ist deaktiviert."
            )

    economics = data.get("economics")
    if not isinstance(economics, dict):
        errors.append(
            "economics fehlt oder ist ungültig."
        )
        economics = {}

    if (
        economics.get("maximum_supply_logic")
        is None
    ):
        warnings.append(
            "Noch keine maximale LOGIC-Menge "
            "festgelegt."
        )

    if (
        economics.get("halving_schedule")
        is None
    ):
        warnings.append(
            "Noch kein Halving- oder "
            "Emissionsplan festgelegt."
        )

    return (
        len(errors) == 0,
        errors,
        warnings,
    )


def public_seed_nodes() -> list[str]:
    data = read_public_network()
    native = data.get("native_asset", {})
    seeds = native.get(
        "public_seed_nodes",
        [],
    )

    if not isinstance(seeds, list):
        return []

    return [
        str(seed).strip().rstrip("/")
        for seed in seeds
        if str(seed).strip()
    ]


def public_asset_registry() -> dict[str, Any]:
    data = read_public_network()
    ok, errors, warnings = (
        validate_public_network(data)
    )

    result = dict(data)
    result["validation"] = {
        "ok": ok,
        "errors": errors,
        "warnings": warnings,
    }
    return result


def update_seed_nodes(
    seeds: list[str],
) -> None:
    data = read_public_network()
    native = data.setdefault(
        "native_asset",
        {},
    )
    native["public_seed_nodes"] = [
        str(seed).strip().rstrip("/")
        for seed in seeds
        if str(seed).strip()
    ]

    temp = PUBLIC_NETWORK_FILE.with_suffix(
        ".json.tmp"
    )
    temp.write_text(
        json.dumps(
            data,
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    os.replace(
        temp,
        PUBLIC_NETWORK_FILE,
    )


def print_validation() -> bool:
    data = read_public_network()
    ok, errors, warnings = (
        validate_public_network(data)
    )

    print("=" * 72)
    print("Logicoin Public Network JSON")
    print("=" * 72)
    print(f"Datei: {PUBLIC_NETWORK_FILE}")
    print(f"Netzwerk: {NETWORK_NAME}")
    print(f"Netzwerk-ID: {NETWORK_ID}")
    print()

    for warning in warnings:
        print(f"[WARNUNG] {warning}")

    for error in errors:
        print(f"[FEHLER] {error}")

    if ok:
        print("[OK] JSON-Struktur ist gültig.")
    else:
        print(
            "[FEHLER] JSON-Struktur ist "
            "nicht freigabefähig."
        )

    return ok


def main() -> int:
    return 0 if print_validation() else 2


if __name__ == "__main__":
    raise SystemExit(main())
