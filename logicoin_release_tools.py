#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import platform
import shutil
import sys
import urllib.request
import zipfile
from pathlib import Path
from typing import Any

from logicoin_core import (
    NETWORK_ID,
    NETWORK_NAME,
    RELEASE_CHANNEL,
    VERSION,
    create_genesis_block,
    load_chain,
    load_config,
    validate_chain,
)

from logicoin_public_network import (
    PUBLIC_NETWORK_FILE,
    public_asset_registry,
    validate_public_network,
)

BASE_DIR = (
    Path(sys.executable).resolve().parent
    if getattr(sys, "frozen", False)
    else Path(__file__).resolve().parent
)
WALLET_FILE = BASE_DIR / "logic_wallet.json"
BACKUP_DIR = BASE_DIR / "backups"
DIAGNOSTICS_DIR = BASE_DIR / "diagnostics"

SENSITIVE_KEYS = {
    "private_key",
    "wallet_secret_for_future_signatures",
    "seed_phrase",
    "mnemonic",
    "password",
    "passphrase",
}


def now_stamp() -> str:
    return dt.datetime.now().strftime("%Y%m%d_%H%M%S")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(
        json.dumps(data, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    os.replace(temp, path)


def redact(value: Any) -> Any:
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, item in value.items():
            lower = str(key).lower()
            if lower in SENSITIVE_KEYS or "private" in lower or "secret" in lower:
                result[key] = "<REDACTED>"
            else:
                result[key] = redact(item)
        return result

    if isinstance(value, list):
        return [redact(item) for item in value]

    return value


def validate_wallet(wallet: Any) -> tuple[bool, str]:
    if not isinstance(wallet, dict):
        return False, "Wallet-Datei ist kein JSON-Objekt."

    required = (
        "address",
        "public_key",
        "private_key",
        "network_id",
    )
    missing = [
        key
        for key in required
        if not str(wallet.get(key, "")).strip()
    ]
    if missing:
        return False, "Wallet-Felder fehlen: " + ", ".join(missing)

    if str(wallet.get("network_id")) != NETWORK_ID:
        return False, (
            "Wallet gehört zu einem anderen Netzwerk: "
            f"{wallet.get('network_id')}."
        )

    return True, "Wallet gültig."


def backup_wallet(
    source: Path | None = None,
    backup_dir: Path | None = None,
) -> Path:
    source = source or WALLET_FILE
    backup_dir = backup_dir or BACKUP_DIR

    if not source.exists():
        raise FileNotFoundError(
            f"Keine Wallet gefunden: {source}"
        )

    wallet = read_json(source)
    ok, reason = validate_wallet(wallet)
    if not ok:
        raise ValueError(reason)

    backup_dir.mkdir(parents=True, exist_ok=True)
    address = str(wallet["address"]).replace("/", "_")[:32]
    target = backup_dir / (
        f"logic_wallet_{NETWORK_ID}_{address}_{now_stamp()}.json"
    )
    shutil.copy2(source, target)

    try:
        os.chmod(target, 0o600)
    except OSError:
        pass

    checksum = sha256_file(target)
    target.with_suffix(target.suffix + ".sha256").write_text(
        checksum + "  " + target.name + "\n",
        encoding="utf-8",
    )

    return target


def restore_wallet(
    backup: Path,
    target: Path | None = None,
) -> Path:
    target = target or WALLET_FILE
    wallet = read_json(backup)
    ok, reason = validate_wallet(wallet)

    if not ok:
        raise ValueError(reason)

    if target.exists():
        backup_wallet(
            source=target,
            backup_dir=target.parent / "backups",
        )

    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(backup, target)

    try:
        os.chmod(target, 0o600)
    except OSError:
        pass

    return target


def chain_diagnostics() -> dict[str, Any]:
    chain = load_chain()
    valid, reason = validate_chain(chain)
    tip = chain[-1]

    block_summaries = []
    for block in chain[-20:]:
        block_summaries.append({
            "index": block.get("index"),
            "timestamp": block.get("timestamp"),
            "hash": block.get("hash"),
            "previous_hash": block.get("previous_hash"),
            "difficulty_rule": block.get("difficulty_rule", "genesis"),
            "difficulty_bits": block.get("difficulty_bits"),
            "miner_address": block.get("miner_address"),
            "transaction_count": len(block.get("transactions", [])),
        })

    return {
        "network_id": NETWORK_ID,
        "network_name": NETWORK_NAME,
        "version": VERSION,
        "valid": valid,
        "validation_reason": reason,
        "blocks": len(chain),
        "height": tip.get("index"),
        "genesis_hash": chain[0].get("hash"),
        "tip_hash": tip.get("hash"),
        "last_blocks": block_summaries,
    }


def fetch_local_node_info() -> dict[str, Any]:
    try:
        with urllib.request.urlopen(
            "http://127.0.0.1:8080/info",
            timeout=2,
        ) as response:
            return json.loads(
                response.read().decode("utf-8")
            )
    except Exception as exc:
        return {
            "ok": False,
            "error": str(exc),
        }


def export_diagnostics(
    output_dir: Path | None = None,
) -> Path:
    output_dir = output_dir or DIAGNOSTICS_DIR
    output_dir.mkdir(parents=True, exist_ok=True)

    work = output_dir / (
        f"logicoin_diagnostics_{now_stamp()}"
    )
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)

    write_json(work / "system.json", {
        "platform": platform.platform(),
        "python": sys.version,
        "executable": str(sys.executable),
        "network_id": NETWORK_ID,
        "network_name": NETWORK_NAME,
        "release_channel": RELEASE_CHANNEL,
        "version": VERSION,
    })
    write_json(
        work / "chain_summary.json",
        chain_diagnostics(),
    )
    write_json(
        work / "node_info.json",
        redact(fetch_local_node_info()),
    )
    write_json(
        work / "config_redacted.json",
        redact(load_config()),
    )
    write_json(
        work / "public_asset_registry.json",
        redact(public_asset_registry()),
    )

    wallet = read_json(WALLET_FILE)
    if isinstance(wallet, dict):
        write_json(
            work / "wallet_public.json",
            {
                "wallet_version": wallet.get("wallet_version"),
                "network_id": wallet.get("network_id"),
                "address": wallet.get("address"),
                "public_key": wallet.get("public_key"),
                "created_at": wallet.get("created_at"),
            },
        )

    copy_names = (
        "logicoin_release.json",
        "logicoin_peers.json",
        "logicoin_peer_status.json",
        "logicoin_node_identity.json",
        "logicoin_gpu_miner_stats.json",
        "logicoin_gpu_miner_stats_gpu0.json",
        "logicoin_gpu_miner_stats_gpu1.json",
        "logicoin_cpu_miner_stats.json",
    )
    for name in copy_names:
        source = BASE_DIR / name
        if source.exists():
            data = read_json(source)
            if data is not None:
                write_json(
                    work / name,
                    redact(data),
                )

    log_names = (
        "logicoin_node.log",
        "logicoin_cpu_miner.log",
        "logicoin_external_miner.log",
    )
    for name in log_names:
        source = BASE_DIR / name
        if not source.exists():
            continue

        lines = source.read_text(
            encoding="utf-8",
            errors="replace",
        ).splitlines()
        (work / name).write_text(
            "\n".join(lines[-2000:]) + "\n",
            encoding="utf-8",
        )

    archive = output_dir / (
        f"logicoin_diagnostics_{now_stamp()}.zip"
    )
    with zipfile.ZipFile(
        archive,
        "w",
        compression=zipfile.ZIP_DEFLATED,
    ) as bundle:
        for file in sorted(work.rglob("*")):
            if file.is_file():
                bundle.write(
                    file,
                    arcname=file.relative_to(work),
                )

    shutil.rmtree(work, ignore_errors=True)
    return archive


def readiness_report() -> dict[str, Any]:
    config = load_config()
    chain = load_chain()
    chain_ok, chain_reason = validate_chain(chain)
    genesis = create_genesis_block()
    wallet = read_json(WALLET_FILE)

    checks: list[dict[str, Any]] = []

    def add(name: str, ok: bool, detail: str) -> None:
        checks.append({
            "name": name,
            "ok": bool(ok),
            "detail": detail,
        })

    add(
        "Version",
        VERSION == "0.12.15.3",
        VERSION,
    )
    add(
        "Netzwerk-ID",
        config.get("network_id") == NETWORK_ID,
        str(config.get("network_id")),
    )
    add(
        "Public-Testnet-Modus",
        bool(config.get("public_testnet")),
        str(config.get("public_testnet")),
    )

    try:
        registry = public_asset_registry()
        registry_ok, registry_errors, registry_warnings = (
            validate_public_network(registry)
        )
        add(
            "Public-Network-JSON",
            registry_ok,
            (
                "gültig"
                if registry_ok
                else "; ".join(registry_errors)
            ),
        )
        add(
            "Bridge deaktiviert bis Deployment",
            not bool(
                registry.get(
                    "bridge",
                    {},
                ).get(
                    "enabled",
                    False,
                )
            ),
            str(
                registry.get(
                    "bridge",
                    {},
                ).get(
                    "enabled",
                    False,
                )
            ),
        )
    except Exception as exc:
        add(
            "Public-Network-JSON",
            False,
            str(exc),
        )
    add(
        "Signierte Transaktionen",
        bool(config.get("require_signed_transactions")),
        str(config.get("require_signed_transactions")),
    )
    add(
        "Legacy-Testwallet deaktiviert",
        not bool(config.get("allow_legacy_unsigned_test_wallet")),
        str(config.get("allow_legacy_unsigned_test_wallet")),
    )
    add(
        "Chain gültig",
        chain_ok,
        chain_reason,
    )
    add(
        "Genesis",
        chain[0].get("hash") == genesis.get("hash"),
        str(chain[0].get("hash")),
    )
    add(
        "Genesis-Netzwerk",
        chain[0].get("network_id") == NETWORK_ID,
        str(chain[0].get("network_id")),
    )

    if wallet is None:
        add(
            "Wallet",
            True,
            "Noch keine Wallet – wird vom Tester erstellt.",
        )
    else:
        wallet_ok, wallet_reason = validate_wallet(wallet)
        add(
            "Wallet",
            wallet_ok,
            wallet_reason,
        )

    seed_nodes = config.get("seed_nodes", [])
    add(
        "Seed-Nodes",
        bool(seed_nodes),
        (
            ", ".join(seed_nodes)
            if seed_nodes
            else "Noch kein öffentlicher Seed-Node konfiguriert."
        ),
    )

    critical_names = {
        "Version",
        "Netzwerk-ID",
        "Public-Testnet-Modus",
        "Public-Network-JSON",
        "Bridge deaktiviert bis Deployment",
        "Signierte Transaktionen",
        "Legacy-Testwallet deaktiviert",
        "Chain gültig",
        "Genesis",
        "Genesis-Netzwerk",
        "Wallet",
    }
    critical_ok = all(
        item["ok"]
        for item in checks
        if item["name"] in critical_names
    )

    return {
        "ok": critical_ok,
        "network_id": NETWORK_ID,
        "network_name": NETWORK_NAME,
        "release_channel": RELEASE_CHANNEL,
        "version": VERSION,
        "checks": checks,
    }


def print_readiness() -> bool:
    report = readiness_report()

    print("=" * 72)
    print(f"{NETWORK_NAME} – Release Readiness")
    print("=" * 72)

    for item in report["checks"]:
        marker = "OK" if item["ok"] else "WARN/FEHLER"
        print(
            f"[{marker:<11}] {item['name']}: "
            f"{item['detail']}"
        )

    print("=" * 72)
    print(
        "KRITISCHE PRÜFUNGEN BESTANDEN"
        if report["ok"]
        else "KRITISCHE PRÜFUNGEN NICHT BESTANDEN"
    )
    return bool(report["ok"])


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Logicoin Public Testnet RC1 "
            "Backup-, Diagnose- und Release-Werkzeuge"
        )
    )
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("backup-wallet")
    restore = sub.add_parser("restore-wallet")
    restore.add_argument("backup")
    sub.add_parser("diagnostics")
    sub.add_parser("readiness")

    args = parser.parse_args()

    if args.command == "backup-wallet":
        result = backup_wallet()
        print(f"Wallet-Backup erstellt: {result}")
        return 0

    if args.command == "restore-wallet":
        result = restore_wallet(
            Path(args.backup).resolve()
        )
        print(f"Wallet wiederhergestellt: {result}")
        return 0

    if args.command == "diagnostics":
        result = export_diagnostics()
        print(f"Diagnosepaket erstellt: {result}")
        return 0

    if args.command == "readiness":
        return 0 if print_readiness() else 2

    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
