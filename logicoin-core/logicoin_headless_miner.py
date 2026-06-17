#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import argparse
import datetime
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import deque
from pathlib import Path
from typing import Any, Dict, Tuple

from logicoin_core import (
    TICKER,
    DEFAULT_MINER_ADDRESS,
    DIFFICULTY_RULE_V2,
    DIFFICULTY_RULE_V3,
    create_candidate_block_from_tip,
    mine_candidate_block,
    configure_utf8_stdio,
)

BASE_DIR = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
CPU_MINER_STATS_FILE = BASE_DIR / "logicoin_cpu_miner_stats.json"


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
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        try:
            return json.loads(body)
        except Exception:
            return {"ok": False, "accepted": False, "reject_type": "invalid", "error": body}


def is_stale_rejection(response: Dict[str, Any]) -> bool:
    if bool(response.get("stale")) or str(response.get("reject_type", "")).lower() == "stale":
        return True
    error = str(response.get("error", "")).lower()
    return any(marker in error for marker in (
        "falscher index",
        "previous_hash passt nicht",
        "anderer block gefunden",
        "node-tip",
    ))


class CpuMinerStats:
    def __init__(self, target_percent: int):
        self.started_at = time.time()
        self.target_percent = int(target_percent)
        self.samples: deque[tuple[float, float]] = deque(maxlen=100_000)
        self.accepted = 0
        self.stale = 0
        self.invalid = 0
        self.last_hashrate = 0.0
        self.last_status = "Start"
        self.last_height: int | None = None

    def progress(self, _hashes: int, _elapsed: float, hps: float) -> None:
        now = time.time()
        self.last_hashrate = float(hps)
        self.samples.append((now, float(hps)))
        cutoff = now - 12 * 3600
        while self.samples and self.samples[0][0] < cutoff:
            self.samples.popleft()
        self.write()

    def average(self, seconds: int) -> float | None:
        cutoff = time.time() - seconds
        values = [value for ts, value in self.samples if ts >= cutoff]
        return (sum(values) / len(values)) if values else None

    def write(self) -> None:
        data = {
            "version": "0.12.15.3",
            "time": datetime.datetime.now().isoformat(timespec="seconds"),
            "uptime_seconds": int(time.time() - self.started_at),
            "active": True,
            "target_percent": self.target_percent,
            "current_hashrate_hs": self.last_hashrate,
            "avg_1m_hs": self.average(60),
            "avg_30m_hs": self.average(1800),
            "avg_1h_hs": self.average(3600),
            "accepted": self.accepted,
            "stale": self.stale,
            "invalid": self.invalid,
            "rejected": self.invalid,
            "last_status": self.last_status,
            "last_height": self.last_height,
        }
        temp = CPU_MINER_STATS_FILE.with_suffix(".json.tmp")
        try:
            temp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
            os.replace(temp, CPU_MINER_STATS_FILE)
        except Exception:
            try:
                if temp.exists():
                    temp.unlink()
            except Exception:
                pass


def tip_is_current(node_url: str, tip_hash: str, height: int) -> bool:
    try:
        data = get_json(node_url + "/tip", timeout=3)
        tip = data.get("tip", {})
        return (
            str(tip.get("hash", "")) == str(tip_hash)
            and int(tip.get("index", -1)) == int(height)
        )
    except Exception:
        return True


def mine_once(
    node_url: str,
    miner_address: str,
    cpu_percent: int,
    stats: CpuMinerStats,
) -> Tuple[bool, bool]:
    encoded = urllib.parse.quote(miner_address)
    template = get_json(node_url + f"/mining_template?miner={encoded}")

    if not template.get("ok"):
        print(f"[ERROR] Node-Fehler: {template}", flush=True)
        return False, False

    if not template.get("chain_valid"):
        print(f"[ERROR] Node-Chain ungültig: {template.get('chain_status')}", flush=True)
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

    stats.last_height = int(tip.get("index", 0))
    stats.last_status = f"Mining Tip #{stats.last_height} | Diff {difficulty}"
    stats.write()

    print("=" * 72, flush=True)
    print(
        f"[JOB] Tip #{tip.get('index')} | Difficulty {difficulty_bits} Bits | "
        f"TXs {len(transactions)} | Reward {reward} {TICKER}",
        flush=True,
    )

    candidate = create_candidate_block_from_tip(
        tip_block=tip,
        miner_address=miner_address,
        difficulty=difficulty,
        reward=reward,
        transactions=transactions,
        difficulty_rule=difficulty_rule,
        difficulty_bits=difficulty_bits,
    )

    block = mine_candidate_block(
        candidate,
        verbose=True,
        throttle_target_percent=cpu_percent,
        progress_callback=stats.progress,
    )

    if not tip_is_current(node_url, str(tip.get("hash", "")), int(tip.get("index", 0))):
        stats.stale += 1
        stats.last_status = "STALE vor Submit – neuer Tip"
        stats.write()
        print("[STALE] Node-Tip hat sich geändert. Kein Reject gesendet.", flush=True)
        return False, True

    response = post_json(node_url + "/submit_block", block)

    if response.get("accepted"):
        stats.accepted += 1
        stats.last_status = f"ACCEPTED Block #{response.get('height')}"
        stats.write()
        print(
            f"[ACCEPTED] Block #{response.get('height')} | "
            f"Fees {response.get('fees')} | "
            f"Next bits {response.get('network_params', {}).get('next_difficulty_bits')}",
            flush=True,
        )
        return True, False

    error = str(response.get("error", "Unbekannter Fehler"))
    if is_stale_rejection(response):
        stats.stale += 1
        stats.last_status = f"STALE: {error[:45]}"
        stats.write()
        print(f"[STALE] {error}", flush=True)
        return False, True

    stats.invalid += 1
    stats.last_status = f"INVALID: {error[:45]}"
    stats.write()
    print(f"[INVALID] {error}", flush=True)
    return False, False


def main() -> None:
    configure_utf8_stdio()
    parser = argparse.ArgumentParser(description="Logicoin Headless CPU Miner v0.12.15.3")
    parser.add_argument("--node-url", default="http://127.0.0.1:8080")
    parser.add_argument("--miner-address", default=DEFAULT_MINER_ADDRESS)
    parser.add_argument("--max-stale-retries", type=int, default=10)
    parser.add_argument("--sleep-after-block", type=float, default=0.25)
    parser.add_argument(
        "--cpu-power-profile",
        default="custom",
        choices=["eco", "medium", "high", "ultra", "custom"],
    )
    parser.add_argument(
        "--cpu-percent",
        type=int,
        default=int(os.environ.get("LOGIC_CPU_USAGE_PERCENT", "50")),
    )
    args = parser.parse_args()

    node_url = normalize_node_url(args.node_url)
    miner_address = args.miner_address
    cpu_percent = max(5, min(100, int(args.cpu_percent)))
    stats = CpuMinerStats(cpu_percent)

    print("=" * 72, flush=True)
    print("Logicoin / LOGIC Headless CPU Miner v0.12.15.3", flush=True)
    print("=" * 72, flush=True)
    print(f"Node: {node_url}", flush=True)
    print(f"Miner-Adresse: {miner_address}", flush=True)
    print(f"CPU-Ziel: {cpu_percent}% (Software-Duty-Cycle)", flush=True)

    stale_retries = 0

    while True:
        try:
            ok, stale = mine_once(node_url, miner_address, cpu_percent, stats)

            if ok:
                stale_retries = 0
                time.sleep(max(0.0, float(args.sleep_after_block)))
                continue

            if stale:
                stale_retries += 1
                print(f"[STALE] Neuer Job {stale_retries}/{args.max_stale_retries}", flush=True)
                if stale_retries >= args.max_stale_retries:
                    stale_retries = 0
                    time.sleep(0.2)
                continue

            stale_retries = 0
            time.sleep(2)

        except KeyboardInterrupt:
            stats.last_status = "Gestoppt"
            stats.write()
            print("\n[STOP] Miner beendet.", flush=True)
            break
        except Exception as exc:
            stats.last_status = f"ERROR: {str(exc)[:50]}"
            stats.write()
            print(f"[ERROR] {exc}", flush=True)
            time.sleep(3)


if __name__ == "__main__":
    main()
