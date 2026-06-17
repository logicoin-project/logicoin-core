#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import hashlib
import json
import shutil
import sys
import zipfile
from pathlib import Path
from typing import Any

from logicoin_core import (
    NETWORK_ID,
    NETWORK_NAME,
    RELEASE_CHANNEL,
    VERSION,
    create_genesis_block,
)
from logicoin_release_tools import readiness_report

BASE_DIR = Path(__file__).resolve().parent
RELEASE_ROOT = BASE_DIR / "release"
PACKAGE_NAME = (
    "Logicoin_Public_Testnet_RC1_"
    f"v{VERSION}"
)
STAGING_DIR = RELEASE_ROOT / PACKAGE_NAME
ZIP_PATH = RELEASE_ROOT / (
    PACKAGE_NAME + ".zip"
)

RUNTIME_EXCLUDES = {
    "logic_chain.json",
    "logic_mempool.json",
    "logicoin_peers.json",
    "logicoin_peer_status.json",
    "logicoin_node_identity.json",
    "logic_wallet.json",
    "logicoin_gpu_submit.lock",
    "logicoin_control_center_settings.json",
}

EXCLUDED_PREFIXES = (
    "logicoin_gpu_miner_stats",
    "logicoin_cpu_miner_stats",
    "logicoin_gpu_benchmark_results",
)

EXCLUDED_SUFFIXES = (
    ".log",
    ".pyc",
)

EXCLUDED_DIRS = {
    "release",
    "backups",
    "diagnostics",
    "__pycache__",
    "dist",
    "build",
}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    digest.update(path.read_bytes())
    return digest.hexdigest()


def should_copy(path: Path) -> bool:
    name = path.name

    if name in RUNTIME_EXCLUDES:
        return False
    if name.startswith(EXCLUDED_PREFIXES):
        return False
    if path.suffix.lower() in EXCLUDED_SUFFIXES:
        return False

    # Alte Entwicklungs- und Hotfix-Dokumente würden Tester verwirren.
    if name.startswith("README_v") and name not in {
        "README_v01215.txt",
        "README_v012151.txt",
    }:
        return False
    if name.startswith("JETZT_TESTEN_v") and name not in {
        "JETZT_TESTEN_v01215.txt",
        "JETZT_TESTEN_v012151.txt",
    }:
        return False
    if name in {
        "HOTFIX_INFO.txt",
        "PROJEKTSTAND.txt",
        "TEST_RELEASE_READINESS.txt",
        "JETZT_TESTEN.txt",
        "JETZT_STARTEN.txt",
    }:
        return False

    return True


def scan_for_private_keys(root: Path) -> list[str]:
    findings: list[str] = []

    for file in root.rglob("*"):
        if not file.is_file():
            continue

        if file.name == "logic_wallet.json":
            findings.append(str(file))
            continue

        if file.suffix.lower() not in {
            ".json", ".txt", ".py", ".bat", ".md"
        }:
            continue

        text = file.read_text(
            encoding="utf-8",
            errors="ignore",
        ).lower()

        if file.name.endswith(".json"):
            if '"private_key"' in text:
                findings.append(str(file))
            if "test_wallet_no_real_signature" in text:
                findings.append(str(file))

    return findings


def build_release() -> Path:
    report = readiness_report()
    if not report["ok"]:
        failed = [
            item["name"]
            for item in report["checks"]
            if not item["ok"]
            and item["name"] != "Seed-Nodes"
        ]
        raise RuntimeError(
            "Release-Readiness fehlgeschlagen: "
            + ", ".join(failed)
        )

    RELEASE_ROOT.mkdir(parents=True, exist_ok=True)
    if STAGING_DIR.exists():
        shutil.rmtree(STAGING_DIR)
    STAGING_DIR.mkdir(parents=True)

    for source in sorted(BASE_DIR.iterdir()):
        if source.is_dir():
            if source.name in EXCLUDED_DIRS:
                continue
            continue

        if not should_copy(source):
            continue

        shutil.copy2(
            source,
            STAGING_DIR / source.name,
        )

    # Jeder öffentliche Tester beginnt mit derselben sauberen Genesis.
    (STAGING_DIR / "logic_chain.json").write_text(
        json.dumps(
            [create_genesis_block()],
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (STAGING_DIR / "logic_mempool.json").write_text(
        "[]\n",
        encoding="utf-8",
    )

    config = json.loads(
        (BASE_DIR / "logicoin_config.json").read_text(
            encoding="utf-8"
        )
    )
    seeds = config.get("seed_nodes", [])
    (STAGING_DIR / "logicoin_peers.json").write_text(
        json.dumps(
            seeds if isinstance(seeds, list) else [],
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    notice = f"""
{NETWORK_NAME}
{'=' * len(NETWORK_NAME)}

Netzwerk-ID: {NETWORK_ID}
Version: {VERSION}
Release-Kanal: {RELEASE_CHANNEL}

Dies ist ein öffentliches Testnetz.
LOGIC-Testcoins besitzen keinen garantierten Geldwert.
Die Chain und alle Testguthaben können vor dem Mainnet zurückgesetzt werden.

WICHTIG:
- Keine alte Alpha-Chain in diesen Ordner kopieren.
- Eine neue Wallet im Control Center erstellen.
- Den Private Key niemals veröffentlichen.
- Vor Updates Wallet sichern.
- Ohne konfigurierten Seed-Node muss die Node-Adresse manuell eingetragen werden.
""".strip() + "\n"

    (STAGING_DIR / "PUBLIC_TESTNET_NOTICE.txt").write_text(
        notice,
        encoding="utf-8",
    )

    start_here = """
START HIER – Logicoin Public Testnet RC1

1. ZIP in einen neuen Ordner entpacken.
2. BUILD_LOGICOIN_APP_EXE.bat ausführen.
3. BUILD_CUDA_WORKER_SAFE.bat ausführen.
4. CHECK_PUBLIC_TESTNET_READINESS.bat ausführen.
5. Control Center starten.
6. Im Wallet-Tab eine neue signierte Wallet erstellen.
7. Wallet sofort sichern.
8. Node-Adresse beziehungsweise Seed-Node eintragen.
9. Erst danach Mining starten.

Keine Dateien aus einer alten Alpha-Chain übernehmen.
""".strip() + "\n"

    (STAGING_DIR / "START_HERE_PUBLIC_TESTNET.txt").write_text(
        start_here,
        encoding="utf-8",
    )

    findings = scan_for_private_keys(STAGING_DIR)
    if findings:
        raise RuntimeError(
            "Sensible Runtime-Dateien im Release gefunden: "
            + ", ".join(findings)
        )

    checksums: dict[str, str] = {}
    for file in sorted(STAGING_DIR.rglob("*")):
        if file.is_file():
            checksums[str(file.relative_to(STAGING_DIR))] = (
                sha256_file(file)
            )

    manifest: dict[str, Any] = {
        "project": "Logicoin",
        "ticker": "LOGIC",
        "version": VERSION,
        "network_id": NETWORK_ID,
        "network_name": NETWORK_NAME,
        "release_channel": RELEASE_CHANNEL,
        "public_testnet": True,
        "genesis_hash": create_genesis_block()["hash"],
        "files": checksums,
    }
    (STAGING_DIR / "PUBLIC_RELEASE_MANIFEST.json").write_text(
        json.dumps(
            manifest,
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    if ZIP_PATH.exists():
        ZIP_PATH.unlink()

    with zipfile.ZipFile(
        ZIP_PATH,
        "w",
        compression=zipfile.ZIP_DEFLATED,
    ) as archive:
        for file in sorted(STAGING_DIR.rglob("*")):
            if file.is_file():
                archive.write(
                    file,
                    arcname=(
                        Path(PACKAGE_NAME)
                        / file.relative_to(STAGING_DIR)
                    ),
                )

    return ZIP_PATH


def main() -> int:
    result = build_release()
    print(f"Public-Testnet-Paket erstellt: {result}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
