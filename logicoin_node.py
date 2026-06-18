#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import argparse
import json
import html
import os
import socket
import sys
import threading
import time
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict
from urllib.parse import urlparse, parse_qs

from logicoin_core import (
    PROJECT_NAME,
    COIN_NAME,
    TICKER,
    VERSION,
    NETWORK_ID,
    NETWORK_NAME,
    RELEASE_CHANNEL,
    ALGORITHM,
    GPU_ALGORITHM,
    SUPPORTED_ALGORITHMS,
    CHAIN_FILE,
    CONFIG_FILE,
    MEMPOOL_FILE,
    DEFAULT_MINER_ADDRESS,
    load_chain,
    save_chain,
    reset_chain,
    validate_chain,
    validate_next_block,
    calculate_balances,
    calculate_chain_stats,
    get_network_params,
    get_recent_block_time_stats,
    load_config,
    load_mempool,
    save_mempool,
    validate_mempool_tx,
    remove_confirmed_from_mempool,
    select_transactions_for_block,
    get_address_info,
    mined_blocks,
    configure_utf8_stdio,
)


from logicoin_public_network import (
    public_asset_registry,
    validate_public_network,
)

from logicoin_peer_network import (
    PEERS_FILE,
    PEER_STATUS_FILE,
    add_peer,
    remove_peer,
    load_peers,
    load_peer_status,
    update_peer_status,
    peer_storage_diagnostics,
    test_peer_connection,
    normalize_peer_url,
    detect_lan_ip,
    build_node_url,
    network_fingerprint,
    chain_score,
    should_adopt_chain,
    http_get_json,
    http_post_json,
)

BASE_DIR = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
NODE_IDENTITY_FILE = BASE_DIR / "logicoin_node_identity.json"

PORT = 8080
BIND_HOST = "0.0.0.0"
NODE_NAME = socket.gethostname()
NODE_STARTED_AT = time.time()

CHAIN_LOCK = threading.RLock()
NODE_STOP_EVENT = threading.Event()
LAST_SYNC_SUMMARY: dict[str, Any] = {
    "time": None,
    "peers_checked": 0,
    "peers_online": 0,
    "chains_adopted": 0,
    "errors": 0,
}


def write_node_identity() -> None:
    payload = {
        "version": VERSION,
        "pid": os.getpid(),
        "executable": str(Path(sys.executable).resolve()),
        "port": PORT,
        "bind_host": BIND_HOST,
        "node_name": NODE_NAME,
        "started_at": time.time(),
    }
    temp = NODE_IDENTITY_FILE.with_suffix(".json.tmp")
    temp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(temp, NODE_IDENTITY_FILE)


def remove_node_identity() -> None:
    try:
        if not NODE_IDENTITY_FILE.exists():
            return
        data = json.loads(NODE_IDENTITY_FILE.read_text(encoding="utf-8"))
        if int(data.get("pid", -1)) == os.getpid():
            NODE_IDENTITY_FILE.unlink()
    except Exception:
        pass


def json_response(handler: BaseHTTPRequestHandler, status: int, data: Dict[str, Any]) -> None:
    payload = json.dumps(data, indent=2, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(payload)))
    handler.end_headers()
    handler.wfile.write(payload)



# ============================================================
# LAN TESTNET / PEER NETWORK v0.12
# ============================================================

def local_lan_ip() -> str:
    return detect_lan_ip()


def local_lan_url() -> str:
    return build_node_url(local_lan_ip(), PORT)


def self_peer_urls() -> set[str]:
    values = {
        build_node_url("127.0.0.1", PORT),
        build_node_url("localhost", PORT),
        local_lan_url(),
    }
    result: set[str] = set()
    for value in values:
        try:
            result.add(normalize_peer_url(value))
        except Exception:
            pass
    return result


def current_network_fingerprint() -> str:
    chain = load_chain()
    genesis_hash = str(chain[0].get("hash", "")) if chain else ""
    return network_fingerprint(load_config(), genesis_hash)


def confirmed_txids(chain: list[dict[str, Any]]) -> set[str]:
    ids: set[str] = set()
    for block in chain:
        for tx in block.get("transactions", []):
            if isinstance(tx, dict) and tx.get("type") == "transfer" and tx.get("txid"):
                ids.add(str(tx.get("txid")))
    return ids


def merge_mempools(chain: list[dict[str, Any]], pools: list[list[dict[str, Any]]]) -> list[dict[str, Any]]:
    confirmed = confirmed_txids(chain)
    candidates: list[dict[str, Any]] = []
    seen: set[str] = set()

    for pool in pools:
        if not isinstance(pool, list):
            continue
        for tx in pool:
            if not isinstance(tx, dict):
                continue
            txid = str(tx.get("txid", ""))
            if not txid or txid in seen or txid in confirmed:
                continue
            seen.add(txid)
            candidates.append(tx)

    candidates.sort(key=lambda tx: (float(tx.get("timestamp", 0.0)), str(tx.get("txid", ""))))
    accepted: list[dict[str, Any]] = []

    for tx in candidates:
        ok, _reason = validate_mempool_tx(chain, accepted, tx)
        if ok:
            accepted.append(tx)

    return accepted


def network_summary() -> dict[str, Any]:
    peers = load_peers()
    statuses = load_peer_status()
    online = sum(1 for peer in peers if bool(statuses.get(peer, {}).get("online")))
    chain = load_chain()
    config = load_config()

    return {
        "ok": True,
        "network_id": NETWORK_ID,
        "network_fingerprint": current_network_fingerprint(),
        "node_name": NODE_NAME,
        "bind_host": BIND_HOST,
        "port": PORT,
        "lan_ip": local_lan_ip(),
        "lan_url": local_lan_url(),
        "uptime_seconds": int(time.time() - NODE_STARTED_AT),
        "height": int(chain[-1].get("index", 0)),
        "tip_hash": chain[-1].get("hash"),
        "chain_score": {
            "work": str(chain_score(chain)[0]),
            "blocks": len(chain),
            "tip_hash": chain_score(chain)[2],
        },
        "peer_sync_enabled": bool(config.get("peer_sync_enabled", True)),
        "peer_sync_interval_seconds": float(config.get("peer_sync_interval_seconds", 5.0)),
        "peers_total": len(peers),
        "peers_online": online,
        "peers_file": str(PEERS_FILE),
        "peer_status_file": str(PEER_STATUS_FILE),
        "last_sync": dict(LAST_SYNC_SUMMARY),
        "peer_storage": peer_storage_diagnostics(),
    }


def sync_from_peer(peer_url: str) -> dict[str, Any]:
    peer = normalize_peer_url(peer_url)
    if peer in self_peer_urls():
        return {"ok": False, "peer": peer, "error": "Eigene Node wird nicht synchronisiert."}

    config = load_config()
    timeout = float(config.get("peer_request_timeout_seconds", 5.0))
    started = time.perf_counter()

    try:
        info = http_get_json(peer + "/info", timeout=timeout)
        latency_ms = (time.perf_counter() - started) * 1000.0

        if info.get("project") != PROJECT_NAME or info.get("ticker") != TICKER:
            raise ValueError("Peer gehört nicht zu Logicoin / LOGIC.")

        if info.get("network_id") != NETWORK_ID:
            raise ValueError("Peer nutzt ein anderes LOGIC-Netzwerk.")

        if info.get("network_fingerprint") != current_network_fingerprint():
            raise ValueError("Peer hat andere Konsens-/Genesis-Regeln.")

        chain_data = http_get_json(peer + "/chain", timeout=max(timeout, 10.0))
        remote_chain = chain_data.get("chain", [])
        if not isinstance(remote_chain, list) or not remote_chain:
            raise ValueError("Peer hat keine gültige Chain geliefert.")

        if len(remote_chain) > int(config.get("max_remote_chain_blocks", 100000)):
            raise ValueError("Remote-Chain überschreitet das konfigurierte Limit.")

        valid, reason = validate_chain(remote_chain)
        if not valid:
            raise ValueError(f"Remote-Chain ungültig: {reason}")

        adopted = False
        old_height = 0
        new_height = int(remote_chain[-1].get("index", 0))

        with CHAIN_LOCK:
            local_chain = load_chain()
            old_height = int(local_chain[-1].get("index", 0))
            if should_adopt_chain(remote_chain, local_chain):
                save_chain(remote_chain)
                adopted = True
                local_chain = remote_chain

            try:
                remote_mempool_data = http_get_json(peer + "/mempool", timeout=timeout)
                remote_mempool = remote_mempool_data.get("transactions", [])
            except Exception:
                remote_mempool = []

            merged = merge_mempools(local_chain, [load_mempool(), remote_mempool])
            save_mempool(merged)

        row = update_peer_status(
            peer,
            online=True,
            compatible=True,
            node_name=info.get("node_name"),
            version=info.get("version"),
            height=int(info.get("height", new_height)),
            tip_hash=info.get("tip_hash"),
            lan_url=info.get("lan_url"),
            latency_ms=round(latency_ms, 2),
            last_seen=time.time(),
            last_sync=time.time(),
            adopted_chain=adopted,
            error="",
        )

        return {
            "ok": True,
            "peer": peer,
            "adopted_chain": adopted,
            "old_height": old_height,
            "new_height": int(load_chain()[-1].get("index", 0)),
            "latency_ms": row.get("latency_ms"),
        }

    except Exception as exc:
        original_error = str(exc)

        try:
            update_peer_status(
                peer,
                online=False,
                compatible=False,
                latency_ms=None,
                last_attempt=time.time(),
                error=original_error,
            )
        except Exception as status_exc:
            print(f"PEER-STATUS WARNUNG: {status_exc}")

        return {
            "ok": False,
            "peer": peer,
            "error": original_error,
            "peer_storage": peer_storage_diagnostics(),
        }


def sync_all_peers() -> dict[str, Any]:
    global LAST_SYNC_SUMMARY

    peers = load_peers()
    results: list[dict[str, Any]] = []
    online = 0
    adopted = 0
    errors = 0

    for peer in peers:
        result = sync_from_peer(peer)
        results.append(result)
        if result.get("ok"):
            online += 1
        else:
            errors += 1
        if result.get("adopted_chain"):
            adopted += 1

    LAST_SYNC_SUMMARY = {
        "time": time.time(),
        "peers_checked": len(peers),
        "peers_online": online,
        "chains_adopted": adopted,
        "errors": errors,
    }

    return {"ok": True, "summary": dict(LAST_SYNC_SUMMARY), "results": results}


def peer_sync_loop() -> None:
    while not NODE_STOP_EVENT.is_set():
        config = load_config()
        if bool(config.get("peer_sync_enabled", True)):
            try:
                sync_all_peers()
            except Exception as exc:
                print(f"PEER-SYNC FEHLER: {exc}")

        interval = max(1.0, float(config.get("peer_sync_interval_seconds", 5.0)))
        NODE_STOP_EVENT.wait(interval)


def broadcast_to_peers(path: str, payload: dict[str, Any]) -> None:
    def worker() -> None:
        timeout = float(load_config().get("peer_request_timeout_seconds", 5.0))
        for peer in load_peers():
            try:
                http_post_json(peer + path, payload, timeout=timeout)
            except Exception as exc:
                update_peer_status(peer, online=False, last_attempt=time.time(), error=str(exc))

    threading.Thread(target=worker, daemon=True).start()


def accept_peer_transaction(tx: dict[str, Any]) -> tuple[bool, str]:
    with CHAIN_LOCK:
        chain = load_chain()
        mempool = load_mempool()
        ok, reason = validate_mempool_tx(chain, mempool, tx)
        if not ok:
            # Bereits vorhandene Transaktion gilt für Peer-Relay als erfolgreich.
            if any(str(existing.get("txid")) == str(tx.get("txid")) for existing in mempool):
                return True, "Transaktion bereits vorhanden."
            return False, reason
        mempool.append(tx)
        save_mempool(mempool)
    return True, "Peer-Transaktion aufgenommen."


def accept_peer_block(block: dict[str, Any]) -> tuple[bool, str]:
    with CHAIN_LOCK:
        chain = load_chain()
        block_hash = str(block.get("hash", ""))

        if any(str(existing.get("hash", "")) == block_hash for existing in chain):
            return True, "Block bereits vorhanden."

        expected_index = int(chain[-1].get("index", -1)) + 1
        block_index = int(block.get("index", -1))

        if block_index != expected_index or block.get("previous_hash") != chain[-1].get("hash"):
            return False, "Block passt nicht direkt auf den lokalen Tip; vollständiger Sync erforderlich."

        candidate_chain = chain + [block]
        ok, reason = validate_chain(candidate_chain)
        if not ok:
            return False, reason

        save_chain(candidate_chain)
        confirmed_txs = [
            tx for tx in block.get("transactions", [])
            if isinstance(tx, dict) and tx.get("type") == "transfer"
        ]
        mempool = remove_confirmed_from_mempool(load_mempool(), confirmed_txs)
        save_mempool(mempool)

    return True, "Peer-Block akzeptiert."


def classify_block_rejection(reason: str) -> tuple[str, bool]:
    text = str(reason or "").lower()
    stale_markers = (
        "falscher index",
        "previous_hash passt nicht",
        "anderer block gefunden",
        "node-tip",
        "passt nicht direkt auf den lokalen tip",
    )
    stale = any(marker in text for marker in stale_markers)
    return ("stale" if stale else "invalid"), stale



# ============================================================
# BLOCK EXPLORER HTML
# ============================================================

def html_response(handler: BaseHTTPRequestHandler, status: int, html_text: str) -> None:
    payload = html_text.encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "text/html; charset=utf-8")
    handler.send_header("Content-Length", str(len(payload)))
    handler.end_headers()
    handler.wfile.write(payload)


def h(value: Any) -> str:
    return html.escape(str(value), quote=True)


def fmt_amount(value: Any) -> str:
    try:
        return f"{float(value):,.8f}"
    except Exception:
        return "0.00000000"


def short_hash(value: Any, chars: int = 12) -> str:
    text = str(value)
    if len(text) <= chars * 2 + 3:
        return h(text)
    return h(text[:chars] + "..." + text[-chars:])


def explorer_layout(title: str, body: str) -> str:
    return f"""<!doctype html>
<html lang="de">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{h(title)} - Logicoin Explorer</title>
  <style>
    :root {{
      color-scheme: dark;
      --bg: #0b1020;
      --card: #151b2e;
      --card2: #101729;
      --text: #edf2ff;
      --muted: #9aa8c7;
      --accent: #69f0ae;
      --danger: #ff6b6b;
      --line: #26314f;
      --link: #7cc7ff;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: Segoe UI, Arial, sans-serif;
      background: radial-gradient(circle at top, #17213b, var(--bg));
      color: var(--text);
    }}
    header {{
      padding: 22px 28px;
      border-bottom: 1px solid var(--line);
      background: rgba(8, 12, 24, 0.82);
      position: sticky;
      top: 0;
      backdrop-filter: blur(8px);
      z-index: 10;
    }}
    header h1 {{
      margin: 0 0 8px 0;
      font-size: 24px;
      letter-spacing: 0.3px;
    }}
    nav a {{
      color: var(--link);
      text-decoration: none;
      margin-right: 16px;
      font-weight: 600;
    }}
    nav a:hover {{ text-decoration: underline; }}
    main {{
      max-width: 1180px;
      margin: 0 auto;
      padding: 26px;
    }}
    .grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
      gap: 16px;
      margin-bottom: 20px;
    }}
    .card {{
      background: linear-gradient(180deg, var(--card), var(--card2));
      border: 1px solid var(--line);
      border-radius: 14px;
      padding: 18px;
      box-shadow: 0 8px 24px rgba(0,0,0,0.25);
    }}
    .metric-label {{
      color: var(--muted);
      font-size: 13px;
      margin-bottom: 8px;
    }}
    .metric-value {{
      font-size: 24px;
      font-weight: 750;
      word-break: break-word;
    }}
    .ok {{ color: var(--accent); }}
    .bad {{ color: var(--danger); }}
    table {{
      width: 100%;
      border-collapse: collapse;
      overflow: hidden;
      border-radius: 12px;
      background: var(--card2);
      border: 1px solid var(--line);
    }}
    th, td {{
      padding: 11px 12px;
      border-bottom: 1px solid var(--line);
      text-align: left;
      vertical-align: top;
      font-size: 14px;
    }}
    th {{
      color: var(--muted);
      background: rgba(255,255,255,0.035);
      font-size: 12px;
      text-transform: uppercase;
      letter-spacing: 0.04em;
    }}
    tr:hover td {{ background: rgba(255,255,255,0.025); }}
    a {{ color: var(--link); }}
    .muted {{ color: var(--muted); }}
    .mono {{
      font-family: Consolas, Menlo, monospace;
      word-break: break-all;
    }}
    .section-title {{
      margin: 26px 0 12px 0;
      font-size: 20px;
    }}
    .pill {{
      display: inline-block;
      padding: 4px 8px;
      border: 1px solid var(--line);
      border-radius: 999px;
      background: rgba(255,255,255,0.04);
      color: var(--muted);
      font-size: 12px;
    }}
    form {{
      display: flex;
      gap: 10px;
      flex-wrap: wrap;
      margin: 12px 0;
    }}
    input {{
      background: #0b1020;
      color: var(--text);
      border: 1px solid var(--line);
      border-radius: 9px;
      padding: 10px 12px;
      min-width: 280px;
    }}
    button {{
      background: var(--accent);
      color: #071018;
      border: 0;
      border-radius: 9px;
      padding: 10px 14px;
      font-weight: 700;
      cursor: pointer;
    }}
    pre {{
      white-space: pre-wrap;
      background: #050814;
      border: 1px solid var(--line);
      border-radius: 12px;
      padding: 14px;
      overflow-x: auto;
    }}
  </style>
</head>
<body>
  <header>
    <h1>Logicoin / LOGIC Explorer</h1>
    <nav>
      <a href="/explorer">Übersicht</a>
      <a href="/explorer/blocks">Blöcke</a>
      <a href="/explorer/mempool">Mempool</a>
      <a href="/explorer/balances">Balances</a>
      <a href="/info">JSON Info</a>
    </nav>
  </header>
  <main>
    {body}
  </main>
</body>
</html>"""


def explorer_home(chain: list[dict[str, Any]], mempool: list[dict[str, Any]], chain_ok: bool, chain_reason: str) -> str:
    tip = chain[-1]
    params = get_network_params(chain)
    diff_stats = get_recent_block_time_stats(chain)
    stats = calculate_chain_stats(chain)
    balances = calculate_balances(chain)
    total_supply = sum(float(v) for v in balances.values())

    body = f"""
    <div class="grid">
      <div class="card"><div class="metric-label">Blockhöhe</div><div class="metric-value">#{h(tip.get('index'))}</div></div>
      <div class="card"><div class="metric-label">Chain-Status</div><div class="metric-value {'ok' if chain_ok else 'bad'}">{'gültig' if chain_ok else 'ungültig'}</div><div class="muted">{h(chain_reason)}</div></div>
      <div class="card"><div class="metric-label">Nächste Difficulty</div><div class="metric-value">{h(str(params.get('next_difficulty_bits')) + ' Bits' if params.get('difficulty_rule') in {'bits-v2', 'bits-v3-fast'} else params.get('next_difficulty'))}</div></div>
      <div class="card"><div class="metric-label">Mempool</div><div class="metric-value">{len(mempool)} TXs</div></div>
      <div class="card"><div class="metric-label">Bestätigte TXs</div><div class="metric-value">{h(stats.get('transfer_transactions'))}</div></div>
      <div class="card"><div class="metric-label">Gesamt-Supply</div><div class="metric-value">{fmt_amount(total_supply)} {TICKER}</div></div>
    </div>

    <h2 class="section-title">Netzwerk</h2>
    <div class="grid">
      <div class="card"><div class="metric-label">Coin</div><div class="metric-value">{COIN_NAME}</div><div class="muted">Ticker {TICKER}</div></div>
      <div class="card"><div class="metric-label">Node-Version</div><div class="metric-value">v{VERSION}</div><div class="muted">{h(ALGORITHM)}</div></div>
      <div class="card"><div class="metric-label">Blockreward</div><div class="metric-value">{fmt_amount(params.get('block_reward'))}</div><div class="muted">{TICKER}</div></div>
      <div class="card"><div class="metric-label">Ø Blockzeit</div><div class="metric-value">{float(stats.get('average_block_time_seconds', 0.0)):.2f}s</div><div class="muted">letzter Abschnitt: {float(diff_stats.get('avg_time_last_interval', 0.0)):.2f}s</div></div>
      <div class="card"><div class="metric-label">Ø Hashrate</div><div class="metric-value">{float(stats.get('average_hashrate_hs', 0.0)):,.0f} H/s</div><div class="muted">letzter Abschnitt: {float(diff_stats.get('avg_hashrate_last_interval', 0.0)):,.0f} H/s</div></div>
      <div class="card"><div class="metric-label">Fees gesamt</div><div class="metric-value">{fmt_amount(stats.get('total_fees'))}</div><div class="muted">{TICKER}</div></div>
    </div>

    <h2 class="section-title">Letzte Blöcke</h2>
    {blocks_table(chain[-10:][::-1])}
    """
    return explorer_layout("Übersicht", body)


def blocks_table(blocks: list[dict[str, Any]]) -> str:
    rows = []
    for block in blocks:
        tx_count = len([tx for tx in block.get("transactions", []) if isinstance(tx, dict) and tx.get("type") == "transfer"])
        index = int(block.get("index", 0))
        rows.append(f"""
          <tr>
            <td><a href="/explorer/block?height={index}">#{index}</a></td>
            <td class="mono">{short_hash(block.get('hash'))}</td>
            <td class="mono"><a href="/explorer/address?address={h(block.get('miner_address'))}">{h(block.get('miner_address'))}</a></td>
            <td>{fmt_amount(block.get('reward'))} {TICKER}</td>
            <td>{h(str(block.get('difficulty_bits')) + ' Bits' if block.get('difficulty_rule') in {'bits-v2', 'bits-v3-fast'} else block.get('difficulty'))}</td>
            <td>{tx_count}</td>
            <td>{float(block.get('mining_time_seconds', 0.0)):.2f}s</td>
          </tr>
        """)
    return f"""
    <table>
      <thead>
        <tr>
          <th>Höhe</th>
          <th>Hash</th>
          <th>Miner</th>
          <th>Reward</th>
          <th>Diff</th>
          <th>TXs</th>
          <th>Zeit</th>
        </tr>
      </thead>
      <tbody>
        {''.join(rows) if rows else '<tr><td colspan="7" class="muted">Keine Blöcke.</td></tr>'}
      </tbody>
    </table>
    """


def explorer_blocks(chain: list[dict[str, Any]]) -> str:
    body = f"""
    <h2 class="section-title">Alle Blöcke</h2>
    {blocks_table(chain[::-1])}
    """
    return explorer_layout("Blöcke", body)


def txs_table(txs: list[dict[str, Any]]) -> str:
    rows = []
    for tx in txs:
        rows.append(f"""
          <tr>
            <td class="mono">{short_hash(tx.get('txid'))}</td>
            <td class="mono"><a href="/explorer/address?address={h(tx.get('from'))}">{h(tx.get('from'))}</a></td>
            <td class="mono"><a href="/explorer/address?address={h(tx.get('to'))}">{h(tx.get('to'))}</a></td>
            <td>{fmt_amount(tx.get('amount'))} {TICKER}</td>
            <td>{fmt_amount(tx.get('fee'))} {TICKER}</td>
            <td>{h(tx.get('nonce'))}</td>
            <td>{h(tx.get('memo', ''))}</td>
          </tr>
        """)
    return f"""
    <table>
      <thead>
        <tr>
          <th>TXID</th>
          <th>Von</th>
          <th>An</th>
          <th>Amount</th>
          <th>Fee</th>
          <th>Nonce</th>
          <th>Memo</th>
        </tr>
      </thead>
      <tbody>
        {''.join(rows) if rows else '<tr><td colspan="7" class="muted">Keine Transaktionen.</td></tr>'}
      </tbody>
    </table>
    """


def explorer_block(chain: list[dict[str, Any]], height_text: str) -> str:
    try:
        height = int(height_text)
    except Exception:
        height = -1

    block = next((b for b in chain if int(b.get("index", -1)) == height), None)

    if block is None:
        return explorer_layout("Block nicht gefunden", f'<div class="card"><h2>Block nicht gefunden</h2><p>Höhe: {h(height_text)}</p></div>')

    txs = [tx for tx in block.get("transactions", []) if isinstance(tx, dict) and tx.get("type") == "transfer"]
    body = f"""
    <h2 class="section-title">Block #{h(block.get('index'))}</h2>
    <div class="grid">
      <div class="card"><div class="metric-label">Hash</div><div class="metric-value mono">{h(block.get('hash'))}</div></div>
      <div class="card"><div class="metric-label">Previous Hash</div><div class="metric-value mono">{h(block.get('previous_hash'))}</div></div>
      <div class="card"><div class="metric-label">Miner</div><div class="metric-value mono"><a href="/explorer/address?address={h(block.get('miner_address'))}">{h(block.get('miner_address'))}</a></div></div>
      <div class="card"><div class="metric-label">Reward</div><div class="metric-value">{fmt_amount(block.get('reward'))} {TICKER}</div></div>
      <div class="card"><div class="metric-label">Difficulty</div><div class="metric-value">{h(str(block.get('difficulty_bits')) + ' Bits' if block.get('difficulty_rule') in {'bits-v2', 'bits-v3-fast'} else block.get('difficulty'))}</div></div>
      <div class="card"><div class="metric-label">Nonce</div><div class="metric-value">{h(block.get('nonce'))}</div></div>
      <div class="card"><div class="metric-label">Mining-Zeit</div><div class="metric-value">{float(block.get('mining_time_seconds', 0.0)):.2f}s</div></div>
      <div class="card"><div class="metric-label">Hashrate</div><div class="metric-value">{float(block.get('hashrate_hs', 0.0)):,.2f} H/s</div></div>
    </div>
    <h2 class="section-title">Transaktionen</h2>
    {txs_table(txs)}
    <h2 class="section-title">Rohdaten</h2>
    <pre>{h(json.dumps(block, indent=2, ensure_ascii=False))}</pre>
    """
    return explorer_layout(f"Block #{height}", body)


def explorer_mempool(mempool: list[dict[str, Any]]) -> str:
    body = f"""
    <h2 class="section-title">Mempool</h2>
    <div class="grid">
      <div class="card"><div class="metric-label">Offene Transaktionen</div><div class="metric-value">{len(mempool)}</div></div>
    </div>
    {txs_table(mempool)}
    """
    return explorer_layout("Mempool", body)


def explorer_balances(chain: list[dict[str, Any]]) -> str:
    balances = calculate_balances(chain)
    rows = []
    for address, balance in sorted(balances.items(), key=lambda item: item[1], reverse=True):
        rows.append(f"""
          <tr>
            <td class="mono"><a href="/explorer/address?address={h(address)}">{h(address)}</a></td>
            <td>{fmt_amount(balance)} {TICKER}</td>
          </tr>
        """)
    body = f"""
    <h2 class="section-title">Balances</h2>
    <table>
      <thead><tr><th>Adresse</th><th>Balance</th></tr></thead>
      <tbody>{''.join(rows) if rows else '<tr><td colspan="2" class="muted">Keine Balances.</td></tr>'}</tbody>
    </table>
    """
    return explorer_layout("Balances", body)


def explorer_address(chain: list[dict[str, Any]], mempool: list[dict[str, Any]], address: str) -> str:
    info = get_address_info(chain, mempool, address)
    related_txs = []
    mined = []

    for block in chain:
        if block.get("miner_address") == address:
            mined.append(block)
        for tx in block.get("transactions", []):
            if isinstance(tx, dict) and tx.get("type") == "transfer":
                if tx.get("from") == address or tx.get("to") == address:
                    related_txs.append(dict(tx, confirmed_in_block=block.get("index")))

    pending_txs = []
    for tx in mempool:
        if tx.get("from") == address or tx.get("to") == address:
            pending_txs.append(tx)

    body = f"""
    <h2 class="section-title">Adresse</h2>
    <form action="/explorer/address" method="get">
      <input name="address" placeholder="logic1_..." value="{h(address)}">
      <button type="submit">Suchen</button>
    </form>
    <div class="grid">
      <div class="card"><div class="metric-label">Adresse</div><div class="metric-value mono">{h(address)}</div></div>
      <div class="card"><div class="metric-label">Bestätigt</div><div class="metric-value">{fmt_amount(info.get('confirmed_balance'))} {TICKER}</div></div>
      <div class="card"><div class="metric-label">Spendable</div><div class="metric-value">{fmt_amount(info.get('spendable_balance'))} {TICKER}</div></div>
      <div class="card"><div class="metric-label">Pending rein/raus</div><div class="metric-value">{fmt_amount(info.get('pending_in'))} / {fmt_amount(info.get('pending_out'))}</div></div>
      <div class="card"><div class="metric-label">Nächster Nonce</div><div class="metric-value">{h(info.get('next_nonce'))}</div></div>
      <div class="card"><div class="metric-label">Geminte Blöcke</div><div class="metric-value">{len(mined)}</div></div>
    </div>

    <h2 class="section-title">Offene Transaktionen</h2>
    {txs_table(pending_txs)}

    <h2 class="section-title">Bestätigte Transaktionen</h2>
    {txs_table(related_txs)}

    <h2 class="section-title">Geminte Blöcke</h2>
    {blocks_table(mined[::-1])}
    """
    return explorer_layout("Adresse", body)

class LogicoinNodeHandler(BaseHTTPRequestHandler):
    server_version = "LogicoinNode/0.12.15.3"
    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args: Any) -> None:
        print(f"{self.address_string()} - {format % args}")

    def _handle_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)

        if path == "/wait_tip":
            known_hash = str(
                query.get("hash", [""])[0]
            )
            try:
                known_height = int(
                    query.get("height", ["-1"])[0]
                )
            except Exception:
                known_height = -1
            try:
                wait_seconds = float(
                    query.get("timeout", ["20"])[0]
                )
            except Exception:
                wait_seconds = 20.0

            wait_seconds = max(
                0.1,
                min(25.0, wait_seconds),
            )
            deadline = (
                time.monotonic() + wait_seconds
            )
            changed = False
            chain = load_chain()
            tip = chain[-1]

            while time.monotonic() < deadline:
                tip = chain[-1]
                changed = (
                    str(tip.get("hash", ""))
                    != known_hash
                    or int(tip.get("index", -1))
                    != known_height
                )

                if changed:
                    break

                time.sleep(0.04)
                chain = load_chain()

            params = get_network_params(chain)
            json_response(self, 200, {
                "ok": True,
                "changed": changed,
                "tip": tip,
                "height": int(
                    tip.get("index", 0)
                ),
                "network_params": params,
            })
            return

        chain = load_chain()
        mempool = load_mempool()
        chain_ok, chain_reason = validate_chain(chain)
        tip = chain[-1]
        balances = calculate_balances(chain)
        stats = calculate_chain_stats(chain)
        params = get_network_params(chain)
        difficulty_stats = get_recent_block_time_stats(chain)
        config = load_config()

        if path == "/asset_registry":
            registry = public_asset_registry()
            json_response(
                self,
                200,
                registry,
            )
            return

        if path == "/explorer":
            html_response(self, 200, explorer_home(chain, mempool, chain_ok, chain_reason))
            return

        if path == "/explorer/blocks":
            html_response(self, 200, explorer_blocks(chain))
            return

        if path == "/explorer/block":
            height = query.get("height", [str(tip.get("index", 0))])[0]
            html_response(self, 200, explorer_block(chain, height))
            return

        if path == "/explorer/mempool":
            html_response(self, 200, explorer_mempool(mempool))
            return

        if path == "/explorer/balances":
            html_response(self, 200, explorer_balances(chain))
            return

        if path == "/explorer/address":
            address = query.get("address", [""])[0]
            html_response(self, 200, explorer_address(chain, mempool, address))
            return

        if path == "/" or path == "/info":
            json_response(self, 200, {
                "project": PROJECT_NAME,
                "coin": COIN_NAME,
                "ticker": TICKER,
                "version": VERSION,
                "network_id": NETWORK_ID,
                "network_name": NETWORK_NAME,
                "release_channel": RELEASE_CHANNEL,
                "network_fingerprint": current_network_fingerprint(),
                "node_name": NODE_NAME,
                "process_id": os.getpid(),
                "executable": str(Path(sys.executable).resolve()),
                "node_identity_file": str(NODE_IDENTITY_FILE),
                "lan_ip": local_lan_ip(),
                "lan_url": local_lan_url(),
                "peers_total": len(load_peers()),
                "algorithm": ALGORITHM,
                "cpu_algorithm": ALGORITHM,
                "gpu_algorithm": GPU_ALGORITHM,
                "supported_algorithms": sorted(list(SUPPORTED_ALGORITHMS)),
                "signature_algorithm": "secp256k1-ecdsa-sha256-v1",
                "height": int(tip.get("index", 0)),
                "blocks_total": len(chain),
                "tip_hash": tip.get("hash"),
                "chain_valid": chain_ok,
                "chain_status": chain_reason,
                "network_params": params,
                "difficulty_stats": difficulty_stats,
                "chain_stats": stats,
                "mempool_count": len(mempool),
                "balances": balances,
                "config": config,
                "asset_registry": public_asset_registry(),
                "chain_file": str(CHAIN_FILE),
                "mempool_file": str(MEMPOOL_FILE),
                "config_file": str(CONFIG_FILE),
                "endpoints": [
                    "GET /info",
                    "GET /asset_registry",
                    "GET /network",
                    "GET /peers",
                    "GET /tip",
                    "GET /wait_tip?hash=...&height=...&timeout=20",
                    "GET /params",
                    "GET /config",
                    "GET /mempool",
                    "GET /address?address=logic1_...",
                    "GET /mining_template?miner=logic1_...",
                    "GET /chain",
                    "POST /peers/test",
                    "POST /peers/add",
                    "POST /peers/remove",
                    "POST /sync",
                    "POST /peer/tx",
                    "POST /peer/block",
                    "POST /submit_tx",
                    "POST /submit_block",
                    "POST /reset"
                ]
            })
            return

        if path == "/network":
            json_response(self, 200, network_summary())
            return

        if path == "/peers":
            peers = load_peers()
            statuses = load_peer_status()
            json_response(self, 200, {
                "ok": True,
                "peers": [
                    {"url": peer, **dict(statuses.get(peer, {}))}
                    for peer in peers
                ],
                "peers_total": len(peers),
            })
            return

        if path == "/config":
            json_response(self, 200, {
                "ok": True,
                "config": config,
                "config_file": str(CONFIG_FILE)
            })
            return

        if path == "/params":
            json_response(self, 200, {
                "ok": True,
                "network_params": params,
                "difficulty_stats": difficulty_stats,
            })
            return

        if path == "/tip":
            json_response(self, 200, {
                "ok": True,
                "tip": tip,
                "height": int(tip.get("index", 0)),
                "chain_valid": chain_ok,
                "chain_status": chain_reason,
                "network_params": params,
            })
            return

        if path == "/mempool":
            json_response(self, 200, {
                "ok": True,
                "mempool_count": len(mempool),
                "transactions": mempool,
            })
            return

        if path == "/address":
            address = query.get("address", [""])[0]
            info = get_address_info(chain, mempool, address)
            json_response(self, 200, {
                "ok": True,
                "address_info": info,
            })
            return

        if path == "/mining_template":
            miner = query.get("miner", [DEFAULT_MINER_ADDRESS])[0]
            selected_txs = select_transactions_for_block(chain, mempool)
            total_fees = sum(float(tx.get("fee", 0.0)) for tx in selected_txs)
            json_response(self, 200, {
                "ok": True,
                "tip": tip,
                "height": int(tip.get("index", 0)),
                "chain_valid": chain_ok,
                "chain_status": chain_reason,
                "network_params": params,
                "miner_address": miner,
                "cpu_algorithm": ALGORITHM,
                "gpu_algorithm": GPU_ALGORITHM,
                "supported_algorithms": sorted(list(SUPPORTED_ALGORITHMS)),
                "transactions": selected_txs,
                "transaction_count": len(selected_txs),
                "total_fees": total_fees,
            })
            return

        if path == "/chain":
            json_response(self, 200, {
                "ok": True,
                "chain": chain,
                "blocks_total": len(chain),
                "chain_valid": chain_ok,
                "chain_status": chain_reason,
                "network_params": params,
            })
            return

        json_response(self, 404, {
            "ok": False,
            "error": "Endpoint nicht gefunden."
        })

    def _handle_POST(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path

        content_length = int(self.headers.get("Content-Length", "0"))
        raw_body = self.rfile.read(content_length) if content_length > 0 else b""


        if path == "/peers/test":
            try:
                data = json.loads(raw_body.decode("utf-8")) if raw_body else {}
                peer_url = str(data.get("url", "")).strip()
                timeout = float(load_config().get("peer_request_timeout_seconds", 5.0))
                result = test_peer_connection(peer_url, timeout=timeout)
                json_response(self, 200, result)
            except Exception as exc:
                json_response(self, 400, {"ok": False, "error": str(exc)})
            return

        if path == "/peers/add":
            try:
                data = json.loads(raw_body.decode("utf-8")) if raw_body else {}
                peer_url = str(data.get("url", "")).strip()
                config = load_config()
                added, reason = add_peer(
                    peer_url,
                    self_urls=self_peer_urls(),
                    max_peers=int(config.get("max_peers", 32)),
                )

                peer = normalize_peer_url(peer_url)
                bidirectional = bool(data.get("bidirectional", True))
                callback_url = str(data.get("callback_url") or local_lan_url())

                if bidirectional:
                    def register_back() -> None:
                        try:
                            http_post_json(
                                peer + "/peers/add",
                                {
                                    "url": callback_url,
                                    "bidirectional": False,
                                },
                                timeout=float(config.get("peer_request_timeout_seconds", 5.0)),
                            )
                        except Exception as exc:
                            update_peer_status(peer, last_attempt=time.time(), error=str(exc))
                    threading.Thread(target=register_back, daemon=True).start()

                json_response(self, 200, {
                    "ok": True,
                    "added": added,
                    "message": reason,
                    "peer": peer,
                    "peers": load_peers(),
                })
            except Exception as exc:
                json_response(self, 400, {"ok": False, "error": str(exc)})
            return

        if path == "/peers/remove":
            try:
                data = json.loads(raw_body.decode("utf-8")) if raw_body else {}
                removed, reason = remove_peer(str(data.get("url", "")))
                json_response(self, 200, {
                    "ok": True,
                    "removed": removed,
                    "message": reason,
                    "peers": load_peers(),
                })
            except Exception as exc:
                json_response(self, 400, {"ok": False, "error": str(exc)})
            return

        if path == "/sync":
            try:
                data = json.loads(raw_body.decode("utf-8")) if raw_body else {}
                peer_url = str(data.get("peer", "")).strip()
                result = sync_from_peer(peer_url) if peer_url else sync_all_peers()
                json_response(self, 200, result)
            except Exception as exc:
                json_response(self, 500, {"ok": False, "error": str(exc)})
            return

        if path == "/peer/tx":
            try:
                tx = json.loads(raw_body.decode("utf-8"))
                ok, reason = accept_peer_transaction(tx)
                json_response(self, 200 if ok else 400, {
                    "ok": ok,
                    "accepted": ok,
                    "message": reason if ok else "",
                    "error": "" if ok else reason,
                })
            except Exception as exc:
                json_response(self, 400, {"ok": False, "accepted": False, "error": str(exc)})
            return

        if path == "/peer/block":
            try:
                block = json.loads(raw_body.decode("utf-8"))
                ok, reason = accept_peer_block(block)
                json_response(self, 200 if ok else 409, {
                    "ok": ok,
                    "accepted": ok,
                    "message": reason if ok else "",
                    "error": "" if ok else reason,
                    "height": int(load_chain()[-1].get("index", 0)),
                })
            except Exception as exc:
                json_response(self, 400, {"ok": False, "accepted": False, "error": str(exc)})
            return

        if path == "/submit_tx":
            try:
                tx = json.loads(raw_body.decode("utf-8"))
            except Exception:
                json_response(self, 400, {
                    "ok": False,
                    "accepted": False,
                    "error": "Ungültiges JSON."
                })
                return

            chain = load_chain()
            mempool = load_mempool()

            ok, reason = validate_mempool_tx(chain, mempool, tx)
            if not ok:
                json_response(self, 400, {
                    "ok": False,
                    "accepted": False,
                    "error": reason
                })
                return

            mempool.append(tx)
            save_mempool(mempool)

            print("\nTRANSAKTION IN MEMPOOL AUFGENOMMEN")
            print(f"- TXID: {tx.get('txid')}")
            print(f"- Von: {tx.get('from')}")
            print(f"- An: {tx.get('to')}")
            print(f"- Amount: {tx.get('amount')} {TICKER}")
            print(f"- Fee: {tx.get('fee')} {TICKER}")
            print(f"- Mempool: {len(mempool)} TXs\n")

            broadcast_to_peers("/peer/tx", tx)

            json_response(self, 200, {
                "ok": True,
                "accepted": True,
                "message": "Transaktion im Mempool aufgenommen.",
                "txid": tx.get("txid"),
                "mempool_count": len(mempool),
            })
            return

        if path == "/submit_block":
            try:
                block = json.loads(raw_body.decode("utf-8"))
            except Exception:
                json_response(self, 400, {
                    "ok": False,
                    "accepted": False,
                    "reject_type": "invalid",
                    "stale": False,
                    "error": "Ungültiges JSON.",
                })
                return

            with CHAIN_LOCK:
                chain = load_chain()
                mempool = load_mempool()
                ok, reason = validate_next_block(chain, block, mempool)

                if not ok:
                    reject_type, stale = classify_block_rejection(reason)
                    json_response(self, 409 if stale else 400, {
                        "ok": False,
                        "accepted": False,
                        "reject_type": reject_type,
                        "stale": stale,
                        "error": reason,
                        "height": int(chain[-1].get("index", 0)),
                        "tip_hash": chain[-1].get("hash"),
                        "network_params": get_network_params(chain),
                    })
                    return

                chain.append(block)
                save_chain(chain)

                confirmed_txs = [
                    tx for tx in block.get("transactions", [])
                    if isinstance(tx, dict) and tx.get("type") == "transfer"
                ]
                mempool = remove_confirmed_from_mempool(mempool, confirmed_txs)
                save_mempool(mempool)

                balances = calculate_balances(chain)
                params = get_network_params(chain)
                difficulty_stats = get_recent_block_time_stats(chain)
                total_fees = sum(float(tx.get("fee", 0.0)) for tx in confirmed_txs)

            print("\nBLOCK VOM NETZWERK-MINER AKZEPTIERT")
            print(f"- Block: #{block.get('index')}")
            print(f"- Miner: {block.get('miner_address')}")
            print(f"- Reward: {block.get('reward')} {TICKER}")
            print(f"- TXs: {len(confirmed_txs)}")
            print(f"- Fees: {total_fees:.8f} {TICKER}")
            difficulty_text = (
                f"{block.get('difficulty_bits')} Bits"
                if block.get("difficulty_rule") in {"bits-v2", "bits-v3-fast"}
                else str(block.get("difficulty"))
            )
            print(f"- Difficulty: {difficulty_text}")
            print(f"- Hash: {block.get('hash')}")
            next_difficulty_text = (
                f"{params.get('next_difficulty_bits')} Bits"
                if params.get("difficulty_rule") in {"bits-v2", "bits-v3-fast"}
                else str(params.get("next_difficulty"))
            )
            print(f"- Nächste Difficulty: {next_difficulty_text}\n")

            broadcast_to_peers("/peer/block", block)

            json_response(self, 200, {
                "ok": True,
                "accepted": True,
                "reject_type": None,
                "stale": False,
                "message": "Block akzeptiert und gespeichert.",
                "height": int(block.get("index", 0)),
                "hash": block.get("hash"),
                "miner_balance": balances.get(block.get("miner_address"), 0.0),
                "confirmed_transactions": len(confirmed_txs),
                "fees": total_fees,
                "network_params": params,
                "difficulty_stats": difficulty_stats,
            })
            return

        if path == "/reset":
            reset_chain()
            json_response(self, 200, {
                "ok": True,
                "message": "Lokale Node-Chain und Mempool wurden zurückgesetzt."
            })
            return

        json_response(self, 404, {
            "ok": False,
            "error": "Endpoint nicht gefunden."
        })


    def do_GET(self) -> None:
        try:
            self._handle_GET()
        except Exception as e:
            print(f"\nNODE GET FEHLER: {e}\n")
            try:
                json_response(self, 500, {
                    "ok": False,
                    "error": f"Node GET Fehler: {e}",
                    "version": VERSION,
                })
            except Exception:
                pass

    def do_POST(self) -> None:
        try:
            self._handle_POST()
        except Exception as e:
            print(f"\nNODE POST FEHLER: {e}\n")
            try:
                json_response(self, 500, {
                    "ok": False,
                    "accepted": False,
                    "error": f"Node POST Fehler: {e}",
                    "version": VERSION,
                })
            except Exception:
                pass


def print_start_info() -> None:
    chain = load_chain()
    mempool = load_mempool()
    chain_ok, chain_reason = validate_chain(chain)
    tip = chain[-1]
    params = get_network_params(chain)

    print("=" * 72)
    print(f"{PROJECT_NAME} / {COIN_NAME} Node v{VERSION}")
    print("=" * 72)
    print(f"CPU-Algorithmus: {ALGORITHM}")
    print(f"GPU-Algorithmus: {GPU_ALGORITHM}")
    print(f"Unterstützte Algorithmen: {', '.join(sorted(SUPPORTED_ALGORITHMS))}")
    print(f"Chain-Datei: {CHAIN_FILE}")
    print(f"Mempool-Datei: {MEMPOOL_FILE}")
    print(f"Config-Datei: {CONFIG_FILE}")
    print(f"Blöcke: {len(chain)} | Höhe: #{tip.get('index')}")
    print(f"Mempool: {len(mempool)} TXs")
    print(f"Chain gültig: {'Ja' if chain_ok else 'Nein'} - {chain_reason}")
    print("Difficulty-Modus: automatisch")
    print(
        "Nächste Difficulty: "
        + (
            f"{params.get('next_difficulty_bits')} Bits"
            if params.get("difficulty_rule") in {"bits-v2", "bits-v3-fast"}
            else str(params.get("next_difficulty"))
        )
    )
    if params.get("difficulty_rule") in {"bits-v2", "bits-v3-fast"}:
        print(
            f"Min/Max Difficulty: "
            f"{params.get('min_difficulty_bits')} / "
            f"{params.get('max_difficulty_bits')} Bits"
        )
    else:
        print(
            f"Min/Max Difficulty: "
            f"{params.get('min_difficulty')} / "
            f"{params.get('max_difficulty')}"
        )
    print(f"Blockreward: {params.get('block_reward')} {TICKER}")
    print(f"Min TX Fee: {params.get('min_tx_fee')} {TICKER}")
    print()
    print(f"Netzwerk: {NETWORK_NAME}")
    print(f"Netzwerk-ID: {NETWORK_ID}")
    print(f"Release-Kanal: {RELEASE_CHANNEL}")
    print(f"Node-Name: {NODE_NAME}")
    print(f"Node läuft lokal: http://127.0.0.1:{PORT}")
    print(f"LAN-URL: {local_lan_url()}")
    print(f"Gespeicherte Peers: {len(load_peers())}")
    print(f"Peer-Sync: {'an' if load_config().get('peer_sync_enabled', True) else 'aus'}")
    print("Beenden mit STRG + C")
    print("=" * 72)


def main() -> None:
    configure_utf8_stdio()
    global PORT, BIND_HOST, NODE_NAME

    config = load_config()

    parser = argparse.ArgumentParser(description="Logicoin / LOGIC LAN-Testnet Node v0.12.15.3")
    parser.add_argument("--port", type=int, default=int(config.get("node_port", 8080)))
    parser.add_argument("--host", default=str(config.get("node_bind_host", "0.0.0.0")))
    parser.add_argument("--node-name", default=socket.gethostname())
    parser.add_argument("--no-peer-sync", action="store_true")
    args = parser.parse_args()

    PORT = max(1, min(65535, int(args.port)))
    BIND_HOST = str(args.host)
    NODE_NAME = str(args.node_name).strip() or socket.gethostname()

    # Öffentlich konfigurierte Seed-Nodes werden beim Start automatisch
    # in die Peer-Liste übernommen. Eine leere Liste bleibt erlaubt.
    for seed_url in config.get("seed_nodes", []):
        try:
            add_peer(
                str(seed_url),
                self_urls=self_peer_urls(),
                max_peers=int(config.get("max_peers", 32)),
            )
        except Exception as exc:
            print(f"Seed-Node übersprungen: {seed_url} ({exc})")

    write_node_identity()
    print_start_info()

    sync_thread = None
    if bool(config.get("peer_sync_enabled", True)) and not args.no_peer_sync:
        sync_thread = threading.Thread(target=peer_sync_loop, daemon=True, name="LogicoinPeerSync")
        sync_thread.start()

    server = ThreadingHTTPServer((BIND_HOST, PORT), LogicoinNodeHandler)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nNode wird beendet.")
    finally:
        NODE_STOP_EVENT.set()
        server.server_close()
        remove_node_identity()


if __name__ == "__main__":
    main()
