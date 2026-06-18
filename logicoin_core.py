#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Logicoin / LOGIC Core v0.8

Neu:
- Wallet-/Adress-Balances mit Transfers
- Transaktionen
- Mempool
- Miner nimmt Mempool-Transaktionen in Blöcke auf
- Gebühren gehen an den Block-Miner

Hinweis:
Dies ist weiterhin ein lokales Lern-Testnet.
Die v0.8-Transaktionen haben noch keine echte kryptografische Signatur.
"""

from __future__ import annotations

import hashlib
import io
import hmac
import json
import math
import os
import secrets
import threading
import time
import sys
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, List, Tuple


PROJECT_NAME = "Logicoin"
COIN_NAME = "LOGIC"
TICKER = "LOGIC"
VERSION = "0.12.15.3"
NETWORK_ID = "logicoin-public-testnet-rc1"
NETWORK_NAME = "Logicoin Public Testnet RC1"
RELEASE_CHANNEL = "public-testnet-rc1"
ALGORITHM = "LogicHash-v0-CPU-Test"
GPU_ALGORITHM_LEGACY = "LogicHash-v1-GPU-Lite"
GPU_ALGORITHM = "LogicHash-v2-CUDA-Mix"
SUPPORTED_ALGORITHMS = {ALGORITHM, GPU_ALGORITHM_LEGACY, GPU_ALGORITHM}

LEGACY_DIFFICULTY_RULE = "hex-v1"
DIFFICULTY_RULE_V2 = "bits-v2"
DIFFICULTY_RULE_V3 = "bits-v3-fast"

# Diese Werte sind absichtlich eingefroren, damit bereits geminte Blöcke
# auch nach späteren Difficulty-Upgrades unverändert validiert werden.
LEGACY_DIFFICULTY_CONFIG: Dict[str, Any] = {
    "start_difficulty": 4,
    "min_difficulty": 3,
    "max_difficulty": 5,
    "difficulty_adjustment_interval": 5,
    "target_block_time_seconds": 35.0,
    "increase_if_avg_below_seconds": 18.0,
    "decrease_if_avg_above_seconds": 75.0,
}

# Eingefrorene v2-Regel aus v0.12.12–v0.12.14.2.
# Sie wird ausschließlich zur Validierung historischer bits-v2-Blöcke benutzt.
FROZEN_DIFFICULTY_V2_CONFIG: Dict[str, Any] = {
    "target_block_time_seconds": 30.0,
    "difficulty_adjustment_interval": 8,
    "min_bits": 18,
    "max_bits": 30,
    "increase_below_seconds": 18.0,
    "decrease_above_seconds": 54.0,
}

BASE_DIR = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
CHAIN_FILE = BASE_DIR / "logic_chain.json"
CONFIG_FILE = BASE_DIR / "logicoin_config.json"
PUBLIC_NETWORK_FILE = BASE_DIR / "logicoin_public_network.json"

# v0.12.15.3: getrennte RLocks für die JSON-Speicher.
_ATOMIC_WRITE_LOCK = threading.RLock()
_CONFIG_LOCK = threading.RLock()
_CHAIN_STORAGE_LOCK = threading.RLock()
_MEMPOOL_STORAGE_LOCK = threading.RLock()
MEMPOOL_FILE = BASE_DIR / "logic_mempool.json"

DEFAULT_MINER_ADDRESS = "logic1_public_test_wallet"

DEFAULT_CONFIG: Dict[str, Any] = {
    "start_difficulty": 4,
    "min_difficulty": 3,
    "max_difficulty": 5,
    "target_block_time_seconds": 35.0,
    "difficulty_adjustment_interval": 5,
    "increase_if_avg_below_seconds": 18.0,
    "decrease_if_avg_above_seconds": 75.0,

    # v0.12.15.3: feinere Difficulty in Bits.
    # Alte Blöcke ohne difficulty_rule bleiben nach den alten Hex-Regeln gültig.
    "difficulty_v2_enabled": True,
    "difficulty_v2_target_block_time_seconds": 30.0,
    "difficulty_v2_adjustment_interval": 8,
    "difficulty_v2_min_bits": 18,
    "difficulty_v2_max_bits": 30,
    "difficulty_v2_increase_below_seconds": 18.0,
    "difficulty_v2_decrease_above_seconds": 54.0,

    # v0.12.15.3: schnellere, proportionale Bit-Difficulty.
    # Alte bits-v2-Blöcke bleiben über die eingefrorene v2-Regel gültig.
    "difficulty_v3_enabled": True,
    "difficulty_v3_target_block_time_seconds": 30.0,
    "difficulty_v3_adjustment_interval": 4,
    "difficulty_v3_min_bits": 18,
    "difficulty_v3_max_bits": 42,
    "difficulty_v3_increase_below_seconds": 24.0,
    "difficulty_v3_decrease_above_seconds": 45.0,
    "difficulty_v3_max_step_up_bits": 6,
    "difficulty_v3_max_step_down_bits": 4,

    "network_id": NETWORK_ID,
    "network_name": NETWORK_NAME,
    "release_channel": RELEASE_CHANNEL,
    "public_testnet": True,
    "seed_nodes": [],

    "block_reward": 50.0,
    "max_transactions_per_block": 25,
    "min_tx_fee": 0.01,

    # v0.10 Wallet-Signaturen
    "require_signed_transactions": True,

    # Historische unsignierte Testwallets bleiben im öffentlichen Testnet deaktiviert.
    "allow_legacy_unsigned_test_wallet": False,

    # v0.12 LAN-Testnet / Peer-Sync
    "node_bind_host": "0.0.0.0",
    "node_port": 8080,
    "peer_sync_enabled": True,
    "peer_sync_interval_seconds": 5.0,
    "peer_request_timeout_seconds": 5.0,
    "max_peers": 32,
    "max_remote_chain_blocks": 100000
}

GENESIS_DIFFICULTY = 0

NON_HEADER_FIELDS = {
    "hash",
    "mining_time_seconds",
    "hashrate_hs",
    "job_id",
    "template_height",
    "template_tip_hash",
}

TX_NON_HASH_FIELDS = {
    "txid",
    "signature"
}


# ============================================================
# ATOMIC JSON STORAGE
# ============================================================

def atomic_json_write(path: Path, data: Any) -> None:
    """
    Atomarer JSON-Writer mit Windows-Retry.

    Windows kann os.replace kurzfristig mit WinError 5/32 blockieren,
    wenn Defender, Explorer oder ein anderer Prozess die Zieldatei liest.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(
        f".{path.name}.{os.getpid()}.{threading.get_ident()}.{uuid.uuid4().hex}.tmp"
    )

    try:
        with _ATOMIC_WRITE_LOCK:
            with temp.open("w", encoding="utf-8") as handle:
                json.dump(data, handle, indent=2, ensure_ascii=False)
                handle.flush()
                try:
                    os.fsync(handle.fileno())
                except OSError:
                    pass

            last_error: OSError | None = None
            for attempt in range(40):
                try:
                    os.replace(temp, path)
                    last_error = None
                    break
                except OSError as exc:
                    last_error = exc
                    if getattr(exc, "winerror", None) not in {5, 32} and not isinstance(exc, PermissionError):
                        raise
                    time.sleep(min(0.05 + attempt * 0.01, 0.25))

            if last_error is not None:
                raise last_error
    finally:
        try:
            if temp.exists():
                temp.unlink()
        except OSError:
            pass


def configure_utf8_stdio() -> None:
    """
    Erzwingt UTF-8 für eingefrorene/umgeleitete Windows-Prozesse.

    Interne Miner schreiben häufig in eine Logdatei. Windows/PyInstaller kann
    dafür trotzdem cp1252 wählen. Zeichen wie ┌, ─, │ oder ✓ würden dann einen
    UnicodeEncodeError auslösen und den Miner beenden.
    """
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        if stream is None:
            continue

        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
            continue
        except Exception:
            pass

        try:
            buffer = getattr(stream, "buffer", None)
            if buffer is not None:
                replacement = io.TextIOWrapper(
                    buffer,
                    encoding="utf-8",
                    errors="replace",
                    line_buffering=True,
                )
                setattr(sys, stream_name, replacement)
        except Exception:
            pass



# ============================================================
# CONFIG
# ============================================================

def save_config(config: Dict[str, Any]) -> None:
    with _CONFIG_LOCK:
        atomic_json_write(CONFIG_FILE, config)


def _normalize_config(loaded: Any) -> Dict[str, Any]:
    config = dict(DEFAULT_CONFIG)
    if isinstance(loaded, dict):
        config.update(loaded)

    for key in [
        "start_difficulty",
        "min_difficulty",
        "max_difficulty",
        "difficulty_adjustment_interval",
        "difficulty_v2_adjustment_interval",
        "difficulty_v2_min_bits",
        "difficulty_v2_max_bits",
        "difficulty_v3_adjustment_interval",
        "difficulty_v3_min_bits",
        "difficulty_v3_max_bits",
        "difficulty_v3_max_step_up_bits",
        "difficulty_v3_max_step_down_bits",
        "max_transactions_per_block",
        "node_port",
        "max_peers",
        "max_remote_chain_blocks",
    ]:
        config[key] = int(config.get(key, DEFAULT_CONFIG[key]))

    for key in [
        "target_block_time_seconds",
        "increase_if_avg_below_seconds",
        "decrease_if_avg_above_seconds",
        "difficulty_v2_target_block_time_seconds",
        "difficulty_v2_increase_below_seconds",
        "difficulty_v2_decrease_above_seconds",
        "difficulty_v3_target_block_time_seconds",
        "difficulty_v3_increase_below_seconds",
        "difficulty_v3_decrease_above_seconds",
        "block_reward",
        "min_tx_fee",
        "peer_sync_interval_seconds",
        "peer_request_timeout_seconds",
    ]:
        config[key] = float(config.get(key, DEFAULT_CONFIG[key]))

    config["min_difficulty"] = max(1, config["min_difficulty"])
    config["max_difficulty"] = max(config["min_difficulty"], config["max_difficulty"])
    config["start_difficulty"] = max(
        config["min_difficulty"],
        min(config["max_difficulty"], config["start_difficulty"]),
    )

    config["difficulty_adjustment_interval"] = max(
        1, config["difficulty_adjustment_interval"]
    )

    config["difficulty_v2_adjustment_interval"] = max(
        2, config["difficulty_v2_adjustment_interval"]
    )
    config["difficulty_v2_min_bits"] = max(
        4, config["difficulty_v2_min_bits"]
    )
    config["difficulty_v2_max_bits"] = max(
        config["difficulty_v2_min_bits"],
        config["difficulty_v2_max_bits"],
    )
    config["difficulty_v2_target_block_time_seconds"] = max(
        1.0,
        config["difficulty_v2_target_block_time_seconds"],
    )
    config["difficulty_v2_increase_below_seconds"] = max(
        0.1,
        config["difficulty_v2_increase_below_seconds"],
    )
    config["difficulty_v2_decrease_above_seconds"] = max(
        config["difficulty_v2_increase_below_seconds"] + 0.1,
        config["difficulty_v2_decrease_above_seconds"],
    )

    config["difficulty_v3_adjustment_interval"] = max(
        2, config["difficulty_v3_adjustment_interval"]
    )
    config["difficulty_v3_min_bits"] = max(
        4, config["difficulty_v3_min_bits"]
    )
    config["difficulty_v3_max_bits"] = max(
        config["difficulty_v3_min_bits"],
        min(62, config["difficulty_v3_max_bits"]),
    )
    config["difficulty_v3_target_block_time_seconds"] = max(
        1.0,
        config["difficulty_v3_target_block_time_seconds"],
    )
    config["difficulty_v3_increase_below_seconds"] = max(
        0.1,
        config["difficulty_v3_increase_below_seconds"],
    )
    config["difficulty_v3_decrease_above_seconds"] = max(
        config["difficulty_v3_increase_below_seconds"] + 0.1,
        config["difficulty_v3_decrease_above_seconds"],
    )
    config["difficulty_v3_max_step_up_bits"] = max(
        1,
        min(12, config["difficulty_v3_max_step_up_bits"]),
    )
    config["difficulty_v3_max_step_down_bits"] = max(
        1,
        min(12, config["difficulty_v3_max_step_down_bits"]),
    )

    # Netzwerkidentität ist Teil der Software und darf nicht durch eine
    # bearbeitete lokale Config auf ein anderes Netzwerk umgebogen werden.
    config["network_id"] = NETWORK_ID
    config["network_name"] = NETWORK_NAME
    config["release_channel"] = RELEASE_CHANNEL
    config["public_testnet"] = True

    seed_nodes = config.get("seed_nodes", [])
    if not isinstance(seed_nodes, list):
        seed_nodes = []
    config["seed_nodes"] = [
        str(item).strip()
        for item in seed_nodes
        if str(item).strip()
    ]

    config["max_transactions_per_block"] = max(
        0, config["max_transactions_per_block"]
    )
    config["block_reward"] = max(0.0, config["block_reward"])
    config["min_tx_fee"] = max(0.0, config["min_tx_fee"])

    config["difficulty_v2_enabled"] = bool(
        config.get("difficulty_v2_enabled", True)
    )
    config["difficulty_v3_enabled"] = bool(
        config.get("difficulty_v3_enabled", True)
    )
    config["require_signed_transactions"] = bool(
        config.get("require_signed_transactions", True)
    )
    config["allow_legacy_unsigned_test_wallet"] = bool(
        config.get("allow_legacy_unsigned_test_wallet", True)
    )
    config["peer_sync_enabled"] = bool(
        config.get("peer_sync_enabled", True)
    )
    config["node_bind_host"] = (
        str(config.get("node_bind_host", "0.0.0.0")).strip() or "0.0.0.0"
    )

    config["node_port"] = max(
        1, min(65535, int(config.get("node_port", 8080)))
    )
    config["max_peers"] = max(
        1, min(256, int(config.get("max_peers", 32)))
    )
    config["max_remote_chain_blocks"] = max(
        100, int(config.get("max_remote_chain_blocks", 100000))
    )
    config["peer_sync_interval_seconds"] = max(
        1.0, float(config.get("peer_sync_interval_seconds", 5.0))
    )
    config["peer_request_timeout_seconds"] = max(
        1.0, float(config.get("peer_request_timeout_seconds", 5.0))
    )

    return config


def load_config() -> Dict[str, Any]:
    with _CONFIG_LOCK:
        if not CONFIG_FILE.exists():
            config = _normalize_config(DEFAULT_CONFIG)
            atomic_json_write(CONFIG_FILE, config)
            return config

        try:
            with CONFIG_FILE.open("r", encoding="utf-8") as handle:
                loaded = json.load(handle)
        except Exception:
            loaded = {}

        config = _normalize_config(loaded)

        # Öffentliche Seed-Nodes werden aus der separaten, leicht
        # verteilbaren Netzwerk-JSON übernommen. Konsensparameter bleiben
        # weiterhin in logicoin_config.json beziehungsweise im Code.
        try:
            if PUBLIC_NETWORK_FILE.exists():
                public_data = json.loads(
                    PUBLIC_NETWORK_FILE.read_text(
                        encoding="utf-8"
                    )
                )
                if (
                    isinstance(public_data, dict)
                    and str(
                        public_data.get(
                            "network_id",
                            "",
                        )
                    )
                    == NETWORK_ID
                ):
                    native = public_data.get(
                        "native_asset",
                        {},
                    )
                    public_seeds = (
                        native.get(
                            "public_seed_nodes",
                            [],
                        )
                        if isinstance(
                            native,
                            dict,
                        )
                        else []
                    )
                    if isinstance(
                        public_seeds,
                        list,
                    ):
                        merged_seeds = list(
                            config.get(
                                "seed_nodes",
                                [],
                            )
                        )
                        for seed in public_seeds:
                            value = (
                                str(seed)
                                .strip()
                                .rstrip("/")
                            )
                            if (
                                value
                                and value
                                not in merged_seeds
                            ):
                                merged_seeds.append(
                                    value
                                )
                        config["seed_nodes"] = (
                            merged_seeds
                        )
        except Exception:
            pass

        # Nur schreiben, wenn wirklich Migration/Korrektur nötig ist.
        # Normale GET-Anfragen ändern die Config-Datei nicht mehr.
        if loaded != config:
            atomic_json_write(CONFIG_FILE, config)

        return config


# ============================================================
# JSON / HASH
# ============================================================

def canonical_json(data: Dict[str, Any]) -> bytes:
    return json.dumps(data, sort_keys=True, separators=(",", ":")).encode("utf-8")


def logic_hash_v0(header: Dict[str, Any]) -> str:
    raw = canonical_json(header)
    h1 = hashlib.sha256(raw).digest()
    h2 = hashlib.blake2s(h1 + raw).digest()
    h3 = hashlib.sha256(h2 + h1).hexdigest()
    return h3


def logic_hash_v1_gpu_lite_base_digest(header: Dict[str, Any]) -> bytes:
    """
    GPU-freundlicher Basis-Digest für LogicHash-v1-GPU-Lite.

    Idee:
    Alle Blockdaten außer nonce/hash/stats werden einmal stabil kanonisch
    gehasht. Die GPU muss dann nur noch base_digest + nonce testen.
    """
    base_header = dict(header)
    base_header.pop("nonce", None)
    return hashlib.sha256(canonical_json(base_header)).digest()


def logic_hash_v1_gpu_lite_base_hex(header: Dict[str, Any]) -> str:
    return logic_hash_v1_gpu_lite_base_digest(header).hex()


def logic_hash_v1_gpu_lite(header: Dict[str, Any]) -> str:
    """
    GPU-Lite PoW:

    base = sha256(canonical_json(header ohne nonce))
    h1   = sha256(base + nonce_u64_little_endian)
    h2   = sha256(h1 + base)

    Vorteil:
    - konstante kleine GPU-Arbeit pro Nonce
    - sehr niedriger VRAM-Bedarf
    - GTX 1050 Ti-freundlich
    - RTX/CUDA-freundlich
    """
    nonce = int(header.get("nonce", 0))
    if nonce < 0:
        nonce = 0
    if nonce > 0xFFFFFFFFFFFFFFFF:
        nonce = nonce % (0xFFFFFFFFFFFFFFFF + 1)

    base_digest = logic_hash_v1_gpu_lite_base_digest(header)
    nonce_bytes = int(nonce).to_bytes(8, "little", signed=False)
    h1 = hashlib.sha256(base_digest + nonce_bytes).digest()
    h2 = hashlib.sha256(h1 + base_digest).hexdigest()
    return h2


# ============================================================
# LogicHash-v2-CUDA-Mix
# ============================================================

MASK64 = 0xFFFFFFFFFFFFFFFF

def _u64_from_le(data: bytes) -> int:
    return int.from_bytes(data[:8], "little", signed=False)


def _splitmix64(value: int) -> int:
    value = (int(value) + 0x9E3779B97F4A7C15) & MASK64
    value = ((value ^ (value >> 30)) * 0xBF58476D1CE4E5B9) & MASK64
    value = ((value ^ (value >> 27)) * 0x94D049BB133111EB) & MASK64
    value = (value ^ (value >> 31)) & MASK64
    return value


def logic_hash_gpu_mix_base_digest(header: Dict[str, Any]) -> bytes:
    """
    Basis-Digest für LogicHash-v2-CUDA-Mix.

    Die GPU bekommt nur diesen 32-Byte-Digest und scannt dann Nonces.
    Der Mix ist bewusst CUDA-freundlich und vermeidet den fehleranfälligen
    eigenen SHA256-CUDA-Kernel.
    """
    base_header = dict(header)
    base_header.pop("nonce", None)
    return hashlib.sha256(canonical_json(base_header)).digest()


def logic_hash_gpu_mix_base_hex(header: Dict[str, Any]) -> str:
    return logic_hash_gpu_mix_base_digest(header).hex()


def logic_hash_v2_cuda_mix_from_base_digest(base: bytes, nonce: int) -> str:
    nonce = int(nonce) & MASK64

    if len(base) != 32:
        raise ValueError("LogicHash-v2 base digest muss genau 32 Bytes lang sein.")

    s0 = _u64_from_le(base[0:8])
    s1 = _u64_from_le(base[8:16])
    s2 = _u64_from_le(base[16:24])
    s3 = _u64_from_le(base[24:32])

    h0 = _splitmix64(s0 ^ nonce ^ 0x243F6A8885A308D3)
    h1 = _splitmix64(s1 ^ nonce ^ 0x13198A2E03707344)
    h2 = _splitmix64(s2 ^ nonce ^ 0xA4093822299F31D0)
    h3 = _splitmix64(s3 ^ nonce ^ 0x082EFA98EC4E6C89)

    # Big-endian Ausgabe, damit führende Nullen im Hex-String eindeutig sind.
    return (
        h0.to_bytes(8, "big", signed=False) +
        h1.to_bytes(8, "big", signed=False) +
        h2.to_bytes(8, "big", signed=False) +
        h3.to_bytes(8, "big", signed=False)
    ).hex()


def logic_hash_v2_cuda_mix_from_base_hex(base_hex: str, nonce: int) -> str:
    return logic_hash_v2_cuda_mix_from_base_digest(bytes.fromhex(base_hex), nonce)


def logic_hash_v2_cuda_mix(header: Dict[str, Any]) -> str:
    nonce = int(header.get("nonce", 0)) & MASK64
    base = logic_hash_gpu_mix_base_digest(header)
    return logic_hash_v2_cuda_mix_from_base_digest(base, nonce)


def make_block_header(block: Dict[str, Any]) -> Dict[str, Any]:
    return {
        key: value
        for key, value in block.items()
        if key not in NON_HEADER_FIELDS
    }


def calculate_block_hash(block: Dict[str, Any]) -> str:
    header = make_block_header(block)
    algorithm = str(header.get("algorithm", ALGORITHM))

    if algorithm == GPU_ALGORITHM:
        return logic_hash_v2_cuda_mix(header)

    if algorithm == GPU_ALGORITHM_LEGACY:
        return logic_hash_v1_gpu_lite(header)

    return logic_hash_v0(header)


def target_prefix(difficulty: int) -> str:
    return "0" * difficulty


def make_transaction_payload(tx: Dict[str, Any]) -> Dict[str, Any]:
    return {
        key: value
        for key, value in tx.items()
        if key not in TX_NON_HASH_FIELDS
    }


def calculate_txid(tx: Dict[str, Any]) -> str:
    raw = canonical_json(make_transaction_payload(tx))
    return hashlib.sha256(raw).hexdigest()

# ============================================================
# WALLET SIGNATURES / SECP256K1 ECDSA
# ============================================================

SIGNATURE_ALGORITHM = "secp256k1-ecdsa-sha256-v1"
ADDRESS_HASH_CHARS = 40

# secp256k1: y^2 = x^3 + 7 over Fp
SECP256K1_P = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEFFFFFC2F
SECP256K1_N = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141
SECP256K1_GX = 55066263022277343669578718895168534326250603453777594175500187360389116729240
SECP256K1_GY = 32670510020758816978083085130507043184471273380659243275938904335757337482424
SECP256K1_G = (SECP256K1_GX, SECP256K1_GY)
Point = Tuple[int, int] | None


def int_to_32byte_hex(value: int) -> str:
    return f"{int(value):064x}"


def mod_inverse(value: int, modulo: int) -> int:
    return pow(value % modulo, -1, modulo)


def point_add(p1: Point, p2: Point) -> Point:
    if p1 is None:
        return p2
    if p2 is None:
        return p1

    x1, y1 = p1
    x2, y2 = p2

    if x1 == x2 and (y1 + y2) % SECP256K1_P == 0:
        return None

    if p1 == p2:
        slope = (3 * x1 * x1) * mod_inverse(2 * y1, SECP256K1_P)
    else:
        slope = (y2 - y1) * mod_inverse(x2 - x1, SECP256K1_P)

    slope %= SECP256K1_P
    x3 = (slope * slope - x1 - x2) % SECP256K1_P
    y3 = (slope * (x1 - x3) - y1) % SECP256K1_P
    return (x3, y3)


def scalar_multiply(k: int, point: Point = SECP256K1_G) -> Point:
    if k % SECP256K1_N == 0 or point is None:
        return None

    result: Point = None
    addend: Point = point
    k = int(k)

    while k:
        if k & 1:
            result = point_add(result, addend)
        addend = point_add(addend, addend)
        k >>= 1

    return result


def generate_private_key_hex() -> str:
    while True:
        private_int = secrets.randbelow(SECP256K1_N - 1) + 1
        if 1 <= private_int < SECP256K1_N:
            return int_to_32byte_hex(private_int)


def private_key_to_public_key(private_key_hex: str) -> str:
    private_int = int(str(private_key_hex), 16)
    if not (1 <= private_int < SECP256K1_N):
        raise ValueError("Ungültiger Private Key.")

    point = scalar_multiply(private_int, SECP256K1_G)
    if point is None:
        raise ValueError("Public Key konnte nicht berechnet werden.")

    x, y = point
    return "04" + int_to_32byte_hex(x) + int_to_32byte_hex(y)


def parse_public_key(public_key_hex: str) -> Point:
    text = str(public_key_hex).strip()

    if len(text) == 130 and text.startswith("04"):
        x = int(text[2:66], 16)
        y = int(text[66:130], 16)
        if (y * y - (x * x * x + 7)) % SECP256K1_P != 0:
            raise ValueError("Public Key liegt nicht auf secp256k1.")
        return (x, y)

    raise ValueError("Nur unkomprimierte Public Keys mit Prefix 04 werden unterstützt.")


def public_key_to_address(public_key_hex: str) -> str:
    pub_bytes = bytes.fromhex(str(public_key_hex))
    digest = hashlib.sha256(pub_bytes).hexdigest()
    return "logic1_" + digest[:ADDRESS_HASH_CHARS]


def generate_keypair_wallet(wallet_version: str = VERSION) -> Dict[str, Any]:
    private_key = generate_private_key_hex()
    public_key = private_key_to_public_key(private_key)
    address = public_key_to_address(public_key)

    return {
        "wallet_version": wallet_version,
        "project": PROJECT_NAME,
        "coin": COIN_NAME,
        "ticker": TICKER,
        "network_id": NETWORK_ID,
        "network_name": NETWORK_NAME,
        "address": address,
        "private_key": private_key,
        "public_key": public_key,
        "signature_algorithm": SIGNATURE_ALGORITHM,
        "created_at": time.time(),
        "note": "v0.10 signierte LOGIC-Wallet. Private Key geheim halten."
    }


def transaction_signing_digest(tx: Dict[str, Any]) -> str:
    payload = make_transaction_payload(tx)
    return hashlib.sha256(canonical_json(payload)).hexdigest()


def deterministic_ecdsa_k(private_key_int: int, digest_int: int) -> int:
    """
    RFC6979-ähnliche deterministische Nonce für ECDSA.
    Für dieses lokale Lern-Testnet ausreichend und besser als reines Random-k.
    """
    x = private_key_int.to_bytes(32, "big")
    h1 = digest_int.to_bytes(32, "big")
    v = b"\x01" * 32
    k = b"\x00" * 32

    k = hmac.new(k, v + b"\x00" + x + h1, hashlib.sha256).digest()
    v = hmac.new(k, v, hashlib.sha256).digest()
    k = hmac.new(k, v + b"\x01" + x + h1, hashlib.sha256).digest()
    v = hmac.new(k, v, hashlib.sha256).digest()

    while True:
        v = hmac.new(k, v, hashlib.sha256).digest()
        candidate = int.from_bytes(v, "big")
        if 1 <= candidate < SECP256K1_N:
            return candidate
        k = hmac.new(k, v + b"\x00", hashlib.sha256).digest()
        v = hmac.new(k, v, hashlib.sha256).digest()


def sign_digest(private_key_hex: str, digest_hex: str) -> Dict[str, str]:
    private_int = int(str(private_key_hex), 16)
    z = int(str(digest_hex), 16)

    if not (1 <= private_int < SECP256K1_N):
        raise ValueError("Ungültiger Private Key.")

    k = deterministic_ecdsa_k(private_int, z)

    while True:
        r_point = scalar_multiply(k, SECP256K1_G)
        if r_point is None:
            k = (k + 1) % SECP256K1_N
            continue

        r = r_point[0] % SECP256K1_N
        if r == 0:
            k = (k + 1) % SECP256K1_N
            continue

        s = (mod_inverse(k, SECP256K1_N) * (z + r * private_int)) % SECP256K1_N
        if s == 0:
            k = (k + 1) % SECP256K1_N
            continue

        # Low-S für eindeutigere Signaturen
        if s > SECP256K1_N // 2:
            s = SECP256K1_N - s

        return {
            "algorithm": SIGNATURE_ALGORITHM,
            "r": int_to_32byte_hex(r),
            "s": int_to_32byte_hex(s),
        }


def verify_digest(public_key_hex: str, digest_hex: str, signature: Dict[str, Any]) -> bool:
    try:
        if signature.get("algorithm") != SIGNATURE_ALGORITHM:
            return False

        public_point = parse_public_key(public_key_hex)
        r = int(str(signature.get("r", "")), 16)
        s = int(str(signature.get("s", "")), 16)
        z = int(str(digest_hex), 16)

        if not (1 <= r < SECP256K1_N and 1 <= s < SECP256K1_N):
            return False

        w = mod_inverse(s, SECP256K1_N)
        u1 = (z * w) % SECP256K1_N
        u2 = (r * w) % SECP256K1_N
        point = point_add(scalar_multiply(u1, SECP256K1_G), scalar_multiply(u2, public_point))

        if point is None:
            return False

        return (point[0] % SECP256K1_N) == r
    except Exception:
        return False


def sign_transaction(tx: Dict[str, Any], private_key_hex: str) -> Dict[str, Any]:
    signed = dict(tx)
    public_key = private_key_to_public_key(private_key_hex)

    signed["signature_version"] = SIGNATURE_ALGORITHM
    signed["public_key"] = public_key
    signed["txid"] = calculate_txid(signed)

    digest = transaction_signing_digest(signed)
    signed["signature"] = sign_digest(private_key_hex, digest)
    signed["txid"] = calculate_txid(signed)

    return signed


def verify_transaction_signature(tx: Dict[str, Any], allow_legacy_test_wallet: bool = True) -> Tuple[bool, str]:
    from_addr = str(tx.get("from", "")).strip()
    public_key = str(tx.get("public_key", "")).strip()
    signature = tx.get("signature")
    signature_version = str(tx.get("signature_version", "")).strip()

    if not public_key or not signature:
        if allow_legacy_test_wallet and from_addr == DEFAULT_MINER_ADDRESS:
            return True, "Legacy-Testwallet ohne Signatur erlaubt."
        return False, "Signatur oder Public Key fehlt."

    if signature_version != SIGNATURE_ALGORITHM:
        return False, "Unbekannte Signatur-Version."

    try:
        derived_address = public_key_to_address(public_key)
    except Exception as e:
        return False, f"Public Key ungültig: {e}"

    if derived_address != from_addr:
        return False, "Public Key passt nicht zur Sender-Adresse."

    digest = transaction_signing_digest(tx)
    if not verify_digest(public_key, digest, signature):
        return False, "Signaturprüfung fehlgeschlagen."

    return True, "Signatur gültig."



# ============================================================
# BLOCKS / CHAIN
# ============================================================

def create_genesis_block() -> Dict[str, Any]:
    block = {
        "index": 0,
        "timestamp": 0,
        "project": PROJECT_NAME,
        "coin": COIN_NAME,
        "ticker": TICKER,
        "network_id": NETWORK_ID,
        "network_name": NETWORK_NAME,
        "release_channel": RELEASE_CHANNEL,
        "algorithm": ALGORITHM,
        "miner_address": "genesis",
        "reward": 0.0,
        "previous_hash": "0" * 64,
        "difficulty": GENESIS_DIFFICULTY,
        "nonce": 0,
        "mining_time_seconds": 0.0,
        "hashrate_hs": 0.0,
        "transactions": [
            {
                "type": "genesis",
                "network_id": NETWORK_ID,
                "message": (
                    "Logicoin / LOGIC Public Testnet RC1. "
                    "Testcoins besitzen keinen garantierten Wert."
                ),
            }
        ],
    }
    block["hash"] = calculate_block_hash(block)
    return block


def load_chain() -> List[Dict[str, Any]]:
    with _CHAIN_STORAGE_LOCK:
        if not CHAIN_FILE.exists():
            chain = [create_genesis_block()]
            atomic_json_write(CHAIN_FILE, chain)
            return chain

        try:
            with CHAIN_FILE.open("r", encoding="utf-8") as handle:
                chain = json.load(handle)
        except Exception:
            chain = [create_genesis_block()]
            atomic_json_write(CHAIN_FILE, chain)

        if not isinstance(chain, list) or not chain:
            chain = [create_genesis_block()]
            atomic_json_write(CHAIN_FILE, chain)

        return chain


def save_chain(chain: List[Dict[str, Any]]) -> None:
    with _CHAIN_STORAGE_LOCK:
        atomic_json_write(CHAIN_FILE, chain)


def reset_chain() -> None:
    save_chain([create_genesis_block()])
    save_mempool([])


# ============================================================
# MEMPOOL
# ============================================================

def load_mempool() -> List[Dict[str, Any]]:
    with _MEMPOOL_STORAGE_LOCK:
        if not MEMPOOL_FILE.exists():
            atomic_json_write(MEMPOOL_FILE, [])
            return []

        try:
            with MEMPOOL_FILE.open("r", encoding="utf-8") as handle:
                mempool = json.load(handle)
        except Exception:
            mempool = []

        if not isinstance(mempool, list):
            mempool = []

        return mempool


def save_mempool(mempool: List[Dict[str, Any]]) -> None:
    with _MEMPOOL_STORAGE_LOCK:
        atomic_json_write(MEMPOOL_FILE, mempool)


def remove_confirmed_from_mempool(mempool: List[Dict[str, Any]], confirmed_txs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    confirmed_ids = {tx.get("txid") for tx in confirmed_txs if tx.get("type") == "transfer"}
    return [tx for tx in mempool if tx.get("txid") not in confirmed_ids]


# ============================================================
# TRANSACTIONS
# ============================================================

def normalize_amount(value: Any) -> float:
    return round(float(value), 8)


def create_transfer_transaction(
    from_address: str,
    to_address: str,
    amount: float,
    fee: float,
    nonce: int,
    memo: str = "",
    public_key: str = "",
    private_key: str | None = None,
) -> Dict[str, Any]:
    tx = {
        "type": "transfer",
        "from": str(from_address).strip(),
        "to": str(to_address).strip(),
        "amount": normalize_amount(amount),
        "fee": normalize_amount(fee),
        "nonce": int(nonce),
        "timestamp": time.time(),
        "memo": str(memo)[:120],
        "ticker": TICKER,
        "network_id": NETWORK_ID,
    }

    if public_key:
        tx["public_key"] = str(public_key).strip()
        tx["signature_version"] = SIGNATURE_ALGORITHM

    tx["txid"] = calculate_txid(tx)

    if private_key:
        tx = sign_transaction(tx, private_key)

    return tx


def is_valid_address(address: str) -> bool:
    if not isinstance(address, str):
        return False
    if not address.startswith("logic1_"):
        return False
    if len(address) < 12 or len(address) > 80:
        return False
    allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_")
    return all(ch in allowed for ch in address)


def get_confirmed_state(chain: List[Dict[str, Any]]) -> Tuple[Dict[str, float], Dict[str, int]]:
    """
    Baut den bestätigten Account-State aus der Chain.

    balances[address] = bestätigtes Guthaben
    nonces[address] = nächste erwartete Transaktionsnummer
    """
    balances: Dict[str, float] = {}
    nonces: Dict[str, int] = {}

    for block in chain:
        block_index = int(block.get("index", 0))

        if block_index > 0:
            miner = str(block.get("miner_address", "unknown"))
            reward = normalize_amount(block.get("reward", 0.0))
            txs = [tx for tx in block.get("transactions", []) if isinstance(tx, dict) and tx.get("type") == "transfer"]
            fees = normalize_amount(sum(normalize_amount(tx.get("fee", 0.0)) for tx in txs))

            balances[miner] = normalize_amount(balances.get(miner, 0.0) + reward + fees)

        for tx in block.get("transactions", []):
            if not isinstance(tx, dict) or tx.get("type") != "transfer":
                continue

            from_addr = str(tx.get("from", ""))
            to_addr = str(tx.get("to", ""))
            amount = normalize_amount(tx.get("amount", 0.0))
            fee = normalize_amount(tx.get("fee", 0.0))

            balances[from_addr] = normalize_amount(balances.get(from_addr, 0.0) - amount - fee)
            balances[to_addr] = normalize_amount(balances.get(to_addr, 0.0) + amount)
            nonces[from_addr] = int(nonces.get(from_addr, 0)) + 1

    return balances, nonces


def validate_transfer_tx_against_state(
    tx: Dict[str, Any],
    balances: Dict[str, float],
    nonces: Dict[str, int],
    min_fee: float,
) -> Tuple[bool, str]:
    if not isinstance(tx, dict):
        return False, "Transaktion ist kein Objekt."

    if tx.get("type") != "transfer":
        return False, "Nur transfer-Transaktionen sind erlaubt."

    if str(tx.get("network_id", "")) != NETWORK_ID:
        return False, (
            "Transaktion gehört zu einem anderen "
            f"LOGIC-Netzwerk. Erwartet {NETWORK_ID}."
        )

    from_addr = str(tx.get("from", "")).strip()
    to_addr = str(tx.get("to", "")).strip()

    if not is_valid_address(from_addr):
        return False, "Ungültige Sender-Adresse."

    if not is_valid_address(to_addr):
        return False, "Ungültige Empfänger-Adresse."

    if from_addr == to_addr:
        return False, "Sender und Empfänger dürfen nicht gleich sein."

    try:
        amount = normalize_amount(tx.get("amount", 0.0))
        fee = normalize_amount(tx.get("fee", 0.0))
        nonce = int(tx.get("nonce", -1))
    except Exception:
        return False, "Amount/Fee/Nonce ungültig."

    if amount <= 0:
        return False, "Amount muss größer als 0 sein."

    if fee < normalize_amount(min_fee):
        return False, f"Fee zu niedrig. Mindest-Fee: {min_fee} {TICKER}."

    expected_nonce = int(nonces.get(from_addr, 0))
    if nonce != expected_nonce:
        return False, f"Falscher Nonce. Erwartet {expected_nonce}, bekommen {nonce}."

    stored_txid = str(tx.get("txid", ""))
    calculated_txid = calculate_txid(tx)
    if stored_txid != calculated_txid:
        return False, "TXID passt nicht."

    config = load_config()
    if bool(config.get("require_signed_transactions", True)):
        sig_ok, sig_reason = verify_transaction_signature(
            tx,
            allow_legacy_test_wallet=bool(config.get("allow_legacy_unsigned_test_wallet", True))
        )
        if not sig_ok:
            return False, sig_reason

    available = normalize_amount(balances.get(from_addr, 0.0))
    needed = normalize_amount(amount + fee)

    if available + 0.000000001 < needed:
        return False, f"Zu wenig Guthaben. Verfügbar {available:.8f}, benötigt {needed:.8f} {TICKER}."

    # State anwenden
    balances[from_addr] = normalize_amount(available - needed)
    balances[to_addr] = normalize_amount(balances.get(to_addr, 0.0) + amount)
    nonces[from_addr] = expected_nonce + 1

    return True, "Transaktion gültig."


def validate_mempool_tx(chain: List[Dict[str, Any]], mempool: List[Dict[str, Any]], new_tx: Dict[str, Any]) -> Tuple[bool, str]:
    config = load_config()
    min_fee = float(config["min_tx_fee"])

    if any(tx.get("txid") == new_tx.get("txid") for tx in mempool):
        return False, "Transaktion ist bereits im Mempool."

    balances, nonces = get_confirmed_state(chain)

    # vorhandene Mempool-TXs zuerst anwenden
    for tx in sorted(mempool, key=lambda item: (str(item.get("from", "")), int(item.get("nonce", 0)), float(item.get("timestamp", 0.0)))):
        ok, reason = validate_transfer_tx_against_state(tx, balances, nonces, min_fee)
        if not ok:
            # Defekte Mempool-TX ignorieren wir hier nicht, weil der Mempool dann aufgeräumt werden sollte.
            return False, f"Mempool enthält ungültige TX: {reason}"

    return validate_transfer_tx_against_state(new_tx, balances, nonces, min_fee)


def select_transactions_for_block(chain: List[Dict[str, Any]], mempool: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    config = load_config()
    min_fee = float(config["min_tx_fee"])
    max_txs = int(config["max_transactions_per_block"])

    if max_txs <= 0:
        return []

    selected: List[Dict[str, Any]] = []
    balances, nonces = get_confirmed_state(chain)

    # Höhere Fees zuerst, danach ältere TXs.
    sorted_pool = sorted(
        mempool,
        key=lambda tx: (-normalize_amount(tx.get("fee", 0.0)), float(tx.get("timestamp", 0.0)))
    )

    for tx in sorted_pool:
        if len(selected) >= max_txs:
            break

        # Auf Kopien testen, damit bei ungültig der State nicht verändert wird.
        test_balances = dict(balances)
        test_nonces = dict(nonces)
        ok, _reason = validate_transfer_tx_against_state(tx, test_balances, test_nonces, min_fee)

        if ok:
            selected.append(tx)
            balances = test_balances
            nonces = test_nonces

    return selected


def validate_block_transactions(chain: List[Dict[str, Any]], block: Dict[str, Any], mempool: List[Dict[str, Any]] | None = None) -> Tuple[bool, str]:
    config = load_config()
    min_fee = float(config["min_tx_fee"])
    max_txs = int(config["max_transactions_per_block"])

    txs = [tx for tx in block.get("transactions", []) if isinstance(tx, dict) and tx.get("type") == "transfer"]

    if len(txs) > max_txs:
        return False, f"Zu viele Transaktionen im Block. Maximal {max_txs}."

    if mempool is not None:
        mempool_ids = {tx.get("txid") for tx in mempool}
        for tx in txs:
            if tx.get("txid") not in mempool_ids:
                return False, f"TX {tx.get('txid')} ist nicht im Mempool."

    balances, nonces = get_confirmed_state(chain)

    seen_ids = set()
    for tx in txs:
        txid = tx.get("txid")
        if txid in seen_ids:
            return False, f"TX {txid} doppelt im Block."
        seen_ids.add(txid)

        ok, reason = validate_transfer_tx_against_state(tx, balances, nonces, min_fee)
        if not ok:
            return False, f"TX {txid} ungültig: {reason}"

    return True, "Block-Transaktionen gültig."


# ============================================================
# DIFFICULTY
# ============================================================

def mined_blocks(chain: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [block for block in chain if int(block.get("index", 0)) > 0]


def clamp_difficulty(value: int, config: Dict[str, Any]) -> int:
    return max(
        int(config["min_difficulty"]),
        min(int(config["max_difficulty"]), int(value)),
    )


def get_next_legacy_difficulty(
    chain: List[Dict[str, Any]],
) -> int:
    """
    Historische Hex-Difficulty für alte Blöcke.

    Diese Berechnung verwendet eingefrorene Werte und darf nicht durch die
    neue Config verändert werden, sonst würde die vorhandene Chain ungültig.
    """
    config = LEGACY_DIFFICULTY_CONFIG
    blocks = mined_blocks(chain)

    if not blocks:
        return clamp_difficulty(
            int(config["start_difficulty"]),
            config,
        )

    last_difficulty = int(
        blocks[-1].get(
            "difficulty",
            config["start_difficulty"],
        )
    )
    interval = int(config["difficulty_adjustment_interval"])

    if len(blocks) % interval != 0:
        return clamp_difficulty(last_difficulty, config)

    interval_blocks = blocks[-interval:]
    total_time = sum(
        float(block.get("mining_time_seconds", 0.0))
        for block in interval_blocks
    )
    avg_time = (
        total_time / len(interval_blocks)
        if interval_blocks
        else float(config["target_block_time_seconds"])
    )

    if avg_time < float(
        config["increase_if_avg_below_seconds"]
    ):
        return clamp_difficulty(
            last_difficulty + 1,
            config,
        )

    if avg_time > float(
        config["decrease_if_avg_above_seconds"]
    ):
        return clamp_difficulty(
            last_difficulty - 1,
            config,
        )

    return clamp_difficulty(last_difficulty, config)


def block_difficulty_rule(
    block: Dict[str, Any],
) -> str:
    rule = str(
        block.get(
            "difficulty_rule",
            LEGACY_DIFFICULTY_RULE,
        )
    ).strip()

    return rule or LEGACY_DIFFICULTY_RULE


def is_bit_difficulty_rule(rule: str) -> bool:
    return str(rule) in {
        DIFFICULTY_RULE_V2,
        DIFFICULTY_RULE_V3,
    }


def chain_uses_difficulty_v2(
    chain: List[Dict[str, Any]],
) -> bool:
    return any(
        block_difficulty_rule(block)
        == DIFFICULTY_RULE_V2
        for block in mined_blocks(chain)
    )


def chain_uses_difficulty_v3(
    chain: List[Dict[str, Any]],
) -> bool:
    return any(
        block_difficulty_rule(block)
        == DIFFICULTY_RULE_V3
        for block in mined_blocks(chain)
    )


def clamp_bits(
    value: int,
    minimum: int,
    maximum: int,
) -> int:
    return max(
        int(minimum),
        min(int(maximum), int(value)),
    )


def clamp_difficulty_bits(
    value: int,
    config: Dict[str, Any],
) -> int:
    """
    Kompatibilitätshelfer für neue v3-Jobs.
    """
    return clamp_bits(
        value,
        int(config["difficulty_v3_min_bits"]),
        int(config["difficulty_v3_max_bits"]),
    )


def hash_meets_difficulty_bits(
    hash_hex: str,
    difficulty_bits: int,
) -> bool:
    bits = max(0, int(difficulty_bits))

    if bits == 0:
        return True

    try:
        raw = bytes.fromhex(str(hash_hex))
    except Exception:
        return False

    full_bytes, remaining_bits = divmod(bits, 8)

    if full_bytes > len(raw):
        return False

    if any(byte != 0 for byte in raw[:full_bytes]):
        return False

    if remaining_bits == 0:
        return True

    if full_bytes >= len(raw):
        return False

    mask = (
        0xFF << (8 - remaining_bits)
    ) & 0xFF
    return (raw[full_bytes] & mask) == 0


def block_expected_work(
    block: Dict[str, Any],
) -> int:
    rule = block_difficulty_rule(block)

    if is_bit_difficulty_rule(rule):
        bits = max(
            0,
            min(
                256,
                int(
                    block.get(
                        "difficulty_bits",
                        0,
                    )
                ),
            ),
        )
        return 1 << bits

    difficulty = max(
        0,
        min(
            64,
            int(block.get("difficulty", 0)),
        ),
    )
    return 1 << (difficulty * 4)


def recent_network_block_intervals(
    chain: List[Dict[str, Any]],
    count: int,
) -> List[float]:
    """
    Reale Zeit zwischen akzeptierten Netzwerkblöcken.
    """
    blocks = mined_blocks(chain)

    if len(blocks) < 2:
        return []

    selected = blocks[
        -(max(2, int(count)) + 1):
    ]
    intervals: List[float] = []

    for previous, current in zip(
        selected,
        selected[1:],
    ):
        try:
            delta = (
                float(
                    current.get(
                        "timestamp",
                        0.0,
                    )
                )
                - float(
                    previous.get(
                        "timestamp",
                        0.0,
                    )
                )
            )
        except Exception:
            continue

        if delta <= 0:
            continue

        intervals.append(
            min(delta, 600.0)
        )

    return intervals


def robust_average(
    values: List[float],
) -> float:
    if not values:
        return 0.0

    ordered = sorted(
        float(value)
        for value in values
    )

    if len(ordered) >= 6:
        ordered = ordered[1:-1]

    return sum(ordered) / len(ordered)


def last_bit_difficulty(
    chain: List[Dict[str, Any]],
) -> int:
    for block in reversed(
        mined_blocks(chain)
    ):
        rule = block_difficulty_rule(block)

        if is_bit_difficulty_rule(rule):
            return int(
                block.get(
                    "difficulty_bits",
                    int(
                        block.get(
                            "difficulty",
                            5,
                        )
                    ) * 4,
                )
            )

        return int(
            block.get(
                "difficulty",
                LEGACY_DIFFICULTY_CONFIG[
                    "start_difficulty"
                ],
            )
        ) * 4

    return int(
        LEGACY_DIFFICULTY_CONFIG[
            "start_difficulty"
        ]
    ) * 4


def get_next_difficulty_bits_v2(
    chain: List[Dict[str, Any]],
) -> int:
    """
    Eingefrorene historische bits-v2-Regel.

    Diese Funktion darf nicht anhand neuer Config-Werte verändert werden,
    da sonst bereits existierende v2-Blöcke ungültig würden.
    """
    policy = FROZEN_DIFFICULTY_V2_CONFIG
    blocks = mined_blocks(chain)
    v2_blocks = [
        block
        for block in blocks
        if block_difficulty_rule(block)
        == DIFFICULTY_RULE_V2
    ]

    last_bits = clamp_bits(
        last_bit_difficulty(chain),
        int(policy["min_bits"]),
        int(policy["max_bits"]),
    )
    interval = int(
        policy[
            "difficulty_adjustment_interval"
        ]
    )

    if (
        v2_blocks
        and len(v2_blocks) % interval != 0
    ):
        return last_bits

    intervals = recent_network_block_intervals(
        chain,
        interval,
    )

    if not intervals:
        return last_bits

    average = robust_average(intervals)
    increase_below = float(
        policy["increase_below_seconds"]
    )
    decrease_above = float(
        policy["decrease_above_seconds"]
    )
    step = 0

    if average < increase_below:
        step = (
            2
            if average
            < increase_below / 3.0
            else 1
        )
    elif average > decrease_above:
        step = (
            -2
            if average
            > decrease_above * 4.0
            else -1
        )

    return clamp_bits(
        last_bits + step,
        int(policy["min_bits"]),
        int(policy["max_bits"]),
    )


def get_next_difficulty_bits_v3(
    chain: List[Dict[str, Any]],
) -> int:
    """
    Schnelle proportionale Difficulty-Regel.

    Beispiel:
    Ziel 30 s, aktuelle Blockzeit etwa 1 s:
    log2(30 / 1) ≈ 4.9 -> +5 Bits.

    Dadurch springt das Netzwerk kontrolliert von etwa 30 auf 35 Bits,
    statt viele Intervalle lang bei extrem kurzen Blöcken zu bleiben.
    """
    config = load_config()
    blocks = mined_blocks(chain)
    v3_blocks = [
        block
        for block in blocks
        if block_difficulty_rule(block)
        == DIFFICULTY_RULE_V3
    ]

    last_bits = clamp_bits(
        last_bit_difficulty(chain),
        int(config["difficulty_v3_min_bits"]),
        int(config["difficulty_v3_max_bits"]),
    )
    interval = int(
        config[
            "difficulty_v3_adjustment_interval"
        ]
    )

    if (
        v3_blocks
        and len(v3_blocks) % interval != 0
    ):
        return last_bits

    intervals = recent_network_block_intervals(
        chain,
        interval,
    )

    if not intervals:
        return last_bits

    average = max(
        0.01,
        robust_average(intervals),
    )
    target = float(
        config[
            "difficulty_v3_target_block_time_seconds"
        ]
    )
    increase_below = float(
        config[
            "difficulty_v3_increase_below_seconds"
        ]
    )
    decrease_above = float(
        config[
            "difficulty_v3_decrease_above_seconds"
        ]
    )
    max_up = int(
        config[
            "difficulty_v3_max_step_up_bits"
        ]
    )
    max_down = int(
        config[
            "difficulty_v3_max_step_down_bits"
        ]
    )
    step = 0

    if average < increase_below:
        ratio = max(
            1.0,
            target / average,
        )
        step = max(
            1,
            min(
                max_up,
                int(math.ceil(math.log2(ratio))),
            ),
        )
    elif average > decrease_above:
        ratio = max(
            1.0,
            average / target,
        )
        step = -max(
            1,
            min(
                max_down,
                int(math.ceil(math.log2(ratio))),
            ),
        )

    return clamp_bits(
        last_bits + step,
        int(config["difficulty_v3_min_bits"]),
        int(config["difficulty_v3_max_bits"]),
    )


def get_next_difficulty_rule(
    chain: List[Dict[str, Any]],
) -> str:
    config = load_config()

    if (
        chain_uses_difficulty_v3(chain)
        or bool(
            config.get(
                "difficulty_v3_enabled",
                True,
            )
        )
    ):
        return DIFFICULTY_RULE_V3

    if (
        chain_uses_difficulty_v2(chain)
        or bool(
            config.get(
                "difficulty_v2_enabled",
                True,
            )
        )
    ):
        return DIFFICULTY_RULE_V2

    return LEGACY_DIFFICULTY_RULE


def get_next_difficulty_bits(
    chain: List[Dict[str, Any]],
) -> int:
    rule = get_next_difficulty_rule(chain)

    if rule == DIFFICULTY_RULE_V3:
        return get_next_difficulty_bits_v3(
            chain
        )

    if rule == DIFFICULTY_RULE_V2:
        return get_next_difficulty_bits_v2(
            chain
        )

    return (
        get_next_legacy_difficulty(chain)
        * 4
    )


def get_next_difficulty(
    chain: List[Dict[str, Any]],
) -> int:
    rule = get_next_difficulty_rule(chain)

    if is_bit_difficulty_rule(rule):
        return int(
            math.ceil(
                get_next_difficulty_bits(
                    chain
                ) / 4.0
            )
        )

    return get_next_legacy_difficulty(
        chain
    )


def expected_hashes_for_next_block(
    chain: List[Dict[str, Any]],
) -> int:
    rule = get_next_difficulty_rule(chain)

    if is_bit_difficulty_rule(rule):
        return 1 << get_next_difficulty_bits(
            chain
        )

    return 1 << (
        get_next_legacy_difficulty(chain)
        * 4
    )


def get_network_params(
    chain: List[Dict[str, Any]],
) -> Dict[str, Any]:
    config = load_config()
    rule = get_next_difficulty_rule(chain)

    if rule == DIFFICULTY_RULE_V3:
        next_bits = get_next_difficulty_bits_v3(
            chain
        )
        target = config[
            "difficulty_v3_target_block_time_seconds"
        ]
        adjustment_interval = config[
            "difficulty_v3_adjustment_interval"
        ]
        increase_below = config[
            "difficulty_v3_increase_below_seconds"
        ]
        decrease_above = config[
            "difficulty_v3_decrease_above_seconds"
        ]
        min_bits = config[
            "difficulty_v3_min_bits"
        ]
        max_bits = config[
            "difficulty_v3_max_bits"
        ]
        mode = "automatic-bits-v3-fast"
    elif rule == DIFFICULTY_RULE_V2:
        policy = FROZEN_DIFFICULTY_V2_CONFIG
        next_bits = get_next_difficulty_bits_v2(
            chain
        )
        target = policy[
            "target_block_time_seconds"
        ]
        adjustment_interval = policy[
            "difficulty_adjustment_interval"
        ]
        increase_below = policy[
            "increase_below_seconds"
        ]
        decrease_above = policy[
            "decrease_above_seconds"
        ]
        min_bits = policy["min_bits"]
        max_bits = policy["max_bits"]
        mode = "automatic-bits-v2-frozen"
    else:
        next_bits = (
            get_next_legacy_difficulty(chain)
            * 4
        )
        target = config[
            "target_block_time_seconds"
        ]
        adjustment_interval = config[
            "difficulty_adjustment_interval"
        ]
        increase_below = config[
            "increase_if_avg_below_seconds"
        ]
        decrease_above = config[
            "decrease_if_avg_above_seconds"
        ]
        min_bits = (
            int(config["min_difficulty"])
            * 4
        )
        max_bits = (
            int(config["max_difficulty"])
            * 4
        )
        mode = "automatic-hex-v1"

    return {
        "difficulty_mode": mode,
        "difficulty_rule": rule,
        "next_difficulty": int(
            math.ceil(next_bits / 4.0)
        ),
        "next_difficulty_bits": next_bits,
        "expected_hashes_per_block": (
            1 << next_bits
        ),
        "start_difficulty": (
            config["start_difficulty"]
        ),
        "block_reward": config[
            "block_reward"
        ],
        "target_block_time_seconds": target,
        "difficulty_adjustment_interval": (
            adjustment_interval
        ),
        "increase_if_avg_below_seconds": (
            increase_below
        ),
        "decrease_if_avg_above_seconds": (
            decrease_above
        ),
        "min_difficulty": (
            int(math.ceil(min_bits / 4.0))
        ),
        "max_difficulty": (
            int(math.ceil(max_bits / 4.0))
        ),
        "min_difficulty_bits": min_bits,
        "max_difficulty_bits": max_bits,
        "max_step_up_bits": (
            config.get(
                "difficulty_v3_max_step_up_bits",
                2,
            )
            if rule == DIFFICULTY_RULE_V3
            else 2
        ),
        "max_step_down_bits": (
            config.get(
                "difficulty_v3_max_step_down_bits",
                2,
            )
            if rule == DIFFICULTY_RULE_V3
            else 2
        ),
        "max_transactions_per_block": (
            config[
                "max_transactions_per_block"
            ]
        ),
        "min_tx_fee": config["min_tx_fee"],
        "config_file": str(CONFIG_FILE),
    }


def get_recent_block_time_stats(
    chain: List[Dict[str, Any]],
) -> Dict[str, Any]:
    blocks = mined_blocks(chain)
    params = get_network_params(chain)
    rule = str(
        params["difficulty_rule"]
    )
    interval = int(
        params[
            "difficulty_adjustment_interval"
        ]
    )

    if not blocks:
        next_bits = int(
            params["next_difficulty_bits"]
        )
        return {
            "mined_blocks": 0,
            "avg_time_last_interval": 0.0,
            "avg_hashrate_last_interval": 0.0,
            "current_difficulty": int(
                math.ceil(next_bits / 4.0)
            ),
            "current_difficulty_bits": (
                next_bits
            ),
            "next_difficulty": params[
                "next_difficulty"
            ],
            "next_difficulty_bits": (
                next_bits
            ),
            "difficulty_rule": rule,
        }

    intervals = (
        recent_network_block_intervals(
            chain,
            interval,
        )
    )
    avg_time = robust_average(intervals)
    interval_blocks = blocks[
        -max(1, len(intervals)):
    ]
    total_work = sum(
        block_expected_work(block)
        for block in interval_blocks
    )
    total_time = sum(intervals)
    avg_hashrate = (
        total_work / total_time
        if total_time > 0
        else 0.0
    )

    last_block = blocks[-1]
    current_rule = block_difficulty_rule(
        last_block
    )
    current_bits = (
        int(
            last_block.get(
                "difficulty_bits",
                0,
            )
        )
        if is_bit_difficulty_rule(
            current_rule
        )
        else int(
            last_block.get(
                "difficulty",
                0,
            )
        ) * 4
    )

    return {
        "mined_blocks": len(blocks),
        "avg_time_last_interval": avg_time,
        "avg_hashrate_last_interval": (
            avg_hashrate
        ),
        "current_difficulty": int(
            math.ceil(current_bits / 4.0)
        ),
        "current_difficulty_bits": (
            current_bits
        ),
        "next_difficulty": params[
            "next_difficulty"
        ],
        "next_difficulty_bits": params[
            "next_difficulty_bits"
        ],
        "difficulty_rule": rule,
    }


# ============================================================
# VALIDATION
# ============================================================

def validate_block_hash_and_pow(
    block: Dict[str, Any],
) -> Tuple[bool, str]:
    stored_hash = str(block.get("hash", ""))
    calculated_hash = calculate_block_hash(block)

    if stored_hash != calculated_hash:
        return False, "Hash passt nicht."

    rule = block_difficulty_rule(block)

    if is_bit_difficulty_rule(rule):
        bits = int(
            block.get(
                "difficulty_bits",
                -1,
            )
        )

        if bits < 0:
            return False, "difficulty_bits fehlt."

        if not hash_meets_difficulty_bits(
            stored_hash,
            bits,
        ):
            return (
                False,
                "Bit-Proof-of-Work passt nicht.",
            )
    elif rule == LEGACY_DIFFICULTY_RULE:
        difficulty = int(
            block.get(
                "difficulty",
                0,
            )
        )

        if not stored_hash.startswith(
            target_prefix(difficulty)
        ):
            return (
                False,
                "Legacy-Proof-of-Work passt nicht.",
            )
    else:
        return (
            False,
            f"Unbekannte Difficulty-Regel: {rule}",
        )

    return (
        True,
        "Block-Hash und Proof-of-Work gültig.",
    )


def validate_block_difficulty_transition(
    prefix_chain: List[Dict[str, Any]],
    block: Dict[str, Any],
    require_current_rule: bool = False,
) -> Tuple[bool, str]:
    rule = block_difficulty_rule(block)

    if require_current_rule:
        expected_rule = get_next_difficulty_rule(
            prefix_chain
        )

        if rule != expected_rule:
            return False, (
                "Falsche Difficulty-Regel. "
                f"Erwartet {expected_rule}, "
                f"bekommen {rule}."
            )

    # Nach v3 darf die Chain nie wieder zu v2 oder Legacy zurück.
    if chain_uses_difficulty_v3(prefix_chain):
        if rule != DIFFICULTY_RULE_V3:
            return False, (
                "Nach Aktivierung von bits-v3-fast "
                "sind ältere Difficulty-Regeln nicht mehr erlaubt."
            )

    # Nach v2 ist nur v2 selbst oder der einmalige Übergang zu v3 erlaubt.
    elif chain_uses_difficulty_v2(prefix_chain):
        if rule not in {
            DIFFICULTY_RULE_V2,
            DIFFICULTY_RULE_V3,
        }:
            return False, (
                "Nach Aktivierung von bits-v2 "
                "ist Legacy-Difficulty nicht mehr erlaubt."
            )

    if rule == DIFFICULTY_RULE_V3:
        expected_bits = (
            get_next_difficulty_bits_v3(
                prefix_chain
            )
        )
    elif rule == DIFFICULTY_RULE_V2:
        expected_bits = (
            get_next_difficulty_bits_v2(
                prefix_chain
            )
        )
    elif rule == LEGACY_DIFFICULTY_RULE:
        expected_legacy = (
            get_next_legacy_difficulty(
                prefix_chain
            )
        )
        block_difficulty = int(
            block.get(
                "difficulty",
                -1,
            )
        )

        if block_difficulty != expected_legacy:
            return False, (
                "Legacy-Difficulty erwartet "
                f"{expected_legacy}, bekommen "
                f"{block_difficulty}."
            )

        return (
            True,
            "Legacy-Difficulty stimmt.",
        )
    else:
        return (
            False,
            f"Unbekannte Difficulty-Regel: {rule}",
        )

    block_bits = int(
        block.get(
            "difficulty_bits",
            -1,
        )
    )

    if block_bits != expected_bits:
        return False, (
            "Falsche Bit-Difficulty. "
            f"Erwartet {expected_bits} Bits, "
            f"bekommen {block_bits}."
        )

    expected_display = int(
        math.ceil(expected_bits / 4.0)
    )
    block_display = int(
        block.get(
            "difficulty",
            -1,
        )
    )

    if block_display != expected_display:
        return False, (
            "Falsche grobe Difficulty-Anzeige. "
            f"Erwartet {expected_display}, "
            f"bekommen {block_display}."
        )

    return (
        True,
        f"{rule}-Difficulty stimmt.",
    )


def validate_chain(chain: List[Dict[str, Any]]) -> Tuple[bool, str]:
    if not chain:
        return False, "Chain ist leer."

    expected_genesis = create_genesis_block()
    if str(chain[0].get("hash", "")) != str(expected_genesis.get("hash", "")):
        return False, "Genesis-Hash passt nicht zu diesem LOGIC-Netzwerk."

    config = load_config()

    for i, block in enumerate(chain):
        if block.get("project") != PROJECT_NAME:
            return False, f"Block {i} hat einen falschen Projektnamen."

        if block.get("coin") != COIN_NAME or block.get("ticker") != TICKER:
            return False, f"Block {i} hat einen falschen Coin/Ticker."

        if str(block.get("network_id", "")) != NETWORK_ID:
            return False, (
                f"Block {i} gehört zum falschen Netzwerk. "
                f"Erwartet {NETWORK_ID}."
            )

        if block.get("algorithm") not in SUPPORTED_ALGORITHMS:
            return False, f"Block {i} nutzt einen nicht unterstützten Algorithmus."

        ok, reason = validate_block_hash_and_pow(block)
        if not ok:
            return False, f"Block {i} ungültig: {reason}"

        if i == 0:
            if block.get("previous_hash") != "0" * 64:
                return False, "Genesis-Block hat falschen previous_hash."
            if int(block.get("difficulty", -1)) != GENESIS_DIFFICULTY:
                return False, "Genesis-Difficulty ist falsch."
            if abs(float(block.get("reward", -1.0))) > 0.00000001:
                return False, "Genesis-Reward ist falsch."
            continue

        previous_block = chain[i - 1]
        if block.get("previous_hash") != previous_block.get("hash"):
            return False, f"Block {i} ungültig: previous_hash passt nicht."

        expected_index = int(previous_block.get("index", -1)) + 1
        if int(block.get("index", -1)) != expected_index:
            return False, f"Block {i} ungültig: Index passt nicht."

        ok, reason = validate_block_difficulty_transition(
            chain[:i],
            block,
            require_current_rule=False,
        )
        if not ok:
            return False, f"Block {i} ungültig: {reason}"

        expected_reward = float(config["block_reward"])
        if abs(float(block.get("reward", -1.0)) - expected_reward) > 0.00000001:
            return False, f"Block {i} ungültig: Reward passt nicht."

        ok, reason = validate_block_transactions(chain[:i], block, mempool=None)
        if not ok:
            return False, f"Block {i} ungültig: {reason}"

    return True, "Chain ist gültig."


def validate_next_block(chain: List[Dict[str, Any]], block: Dict[str, Any], mempool: List[Dict[str, Any]]) -> Tuple[bool, str]:
    chain_ok, chain_reason = validate_chain(chain)
    if not chain_ok:
        return False, f"Node-Chain ungültig: {chain_reason}"

    previous_block = chain[-1]

    expected_index = int(previous_block.get("index", -1)) + 1
    if int(block.get("index", -1)) != expected_index:
        return False, f"Falscher Index. Erwartet #{expected_index}."

    if block.get("previous_hash") != previous_block.get("hash"):
        return False, "previous_hash passt nicht zum Node-Tip. Wahrscheinlich wurde währenddessen ein anderer Block gefunden."

    if block.get("project") != PROJECT_NAME:
        return False, "Falscher Projektname."

    if block.get("coin") != COIN_NAME or block.get("ticker") != TICKER:
        return False, "Falscher Coin/Ticker."

    if block.get("algorithm") not in SUPPORTED_ALGORITHMS:
        return False, f"Falscher Algorithmus. Erlaubt: {', '.join(sorted(SUPPORTED_ALGORITHMS))}."

    ok, reason = validate_block_difficulty_transition(
        chain,
        block,
        require_current_rule=True,
    )
    if not ok:
        return False, reason

    expected_reward = float(load_config()["block_reward"])
    block_reward = float(block.get("reward", -1))
    if abs(block_reward - expected_reward) > 0.00000001:
        return False, f"Falscher Reward. Erwartet {expected_reward}."

    ok, reason = validate_block_hash_and_pow(block)
    if not ok:
        return False, reason

    ok, reason = validate_block_transactions(chain, block, mempool=mempool)
    if not ok:
        return False, reason

    return True, "Block akzeptiert."


# ============================================================
# BALANCE / STATS
# ============================================================

def calculate_balances(chain: List[Dict[str, Any]]) -> Dict[str, float]:
    balances, _nonces = get_confirmed_state(chain)
    return balances


def calculate_pending_for_address(mempool: List[Dict[str, Any]], address: str) -> Dict[str, float]:
    pending_out = 0.0
    pending_in = 0.0
    pending_fees = 0.0

    for tx in mempool:
        if tx.get("type") != "transfer":
            continue
        if tx.get("from") == address:
            pending_out = normalize_amount(pending_out + normalize_amount(tx.get("amount", 0.0)))
            pending_fees = normalize_amount(pending_fees + normalize_amount(tx.get("fee", 0.0)))
        if tx.get("to") == address:
            pending_in = normalize_amount(pending_in + normalize_amount(tx.get("amount", 0.0)))

    return {
        "pending_out": pending_out,
        "pending_in": pending_in,
        "pending_fees": pending_fees,
        "pending_total_out": normalize_amount(pending_out + pending_fees),
    }


def get_address_info(chain: List[Dict[str, Any]], mempool: List[Dict[str, Any]], address: str) -> Dict[str, Any]:
    balances, nonces = get_confirmed_state(chain)

    confirmed = normalize_amount(balances.get(address, 0.0))
    pending = calculate_pending_for_address(mempool, address)

    next_nonce = int(nonces.get(address, 0))
    outgoing_mempool = sorted(
        [tx for tx in mempool if tx.get("type") == "transfer" and tx.get("from") == address],
        key=lambda tx: int(tx.get("nonce", 0))
    )
    for tx in outgoing_mempool:
        if int(tx.get("nonce", -1)) == next_nonce:
            next_nonce += 1

    spendable = normalize_amount(confirmed - pending["pending_total_out"])

    mined_blocks_count = 0
    mined_rewards = 0.0
    mined_fees = 0.0
    for block in chain:
        if str(block.get("miner_address", "")) != str(address):
            continue
        if int(block.get("index", 0)) <= 0:
            continue
        mined_blocks_count += 1
        mined_rewards += float(block.get("reward", 0.0))
        mined_fees += sum(
            float(tx.get("fee", 0.0))
            for tx in block.get("transactions", [])
            if isinstance(tx, dict) and tx.get("type") == "transfer"
        )

    mined_rewards = normalize_amount(mined_rewards)
    mined_fees = normalize_amount(mined_fees)
    mined_total = normalize_amount(mined_rewards + mined_fees)

    return {
        "address": address,
        "confirmed_balance": confirmed,
        "pending_in": pending["pending_in"],
        "pending_out": pending["pending_out"],
        "pending_fees": pending["pending_fees"],
        "pending_total_out": pending["pending_total_out"],
        "spendable_balance": spendable,
        "next_nonce": next_nonce,
        "ticker": TICKER,
        "mined_blocks": mined_blocks_count,
        "mined_rewards": mined_rewards,
        "mined_fees": mined_fees,
        "mined_total": mined_total,
    }


def calculate_chain_stats(chain: List[Dict[str, Any]]) -> Dict[str, Any]:
    blocks = mined_blocks(chain)

    if not blocks:
        return {
            "mined_blocks": 0,
            "total_reward": 0.0,
            "total_fees": 0.0,
            "total_mining_time_seconds": 0.0,
            "average_block_time_seconds": 0.0,
            "average_hashrate_hs": 0.0,
            "highest_difficulty": 0,
            "transfer_transactions": 0,
        }

    total_reward = sum(float(block.get("reward", 0.0)) for block in blocks)
    total_fees = 0.0
    tx_count = 0
    for block in blocks:
        txs = [tx for tx in block.get("transactions", []) if isinstance(tx, dict) and tx.get("type") == "transfer"]
        total_fees += sum(float(tx.get("fee", 0.0)) for tx in txs)
        tx_count += len(txs)

    intervals = recent_network_block_intervals(
        chain,
        max(2, len(blocks)),
    )
    total_network_time = sum(intervals)
    total_work = sum(
        block_expected_work(block)
        for block in blocks[-max(1, len(intervals)):]
    )
    highest_bits = max(
        (
            int(block.get("difficulty_bits", 0))
            if is_bit_difficulty_rule(
                block_difficulty_rule(block)
            )
            else int(
                block.get(
                    "difficulty",
                    0,
                )
            ) * 4
        )
        for block in blocks
    )

    return {
        "mined_blocks": len(blocks),
        "total_reward": normalize_amount(total_reward),
        "total_fees": normalize_amount(total_fees),
        "total_mining_time_seconds": total_network_time,
        "average_block_time_seconds": (
            robust_average(intervals)
            if intervals
            else 0.0
        ),
        "average_hashrate_hs": (
            total_work / total_network_time
            if total_network_time > 0
            else 0.0
        ),
        "highest_difficulty": int(math.ceil(highest_bits / 4.0)),
        "highest_difficulty_bits": highest_bits,
        "transfer_transactions": tx_count,
    }


# ============================================================
# MINING
# ============================================================

def create_candidate_block_from_tip(
    tip_block: Dict[str, Any],
    miner_address: str,
    difficulty: int,
    reward: float,
    transactions: List[Dict[str, Any]] | None = None,
    algorithm: str = ALGORITHM,
    difficulty_rule: str = LEGACY_DIFFICULTY_RULE,
    difficulty_bits: int | None = None,
) -> Dict[str, Any]:
    next_index = int(tip_block["index"]) + 1
    rule = str(
        difficulty_rule
        or LEGACY_DIFFICULTY_RULE
    )

    block = {
        "index": next_index,
        "timestamp": time.time(),
        "project": PROJECT_NAME,
        "coin": COIN_NAME,
        "ticker": TICKER,
        "network_id": NETWORK_ID,
        "network_name": NETWORK_NAME,
        "release_channel": RELEASE_CHANNEL,
        "algorithm": algorithm,
        "miner_address": miner_address,
        "reward": normalize_amount(reward),
        "previous_hash": tip_block["hash"],
        "difficulty": int(difficulty),
        "nonce": 0,
        "mining_time_seconds": 0.0,
        "hashrate_hs": 0.0,
        "transactions": list(transactions or []),
    }

    if is_bit_difficulty_rule(rule):
        if difficulty_bits is None:
            raise ValueError(
                f"{rule} benötigt difficulty_bits."
            )

        block["difficulty_rule"] = rule
        block["difficulty_bits"] = int(
            difficulty_bits
        )

    return block


def mine_candidate_block(
    block: Dict[str, Any],
    verbose: bool = True,
    throttle_sleep_every: int = 0,
    throttle_sleep_seconds: float = 0.0,
    throttle_target_percent: int = 100,
    progress_callback: Callable[[int, float, float], None] | None = None,
) -> Dict[str, Any]:
    """
    CPU Proof-of-Work mit selbstkalibrierendem Software-Duty-Cycle.
    Die Prozentangabe begrenzt diesen Miner-Prozess, nicht das ganze System.
    """
    difficulty = int(block.get("difficulty", 4))
    difficulty_rule = block_difficulty_rule(block)
    difficulty_bits = int(
        block.get(
            "difficulty_bits",
            difficulty * 4,
        )
    )

    def target_matches(hash_hex: str) -> bool:
        if is_bit_difficulty_rule(
            difficulty_rule
        ):
            return hash_meets_difficulty_bits(
                hash_hex,
                difficulty_bits,
            )

        return hash_hex.startswith(
            target_prefix(difficulty)
        )

    nonce = 0
    start_time = time.time()
    last_update = start_time
    last_nonce = 0
    chunk_started = time.perf_counter()
    target_percent = max(5, min(100, int(throttle_target_percent)))

    block["nonce"] = 0
    block["hash"] = ""
    block["mining_time_seconds"] = 0.0
    block["hashrate_hs"] = 0.0

    if verbose:
        tx_count = len([
            tx for tx in block.get("transactions", [])
            if isinstance(tx, dict) and tx.get("type") == "transfer"
        ])
        print("\nMining gestartet.")
        print(f"- Neuer Block: #{block['index']}")
        if is_bit_difficulty_rule(
            difficulty_rule
        ):
            print(
                f"- Netzwerk-Difficulty: {difficulty_bits} Bits "
                f"(Anzeige {difficulty})"
            )
        else:
            print(f"- Netzwerk-Difficulty: {difficulty}")
        print(f"- Reward: {block['reward']} {TICKER}")
        print(f"- Transaktionen im Block: {tx_count}")
        print(f"- CPU-Ziel: {target_percent}%")
        print("- Abbrechen mit STRG + C\n")

    while True:
        block["nonce"] = nonce
        block_hash = calculate_block_hash(block)

        if target_matches(block_hash):
            elapsed = time.time() - start_time
            hps = (nonce + 1) / elapsed if elapsed > 0 else 0.0

            block["hash"] = block_hash
            block["mining_time_seconds"] = elapsed
            block["hashrate_hs"] = hps

            if progress_callback is not None:
                progress_callback(nonce + 1, elapsed, hps)

            if verbose:
                print("\nBLOCK GEFUNDEN!")
                print(f"- Block: #{block['index']}")
                print(f"- Nonce: {nonce}")
                print(f"- Hash: {block_hash}")
                print(f"- Zeit: {elapsed:.2f} Sekunden")
                print(f"- Geschwindigkeit: {hps:,.2f} H/s")

            return block

        nonce += 1

        if throttle_sleep_every > 0 and throttle_sleep_seconds > 0:
            if nonce % throttle_sleep_every == 0:
                time.sleep(throttle_sleep_seconds)

        if nonce % 1024 == 0:
            active_seconds = max(time.perf_counter() - chunk_started, 0.0)
            if target_percent < 100 and active_seconds > 0:
                pause = active_seconds * ((100.0 - target_percent) / target_percent)
                if pause > 0:
                    time.sleep(min(pause, 2.0))
            chunk_started = time.perf_counter()

        now = time.time()
        if now - last_update >= 1.0:
            elapsed = now - start_time
            current_hps = (nonce + 1) / elapsed if elapsed > 0 else 0.0
            interval_hps = (nonce - last_nonce) / (now - last_update) if now > last_update else 0.0

            if progress_callback is not None:
                progress_callback(nonce + 1, elapsed, current_hps)

            if verbose:
                print(
                    f"[CPU] Nonce {nonce:,} | "
                    f"aktuell {interval_hps:,.2f} H/s | "
                    f"Ø {current_hps:,.2f} H/s",
                    flush=True,
                )

            last_update = now
            last_nonce = nonce

