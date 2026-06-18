#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import hashlib
import json
import os
import socket
import sys
import threading
import time
import uuid
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

BASE_DIR = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent

PEERS_FILE = BASE_DIR / "logicoin_peers.json"
PEER_STATUS_FILE = BASE_DIR / "logicoin_peer_status.json"

_PEER_STORAGE_LOCK = threading.RLock()
_PEER_STATUS_CACHE: dict[str, dict[str, Any]] = {}
_PEER_STORAGE_LAST_ERROR = ""
_PEER_STORAGE_LAST_SUCCESS: float | None = None

NETWORK_ID = "logicoin-public-testnet-rc1"

CONSENSUS_CONFIG_KEYS = (
    "start_difficulty",
    "min_difficulty",
    "max_difficulty",
    "target_block_time_seconds",
    "difficulty_adjustment_interval",
    "increase_if_avg_below_seconds",
    "decrease_if_avg_above_seconds",
    "difficulty_v2_enabled",
    "difficulty_v2_target_block_time_seconds",
    "difficulty_v2_adjustment_interval",
    "difficulty_v2_min_bits",
    "difficulty_v2_max_bits",
    "difficulty_v2_increase_below_seconds",
    "difficulty_v2_decrease_above_seconds",
    "difficulty_v3_enabled",
    "difficulty_v3_target_block_time_seconds",
    "difficulty_v3_adjustment_interval",
    "difficulty_v3_min_bits",
    "difficulty_v3_max_bits",
    "difficulty_v3_increase_below_seconds",
    "difficulty_v3_decrease_above_seconds",
    "difficulty_v3_max_step_up_bits",
    "difficulty_v3_max_step_down_bits",
    "block_reward",
    "max_transactions_per_block",
    "min_tx_fee",
    "require_signed_transactions",
    "allow_legacy_unsigned_test_wallet",
)


def atomic_json_write(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(
        f".{path.name}.{os.getpid()}.{threading.get_ident()}.{uuid.uuid4().hex}.tmp"
    )

    try:
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


def load_json(path: Path, default: Any) -> Any:
    with _PEER_STORAGE_LOCK:
        if not path.exists():
            atomic_json_write(path, default)
            return default

        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            atomic_json_write(path, default)
            return default


def normalize_peer_url(value: str) -> str:
    value = str(value or "").strip().rstrip("/")
    if not value:
        raise ValueError("Peer-URL ist leer.")

    if "://" not in value:
        value = "http://" + value

    parsed = urllib.parse.urlparse(value)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("Nur http:// oder https:// ist erlaubt.")

    if not parsed.hostname:
        raise ValueError("Peer-URL enthält keinen Host.")

    if parsed.username or parsed.password:
        raise ValueError("Benutzername/Passwort in Peer-URLs ist nicht erlaubt.")

    netloc = parsed.hostname
    if ":" in netloc and not netloc.startswith("["):
        netloc = f"[{netloc}]"

    if parsed.port:
        netloc += f":{parsed.port}"

    return f"{parsed.scheme}://{netloc}".rstrip("/")


def load_peers() -> list[str]:
    raw = load_json(PEERS_FILE, [])
    if not isinstance(raw, list):
        raw = []

    peers: list[str] = []
    for item in raw:
        try:
            url = normalize_peer_url(str(item))
        except Exception:
            continue
        if url not in peers:
            peers.append(url)

    if peers != raw:
        atomic_json_write(PEERS_FILE, peers)
    return peers


def save_peers(peers: list[str]) -> None:
    cleaned: list[str] = []
    for peer in peers:
        try:
            url = normalize_peer_url(peer)
        except Exception:
            continue
        if url not in cleaned:
            cleaned.append(url)
    atomic_json_write(PEERS_FILE, cleaned)


def add_peer(peer_url: str, self_urls: set[str] | None = None, max_peers: int = 32) -> tuple[bool, str]:
    peer = normalize_peer_url(peer_url)
    self_urls = {normalize_peer_url(url) for url in (self_urls or set()) if url}

    with _PEER_STORAGE_LOCK:
        if peer in self_urls:
            return False, "Die eigene Node kann nicht als Peer hinzugefügt werden."

        peers = load_peers()
        if peer in peers:
            return False, "Peer ist bereits gespeichert."

        if len(peers) >= max(1, int(max_peers)):
            return False, f"Maximale Peer-Anzahl erreicht: {max_peers}."

        peers.append(peer)
        save_peers(peers)
        return True, "Peer hinzugefügt."


def remove_peer(peer_url: str) -> tuple[bool, str]:
    peer = normalize_peer_url(peer_url)

    with _PEER_STORAGE_LOCK:
        peers = load_peers()
        if peer not in peers:
            return False, "Peer wurde nicht gefunden."

        peers.remove(peer)
        save_peers(peers)

        status = load_peer_status()
        status.pop(peer, None)
        save_peer_status(status)
        return True, "Peer entfernt."


def load_peer_status() -> dict[str, dict[str, Any]]:
    global _PEER_STATUS_CACHE

    with _PEER_STORAGE_LOCK:
        raw = load_json(PEER_STATUS_FILE, {})
        disk_status = raw if isinstance(raw, dict) else {}

        # Der laufende Node behält die aktuellsten Werte auch dann,
        # wenn Windows die Datei kurzzeitig blockiert.
        merged = dict(disk_status)
        merged.update(_PEER_STATUS_CACHE)
        _PEER_STATUS_CACHE = merged
        return dict(merged)


def save_peer_status(status: dict[str, dict[str, Any]]) -> bool:
    global _PEER_STATUS_CACHE
    global _PEER_STORAGE_LAST_ERROR
    global _PEER_STORAGE_LAST_SUCCESS

    with _PEER_STORAGE_LOCK:
        _PEER_STATUS_CACHE = dict(status)

        try:
            atomic_json_write(PEER_STATUS_FILE, status)
            _PEER_STORAGE_LAST_ERROR = ""
            _PEER_STORAGE_LAST_SUCCESS = time.time()
            return True
        except OSError as exc:
            # Peer-Status ist Diagnosezustand, kein Konsenszustand.
            # Ein Windows-Dateilock darf den Chain-Sync nicht abbrechen.
            _PEER_STORAGE_LAST_ERROR = str(exc)
            return False


def peer_storage_diagnostics() -> dict[str, Any]:
    return {
        "ok": not bool(_PEER_STORAGE_LAST_ERROR),
        "last_error": _PEER_STORAGE_LAST_ERROR,
        "last_success": _PEER_STORAGE_LAST_SUCCESS,
        "cached_peers": len(_PEER_STATUS_CACHE),
        "status_file": str(PEER_STATUS_FILE),
    }


def update_peer_status(peer_url: str, **fields: Any) -> dict[str, Any]:
    peer = normalize_peer_url(peer_url)

    with _PEER_STORAGE_LOCK:
        status = load_peer_status()
        row = dict(status.get(peer, {}))
        row.update(fields)
        row["url"] = peer
        row["updated_at"] = time.time()
        status[peer] = row

        persisted = save_peer_status(status)
        row["status_persisted"] = persisted
        if not persisted:
            row["storage_warning"] = _PEER_STORAGE_LAST_ERROR

        _PEER_STATUS_CACHE[peer] = dict(row)
        return row


def test_peer_connection(peer_url: str, timeout: float = 5.0) -> dict[str, Any]:
    peer = normalize_peer_url(peer_url)
    parsed = urllib.parse.urlparse(peer)
    host = parsed.hostname
    port = parsed.port or (443 if parsed.scheme == "https" else 80)

    result: dict[str, Any] = {
        "ok": False,
        "peer": peer,
        "host": host,
        "port": port,
        "tcp_ok": False,
        "http_ok": False,
        "logicoin_ok": False,
        "latency_ms": None,
        "stage": "tcp",
        "error": "",
    }

    started = time.perf_counter()

    try:
        with socket.create_connection((str(host), int(port)), timeout=timeout):
            result["tcp_ok"] = True
            result["tcp_latency_ms"] = round((time.perf_counter() - started) * 1000.0, 2)
    except Exception as exc:
        result["error"] = f"TCP-Verbindung fehlgeschlagen: {exc}"
        return result

    result["stage"] = "http"

    try:
        info = http_get_json(peer + "/info", timeout=timeout)
        result["http_ok"] = True
        result["latency_ms"] = round((time.perf_counter() - started) * 1000.0, 2)
        result["info"] = {
            "project": info.get("project"),
            "ticker": info.get("ticker"),
            "version": info.get("version"),
            "network_id": info.get("network_id"),
            "node_name": info.get("node_name"),
            "height": info.get("height"),
            "lan_url": info.get("lan_url"),
        }
    except Exception as exc:
        result["error"] = f"HTTP /info fehlgeschlagen: {exc}"
        return result

    result["stage"] = "logicoin"
    info = result.get("info", {})

    if info.get("project") != "Logicoin" or info.get("ticker") != "LOGIC":
        result["error"] = "HTTP erreichbar, aber kein Logicoin/LOGIC-Node."
        return result

    if info.get("network_id") != NETWORK_ID:
        result["error"] = "Logicoin-Node erreichbar, aber falsche Netzwerk-ID."
        return result

    result["logicoin_ok"] = True
    result["ok"] = True
    result["stage"] = "ok"
    return result


def detect_lan_ip() -> str:
    # Es wird nichts gesendet; der UDP-Socket bestimmt nur die passende lokale Schnittstelle.
    candidates = [("1.1.1.1", 80), ("8.8.8.8", 80)]
    for host, port in candidates:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.connect((host, port))
            ip = sock.getsockname()[0]
            if ip and not ip.startswith("127."):
                return ip
        except Exception:
            pass
        finally:
            sock.close()

    try:
        ip = socket.gethostbyname(socket.gethostname())
        if ip:
            return ip
    except Exception:
        pass

    return "127.0.0.1"


def build_node_url(host: str, port: int) -> str:
    host = str(host).strip()
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    return f"http://{host}:{int(port)}"


def network_fingerprint(config: dict[str, Any], genesis_hash: str) -> str:
    consensus = {key: config.get(key) for key in CONSENSUS_CONFIG_KEYS}
    payload = {
        "network_id": NETWORK_ID,
        "genesis_hash": str(genesis_hash),
        "consensus": consensus,
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def chain_work(chain: list[dict[str, Any]]) -> int:
    total = 0

    for block in chain:
        rule = str(
            block.get(
                "difficulty_rule",
                "hex-v1",
            )
        )

        try:
            if rule in {"bits-v2", "bits-v3-fast"}:
                bits = max(
                    0,
                    min(
                        256,
                        int(block.get("difficulty_bits", 0)),
                    ),
                )
            else:
                difficulty = max(
                    0,
                    min(
                        64,
                        int(block.get("difficulty", 0)),
                    ),
                )
                bits = difficulty * 4
        except Exception:
            bits = 0

        total += 1 << bits

    return total


def chain_score(chain: list[dict[str, Any]]) -> tuple[int, int, str]:
    tip_hash = str(chain[-1].get("hash", "")) if chain else ""
    return chain_work(chain), len(chain), tip_hash


def should_adopt_chain(remote_chain: list[dict[str, Any]], local_chain: list[dict[str, Any]]) -> bool:
    return chain_score(remote_chain) > chain_score(local_chain)


def http_get_json(url: str, timeout: float = 5.0) -> dict[str, Any]:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "LogicoinNode/0.12.15.3",
            "Accept": "application/json",
        },
        method="GET",
    )
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def http_post_json(url: str, data: dict[str, Any], timeout: float = 8.0) -> dict[str, Any]:
    payload = json.dumps(data, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=payload,
        headers={
            "User-Agent": "LogicoinNode/0.12.15.3",
            "Content-Type": "application/json; charset=utf-8",
            "Accept": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))
