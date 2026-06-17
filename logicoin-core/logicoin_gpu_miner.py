#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Logicoin / LOGIC GPU Streaming Miner v0.12.15.3

Dieser Miner ist der erste GPU-Mining-Schritt für LOGIC.

Backends:
- auto: nutzt logicoin_cuda_worker.exe, falls vorhanden, sonst CPU-Fallback
- cuda: verlangt logicoin_cuda_worker.exe
- cpu-fallback: testet den GPU-Mix-Algorithmus auf CPU

Wichtig:
CPU-Fallback ist nur zum Testen.
Echtes NVIDIA-GPU-Mining braucht:
- NVIDIA CUDA Toolkit
- BUILD_CUDA_WORKER_SAFE.bat ausführen
- logicoin_cuda_worker.exe im Logicoin-Ordner
"""

from __future__ import annotations

import argparse
import contextlib
import ctypes
import hashlib
import platform
import queue
import threading
import json
import os
import subprocess
import sys
import time
import datetime
import shutil
from collections import deque
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, Tuple

from logicoin_core import (
    TICKER,
    DEFAULT_MINER_ADDRESS,
    GPU_ALGORITHM,
    DIFFICULTY_RULE_V2,
    DIFFICULTY_RULE_V3,
    create_candidate_block_from_tip,
    make_block_header,
    calculate_block_hash,
    hash_meets_difficulty_bits,
    logic_hash_gpu_mix_base_hex,
    logic_hash_v2_cuda_mix_from_base_hex,
    configure_utf8_stdio,
)

BASE_DIR = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
DATA_DIR = Path(
    os.environ.get(
        "LOGICOIN_DATA_DIR",
        str(BASE_DIR),
    )
).resolve()
DATA_DIR.mkdir(parents=True, exist_ok=True)
GPU_MINER_STATS_FILE = DATA_DIR / "logicoin_gpu_miner_stats.json"

def stats_file_for_device(device: int) -> Path:
    return DATA_DIR / f"logicoin_gpu_miner_stats_gpu{int(device)}.json"



# ========================================================
# MINER DASHBOARD / STATS
# ========================================================

def format_hashrate(hs: float) -> str:
    try:
        value = float(hs)
    except Exception:
        return "--"

    units = [
        ("TH/s", 1_000_000_000_000),
        ("GH/s", 1_000_000_000),
        ("MH/s", 1_000_000),
        ("KH/s", 1_000),
        ("H/s", 1),
    ]

    for unit, factor in units:
        if abs(value) >= factor:
            return f"{value / factor:,.2f} {unit}"
    return f"{value:,.2f} H/s"


def format_duration(seconds: float) -> str:
    seconds = int(max(0, seconds))
    h = seconds // 3600
    m = (seconds % 3600) // 60
    s = seconds % 60
    if h:
        return f"{h:02d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


def now_time() -> str:
    return datetime.datetime.now().strftime("%H:%M:%S")


def safe_percent(v: object) -> str:
    try:
        return f"{int(float(str(v).replace('%','').strip()))}%"
    except Exception:
        return "--"


def safe_celsius(v: object) -> str:
    try:
        return f"{int(float(str(v).strip()))}°C"
    except Exception:
        return "--"


def safe_watts(v: object) -> str:
    try:
        return f"{float(str(v).strip()):.0f} W"
    except Exception:
        return "--"


class _NvmlUtilization(ctypes.Structure):
    _fields_ = [("gpu", ctypes.c_uint), ("memory", ctypes.c_uint)]


class _NvmlMemory(ctypes.Structure):
    _fields_ = [
        ("total", ctypes.c_ulonglong),
        ("free", ctypes.c_ulonglong),
        ("used", ctypes.c_ulonglong),
    ]


_MINER_NVML_LIB: object | None = None
_MINER_NVML_FAILED = False


def _configure_miner_nvml_signatures(lib: object) -> None:
    specs = {
        "nvmlInit_v2": ([], ctypes.c_int),
        "nvmlInit": ([], ctypes.c_int),
        "nvmlDeviceGetHandleByIndex_v2": (
            [ctypes.c_uint, ctypes.POINTER(ctypes.c_void_p)],
            ctypes.c_int,
        ),
        "nvmlDeviceGetHandleByIndex": (
            [ctypes.c_uint, ctypes.POINTER(ctypes.c_void_p)],
            ctypes.c_int,
        ),
        "nvmlDeviceGetName": (
            [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_uint],
            ctypes.c_int,
        ),
        "nvmlDeviceGetTemperature": (
            [
                ctypes.c_void_p,
                ctypes.c_uint,
                ctypes.POINTER(ctypes.c_uint),
            ],
            ctypes.c_int,
        ),
        "nvmlDeviceGetPowerUsage": (
            [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint)],
            ctypes.c_int,
        ),
        "nvmlDeviceGetUtilizationRates": (
            [
                ctypes.c_void_p,
                ctypes.POINTER(_NvmlUtilization),
            ],
            ctypes.c_int,
        ),
        "nvmlDeviceGetMemoryInfo": (
            [
                ctypes.c_void_p,
                ctypes.POINTER(_NvmlMemory),
            ],
            ctypes.c_int,
        ),
    }

    for name, (argtypes, restype) in specs.items():
        fn = getattr(lib, name, None)
        if fn is None:
            continue
        try:
            fn.argtypes = argtypes
            fn.restype = restype
        except Exception:
            pass


def _miner_nvml_library() -> object | None:
    global _MINER_NVML_LIB, _MINER_NVML_FAILED

    if os.name != "nt":
        return None
    if _MINER_NVML_LIB is not None:
        return _MINER_NVML_LIB
    if _MINER_NVML_FAILED:
        return None

    candidates = [
        Path(r"C:\Windows\System32\nvml.dll"),
        Path(r"C:\Program Files\NVIDIA Corporation\NVSMI\nvml.dll"),
    ]

    program_w6432 = os.environ.get("ProgramW6432")
    if program_w6432:
        candidates.append(
            Path(program_w6432) / "NVIDIA Corporation" / "NVSMI" / "nvml.dll"
        )

    for candidate in candidates:
        try:
            if not candidate.exists():
                continue

            lib = ctypes.WinDLL(str(candidate))
            _configure_miner_nvml_signatures(lib)
            init_fn = (
                getattr(lib, "nvmlInit_v2", None)
                or getattr(lib, "nvmlInit", None)
            )
            if init_fn is None:
                continue

            if int(init_fn()) not in (0, 5):
                continue

            _MINER_NVML_LIB = lib
            return lib
        except Exception:
            continue

    _MINER_NVML_FAILED = True
    return None


def read_nvidia_gpu_stats(device: int) -> dict[str, object]:
    """Liest GPU-Sensoren direkt über nvml.dll, ohne nvidia-smi.exe."""
    lib = _miner_nvml_library()
    if lib is None:
        return {"ok": False, "error": "NVML nicht verfügbar", "device": device}

    try:
        handle_fn = (
            getattr(lib, "nvmlDeviceGetHandleByIndex_v2", None)
            or getattr(lib, "nvmlDeviceGetHandleByIndex", None)
        )
        name_fn = getattr(lib, "nvmlDeviceGetName", None)

        if handle_fn is None or name_fn is None:
            return {
                "ok": False,
                "error": "NVML-Funktionen fehlen",
                "device": device,
            }

        handle = ctypes.c_void_p()
        if int(handle_fn(ctypes.c_uint(int(device)), ctypes.byref(handle))) != 0:
            return {
                "ok": False,
                "error": "NVML Device nicht gefunden",
                "device": device,
            }

        name_buffer = ctypes.create_string_buffer(128)
        name = f"NVIDIA GPU {device}"
        try:
            if int(
                name_fn(
                    handle,
                    name_buffer,
                    ctypes.c_uint(len(name_buffer)),
                )
            ) == 0:
                name = name_buffer.value.decode("utf-8", errors="replace")
        except Exception:
            pass

        result: dict[str, object] = {
            "ok": True,
            "device": device,
            "index": device,
            "name": name,
        }

        try:
            value = ctypes.c_uint(0)
            fn = getattr(lib, "nvmlDeviceGetTemperature", None)
            if fn is not None and int(
                fn(handle, ctypes.c_uint(0), ctypes.byref(value))
            ) == 0:
                result["temperature_c"] = float(value.value)
        except Exception:
            pass

        try:
            value = ctypes.c_uint(0)
            fn = getattr(lib, "nvmlDeviceGetPowerUsage", None)
            if fn is not None and int(fn(handle, ctypes.byref(value))) == 0:
                result["power_w"] = float(value.value) / 1000.0
        except Exception:
            pass

        try:
            util = _NvmlUtilization()
            fn = getattr(lib, "nvmlDeviceGetUtilizationRates", None)
            if fn is not None and int(fn(handle, ctypes.byref(util))) == 0:
                result["utilization_percent"] = float(util.gpu)
        except Exception:
            pass

        try:
            memory = _NvmlMemory()
            fn = getattr(lib, "nvmlDeviceGetMemoryInfo", None)
            if fn is not None and int(fn(handle, ctypes.byref(memory))) == 0:
                result["memory_used_mb"] = (
                    float(memory.used) / (1024.0 * 1024.0)
                )
                result["memory_total_mb"] = (
                    float(memory.total) / (1024.0 * 1024.0)
                )
        except Exception:
            pass

        return result
    except Exception as exc:
        return {"ok": False, "error": str(exc), "device": device}


def safe_console_print(text: object = "", *, flush: bool = True) -> None:
    """
    Druckt auch dann weiter, wenn Windows eine eingeschränkte Codepage nutzt.

    Der Fallback ersetzt nur nicht darstellbare Zeichen. Mining darf niemals
    wegen eines dekorativen Rahmenzeichens beendet werden.
    """
    value = str(text)
    try:
        print(value, flush=flush)
        return
    except UnicodeEncodeError:
        pass

    stream = getattr(sys, "stdout", None)
    encoding = getattr(stream, "encoding", None) or "ascii"

    try:
        fallback = value.encode(encoding, errors="replace").decode(
            encoding,
            errors="replace",
        )
    except Exception:
        fallback = value.encode("ascii", errors="replace").decode("ascii")

    try:
        print(fallback, flush=flush)
    except Exception:
        try:
            if stream is not None and hasattr(stream, "buffer"):
                stream.buffer.write((fallback + "\n").encode("utf-8", errors="replace"))
                stream.buffer.flush()
        except Exception:
            pass



class MinerStats:
    def __init__(
        self,
        device: int,
        stats_file: Path | None = None,
    ):
        self.device = int(device)
        self.stats_file = (
            stats_file
            or stats_file_for_device(self.device)
        )
        self.generic_stats_file = (
            self.stats_file.parent
            / "logicoin_gpu_miner_stats.json"
        )
        self.started_at = time.time()

        # CUDA-Rohleistung: nur die Zeit, in der ein Kernel rechnet.
        self.raw_samples: deque[
            tuple[float, float]
        ] = deque(maxlen=250_000)

        # Effektive Arbeit: reale Nonces mit Host-Zeitintervall.
        # Damit zählen Node-Kommunikation und Jobwechsel automatisch
        # als Leerlauf und senken die End-to-End-Hashrate korrekt.
        self.work_intervals: deque[
            tuple[float, float, int, int]
        ] = deque(maxlen=250_000)

        self.activity_samples: deque[
            tuple[float, float, float]
        ] = deque(maxlen=250_000)

        self.accepted_timestamps: deque[
            float
        ] = deque(maxlen=100_000)
        self.proof_timestamps: deque[
            float
        ] = deque(maxlen=100_000)

        self.total_tested_nonces = 0
        self.expected_proofs_total = 0.0
        self.proofs_found = 0
        self.found_work_units = 0.0
        self.accepted_work_units = 0.0

        self.accepted = 0
        self.invalid = 0
        self.rejected = 0
        self.stale = 0
        self.stale_avoided = 0
        self.job_refreshes = 0
        self.jobs = 0

        self.last_height: int | None = None
        self.last_diff: int | None = None
        self.last_diff_bits: int | None = None
        self.last_txs: int | None = None
        self.last_status = "Start"
        self.last_hashrate = 0.0
        self.last_raw_hashrate = 0.0
        self.last_hash: str | None = None
        self.last_nonce: int | None = None
        self.last_accepted_height: int | None = None
        self.last_gpu_stats: dict[str, object] = {
            "ok": False
        }
        self.last_gpu_poll = 0.0
        self.last_dashboard = 0.0

        self.hashrate_measurement = (
            "exact_cuda_end_to_end_counter"
        )
        self.proof_search_selftest_ok = False

    @property
    def current_hs(self) -> float:
        return self.effective_rate_for_seconds(5)

    def _prune(self) -> None:
        now = time.time()
        cutoff = now - (12 * 3600 + 300)

        while (
            self.raw_samples
            and self.raw_samples[0][0] < cutoff
        ):
            self.raw_samples.popleft()

        while (
            self.work_intervals
            and self.work_intervals[0][1] < cutoff
        ):
            self.work_intervals.popleft()

        while (
            self.activity_samples
            and self.activity_samples[0][0] < cutoff
        ):
            self.activity_samples.popleft()

        event_cutoff = now - (7 * 86400)
        while (
            self.accepted_timestamps
            and self.accepted_timestamps[0]
            < event_cutoff
        ):
            self.accepted_timestamps.popleft()

        while (
            self.proof_timestamps
            and self.proof_timestamps[0]
            < event_cutoff
        ):
            self.proof_timestamps.popleft()

    def add_hashrate_sample(
        self,
        hps: float,
    ) -> None:
        """
        CUDA-Rohleistung während der tatsächlichen Kernelzeit.

        Dieser Wert ist nur Diagnose und wird nicht mehr für
        Ertragsprognose oder GPU-gesamt verwendet.
        """
        ts = time.time()
        value = max(0.0, float(hps))
        self.last_raw_hashrate = value
        self.raw_samples.append((ts, value))
        self._prune()

    def add_exact_work(
        self,
        tested_nonces: int,
        interval_seconds: float,
        difficulty_bits: int,
    ) -> None:
        count = max(0, int(tested_nonces))
        if count <= 0:
            return

        end_ts = time.time()
        duration = max(
            0.000001,
            float(interval_seconds),
        )
        start_ts = max(
            self.started_at,
            end_ts - duration,
        )
        bits = max(
            0,
            min(62, int(difficulty_bits)),
        )

        self.work_intervals.append(
            (
                start_ts,
                end_ts,
                count,
                bits,
            )
        )
        self.total_tested_nonces += count
        self.expected_proofs_total += (
            count / float(1 << bits)
        )
        self.last_hashrate = (
            self.effective_rate_for_seconds(5)
        )
        self._prune()

    def _work_in_window(
        self,
        seconds: int,
    ) -> tuple[float, float]:
        now = time.time()
        requested = max(1.0, float(seconds))
        window_start = max(
            self.started_at,
            now - requested,
        )
        denominator = max(
            0.25,
            now - window_start,
        )
        work = 0.0

        for (
            start_ts,
            end_ts,
            count,
            _bits,
        ) in self.work_intervals:
            if end_ts <= window_start:
                continue
            if start_ts >= now:
                continue

            interval_duration = max(
                0.000001,
                end_ts - start_ts,
            )
            overlap_start = max(
                start_ts,
                window_start,
            )
            overlap_end = min(
                end_ts,
                now,
            )
            overlap = max(
                0.0,
                overlap_end - overlap_start,
            )

            if overlap > 0:
                work += (
                    count
                    * overlap
                    / interval_duration
                )

        return work, denominator

    def effective_rate_for_seconds(
        self,
        seconds: int,
    ) -> float:
        work, duration = self._work_in_window(
            seconds
        )
        return (
            work / duration
            if duration > 0
            else 0.0
        )

    def raw_avg_for_seconds(
        self,
        seconds: int,
    ) -> float | None:
        cutoff = time.time() - max(
            1,
            int(seconds),
        )
        values = [
            value
            for ts, value in self.raw_samples
            if ts >= cutoff
        ]
        if not values:
            return None
        return sum(values) / len(values)

    def avg_for_seconds(
        self,
        seconds: int,
    ) -> float | None:
        if not self.work_intervals:
            return None
        return self.effective_rate_for_seconds(
            seconds
        )

    def session_avg(self) -> float:
        elapsed = max(
            0.25,
            time.time() - self.started_at,
        )
        return (
            self.total_tested_nonces
            / elapsed
        )

    def add_activity_sample(
        self,
        active_seconds: float,
        wall_seconds: float,
    ) -> None:
        ts = time.time()
        active = max(
            0.0,
            float(active_seconds),
        )
        wall = max(
            active,
            float(wall_seconds),
        )
        self.activity_samples.append(
            (ts, active, wall)
        )
        self._prune()

    def activity_for_seconds(
        self,
        seconds: int,
    ) -> float | None:
        cutoff = time.time() - max(
            1,
            int(seconds),
        )
        active_total = 0.0
        wall_total = 0.0

        for ts, active, wall in self.activity_samples:
            if ts < cutoff:
                continue
            active_total += active
            wall_total += wall

        if wall_total <= 0:
            return None

        return max(
            0.0,
            min(
                100.0,
                active_total
                / wall_total
                * 100.0,
            ),
        )

    def record_proof_found(
        self,
        difficulty_bits: int,
    ) -> None:
        now = time.time()
        bits = max(
            0,
            min(62, int(difficulty_bits)),
        )
        self.proofs_found += 1
        self.proof_timestamps.append(now)
        self.found_work_units += float(
            1 << bits
        )
        self._prune()

    def record_accepted(
        self,
        difficulty_bits: int,
    ) -> None:
        now = time.time()
        bits = max(
            0,
            min(62, int(difficulty_bits)),
        )
        self.accepted += 1
        self.accepted_timestamps.append(now)
        self.accepted_work_units += float(
            1 << bits
        )
        self._prune()

    @staticmethod
    def _events_per_minute(
        events: deque[float],
        started_at: float,
    ) -> tuple[float, float]:
        now = time.time()
        last_minute = sum(
            1
            for ts in events
            if ts >= now - 60.0
        )
        window = max(
            1.0,
            min(
                60.0,
                now - started_at,
            ),
        )
        rolling = (
            last_minute
            / window
            * 60.0
        )
        session_minutes = max(
            1.0 / 60.0,
            (now - started_at) / 60.0,
        )
        session = len(events) / session_minutes
        return rolling, session

    def accepted_rates(
        self,
    ) -> tuple[float, float]:
        return self._events_per_minute(
            self.accepted_timestamps,
            self.started_at,
        )

    def proof_rates(
        self,
    ) -> tuple[float, float]:
        return self._events_per_minute(
            self.proof_timestamps,
            self.started_at,
        )

    def proof_estimated_hashrate(
        self,
    ) -> float:
        elapsed = max(
            0.25,
            time.time() - self.started_at,
        )
        return self.found_work_units / elapsed

    def accepted_estimated_hashrate(
        self,
    ) -> float:
        elapsed = max(
            0.25,
            time.time() - self.started_at,
        )
        return (
            self.accepted_work_units / elapsed
        )

    def measurement_plausibility(
        self,
    ) -> tuple[bool, str]:
        if not self.proof_search_selftest_ok:
            return (
                False,
                "20-Bit-Selbsttest fehlt",
            )

        expected = max(
            0.0,
            self.expected_proofs_total,
        )
        observed = max(
            0,
            self.proofs_found,
        )

        if expected < 12.0:
            return (
                True,
                "Aufwärmphase",
            )

        if observed == 0:
            return (
                False,
                "Keine Proofs trotz hoher Arbeit",
            )

        # Sehr großzügige Sicherheitsgrenzen:
        # erst große, statistisch praktisch unmögliche
        # Abweichungen sperren die Prognose.
        if (
            expected >= 40.0
            and observed < expected / 10.0
        ):
            return (
                False,
                "Nonce-Zähler und Proofrate unplausibel",
            )

        if (
            observed >= 40
            and observed > expected * 10.0
        ):
            return (
                False,
                "Zu viele Proofs für gemeldete Arbeit",
            )

        return (
            True,
            "Messung plausibel",
        )

    def update_gpu_stats_if_needed(
        self,
        force: bool = False,
    ) -> None:
        ts = time.time()
        if (
            force
            or ts - self.last_gpu_poll >= 5
        ):
            self.last_gpu_stats = (
                read_nvidia_gpu_stats(
                    self.device
                )
            )
            self.last_gpu_poll = ts

    def to_json_dict(
        self,
    ) -> dict[str, object]:
        g = self.last_gpu_stats or {}
        effective_5s = (
            self.effective_rate_for_seconds(5)
        )
        effective_1m = (
            self.effective_rate_for_seconds(60)
            if self.work_intervals
            else None
        )
        effective_session = self.session_avg()
        raw_1m = self.raw_avg_for_seconds(60)
        accepted_1m, accepted_session = (
            self.accepted_rates()
        )
        proof_1m, proof_session = (
            self.proof_rates()
        )
        plausible, plausibility_text = (
            self.measurement_plausibility()
        )

        self.last_hashrate = effective_5s

        return {
            "version": "0.12.15.3",
            "time": datetime.datetime.now().isoformat(
                timespec="seconds"
            ),
            "uptime_seconds": int(
                time.time() - self.started_at
            ),
            "active": True,
            "device": self.device,

            # Kompatibilitätsfelder sind jetzt End-to-End.
            "current_hashrate_hs": effective_5s,
            "session_avg_hs": effective_session,
            "avg_1m_hs": effective_1m,
            "avg_30m_hs": (
                self.effective_rate_for_seconds(
                    30 * 60
                )
                if self.work_intervals
                else None
            ),
            "avg_1h_hs": (
                self.effective_rate_for_seconds(
                    60 * 60
                )
                if self.work_intervals
                else None
            ),
            "avg_6h_hs": (
                self.effective_rate_for_seconds(
                    6 * 60 * 60
                )
                if self.work_intervals
                else None
            ),
            "avg_12h_hs": (
                self.effective_rate_for_seconds(
                    12 * 60 * 60
                )
                if self.work_intervals
                else None
            ),

            "effective_current_hs": effective_5s,
            "effective_avg_1m_hs": effective_1m,
            "effective_session_hs": effective_session,
            "cuda_raw_current_hs": self.last_raw_hashrate,
            "cuda_raw_avg_1m_hs": raw_1m,
            "total_tested_nonces": (
                self.total_tested_nonces
            ),

            "accepted": self.accepted,
            "accepted_per_minute": accepted_1m,
            "accepted_session_per_minute": (
                accepted_session
            ),
            "proofs_found": self.proofs_found,
            "proofs_per_minute": proof_1m,
            "proofs_session_per_minute": proof_session,
            "expected_proofs_from_work": (
                self.expected_proofs_total
            ),
            "proof_estimated_hashrate_hs": (
                self.proof_estimated_hashrate()
            ),
            "accepted_estimated_hashrate_hs": (
                self.accepted_estimated_hashrate()
            ),

            "measurement_plausible": plausible,
            "measurement_plausibility_text": (
                plausibility_text
            ),
            "invalid": self.invalid,
            "rejected": self.invalid,
            "stale": self.stale,
            "stale_avoided": self.stale_avoided,
            "job_refreshes": self.job_refreshes,
            "target_percent": getattr(
                self,
                "target_percent",
                100,
            ),
            "miner_activity_5s_percent": (
                self.activity_for_seconds(5)
            ),
            "miner_activity_60s_percent": (
                self.activity_for_seconds(60)
            ),
            "miner_activity_session_percent": (
                self.activity_for_seconds(
                    max(
                        1,
                        int(
                            time.time()
                            - self.started_at
                        ),
                    )
                )
            ),
            "jobs": self.jobs,
            "last_height": self.last_height,
            "last_diff": self.last_diff,
            "last_diff_bits": self.last_diff_bits,
            "last_txs": self.last_txs,
            "last_status": self.last_status,
            "last_hash": self.last_hash,
            "last_nonce": self.last_nonce,
            "last_accepted_height": (
                self.last_accepted_height
            ),
            "hashrate_measurement": (
                self.hashrate_measurement
            ),
            "proof_search_selftest_ok": (
                self.proof_search_selftest_ok
            ),
            "gpu": g,
        }

    def write_stats_file(
        self,
    ) -> None:
        try:
            payload = json.dumps(
                self.to_json_dict(),
                indent=2,
                ensure_ascii=False,
            )
            self.stats_file.write_text(
                payload,
                encoding="utf-8",
            )
            self.generic_stats_file.write_text(
                payload,
                encoding="utf-8",
            )
        except Exception:
            pass

    def print_dashboard(
        self,
        node_url: str,
        backend_name: str,
        force: bool = False,
    ) -> None:
        ts = time.time()
        if (
            not force
            and ts - self.last_dashboard < 5
        ):
            return

        self.last_dashboard = ts
        self.update_gpu_stats_if_needed(
            force=force
        )

        g = self.last_gpu_stats or {}
        gpu_name = (
            str(
                g.get(
                    "name",
                    f"GPU {self.device}",
                )
            )
            if g.get("ok")
            else f"GPU {self.device}"
        )
        temp = safe_celsius(
            g.get("temperature_c")
        )
        power = safe_watts(
            g.get("power_w")
        )
        util = safe_percent(
            g.get("utilization_percent")
        )
        mem_used = g.get(
            "memory_used_mb",
            "--",
        )
        mem_total = g.get(
            "memory_total_mb",
            "--",
        )

        def effective_label(
            seconds: int,
        ) -> str:
            value = self.avg_for_seconds(
                seconds
            )
            return (
                format_hashrate(value)
                if value is not None
                else "--"
            )

        accepted_1m, _ = self.accepted_rates()
        plausible, plausibility_text = (
            self.measurement_plausibility()
        )

        width = 78
        line = "─" * width

        safe_console_print(
            "\n┌" + line + "┐",
            flush=True,
        )
        safe_console_print(
            f"│ LOGIC GPU Miner v0.12.15.3"
            f"{' ' * 29}{now_time():>8} │",
            flush=True,
        )
        safe_console_print(
            "├" + line + "┤",
            flush=True,
        )

        difficulty_text = (
            f"{self.last_diff_bits}b"
            if self.last_diff_bits is not None
            else str(self.last_diff or "-")
        )

        safe_console_print(
            f"│ Node: {node_url:<24} "
            f"Height: #{str(self.last_height or 0):<5} "
            f"Diff: {difficulty_text:>4} "
            f"TXs: {str(self.last_txs or 0):>3} │",
            flush=True,
        )

        miner_duty = self.activity_for_seconds(5)
        duty_text = (
            f"{miner_duty:.0f}%"
            if miner_duty is not None
            else "--"
        )

        safe_console_print(
            f"│ GPU: {gpu_name[:27]:<27} "
            f"Temp: {temp:<6} Power: {power:<7} "
            f"NVML: {util:<5} Duty: {duty_text:<4} │",
            flush=True,
        )
        safe_console_print(
            f"│ VRAM: {str(mem_used):>5} / "
            f"{str(mem_total):<5} MB  "
            f"Backend: {backend_name:<12} "
            f"Uptime: "
            f"{format_duration(ts - self.started_at):>8} │",
            flush=True,
        )
        safe_console_print(
            "├" + line + "┤",
            flush=True,
        )
        safe_console_print(
            f"│ Effektiv 5s: "
            f"{format_hashrate(self.current_hs):<14} "
            f"Effektiv Session: "
            f"{format_hashrate(self.session_avg()):<15} │",
            flush=True,
        )
        safe_console_print(
            f"│ Effektiv 1m: "
            f"{effective_label(60):<14} "
            f"CUDA roh: "
            f"{format_hashrate(self.last_raw_hashrate):<15} │",
            flush=True,
        )
        safe_console_print(
            f"│ Accepted/min: {accepted_1m:>8.2f} "
            f"Proofs: {self.proofs_found:<6} "
            f"Erwartet aus Arbeit: "
            f"{self.expected_proofs_total:>9.2f} │",
            flush=True,
        )
        safe_console_print(
            f"│ Messung: "
            f"{('OK' if plausible else 'GESPERRT'):<8} "
            f"{plausibility_text[:55]:<55} │",
            flush=True,
        )
        safe_console_print(
            "├" + line + "┤",
            flush=True,
        )
        safe_console_print(
            f"│ Accepted: {self.accepted:<5} "
            f"Stale: {self.stale:<5} "
            f"Avoided: {self.stale_avoided:<5} "
            f"Invalid: {self.invalid:<5} │",
            flush=True,
        )
        safe_console_print(
            f"│ Status: "
            f"{self.last_status[:68]:<68} │",
            flush=True,
        )
        safe_console_print(
            "└" + line + "┘\n",
            flush=True,
        )

        self.write_stats_file()


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
            return {"accepted": False, "ok": False, "error": body}


SUBMIT_LOCK_FILE = DATA_DIR / "logicoin_gpu_submit.lock"


@contextlib.contextmanager
def local_submit_lock(timeout_seconds: float = 3.0):
    """
    Cross-process lock for GPU miners on the same PC.

    Two local GPUs may find a candidate nearly simultaneously. The lock lets
    only one miner re-check the node tip and submit at a time, preventing the
    second local miner from sending a block that is already stale.
    """
    handle = open(SUBMIT_LOCK_FILE, "a+b")
    acquired = False
    deadline = time.monotonic() + max(0.1, float(timeout_seconds))

    try:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()

        while time.monotonic() < deadline:
            try:
                handle.seek(0)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(
                        handle.fileno(),
                        msvcrt.LK_NBLCK,
                        1,
                    )
                else:
                    import fcntl
                    fcntl.flock(
                        handle.fileno(),
                        fcntl.LOCK_EX | fcntl.LOCK_NB,
                    )
                acquired = True
                break
            except (OSError, BlockingIOError):
                time.sleep(0.005)

        if not acquired:
            raise TimeoutError(
                "Lokale GPU-Submit-Sperre konnte nicht rechtzeitig "
                "übernommen werden."
            )

        yield
    finally:
        if acquired:
            try:
                handle.seek(0)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(
                        handle.fileno(),
                        msvcrt.LK_UNLCK,
                        1,
                    )
                else:
                    import fcntl
                    fcntl.flock(
                        handle.fileno(),
                        fcntl.LOCK_UN,
                    )
            except Exception:
                pass

        try:
            handle.close()
        except Exception:
            pass


def choose_next_batch_size(
    current_count: int,
    maximum_count: int,
    active_seconds: float,
    target_seconds: float = 0.10,
) -> int:
    """
    Keeps a CUDA batch near 100 ms.

    Short batches notice a new node tip much faster, while the persistent CUDA
    worker avoids the old process-start overhead.
    """
    current = max(8192, int(current_count))
    maximum = max(8192, int(maximum_count))
    elapsed = max(0.001, float(active_seconds))
    target = max(0.03, min(0.25, float(target_seconds)))

    ideal = int(current * target / elapsed)

    # Limit changes to 2x per step to avoid unstable oscillation.
    lower = max(8192, current // 2)
    upper = min(maximum, current * 2)

    return max(
        8192,
        min(
            maximum,
            max(lower, min(upper, ideal)),
        ),
    )


def submit_candidate_with_stale_guard(
    node_url: str,
    tip_hash: str,
    tip_height: int,
    candidate: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Serializes local GPU submissions and checks the tip immediately before POST.
    A candidate that is already outdated is not sent to the node.
    """
    with local_submit_lock():
        if not tip_is_current(
            node_url,
            tip_hash,
            tip_height,
        ):
            return {
                "accepted": False,
                "ok": False,
                "stale_avoided": True,
                "reject_type": "stale_avoided",
                "error": "Tip änderte sich vor dem Submit.",
            }

        return post_json(
            node_url + "/submit_block",
            candidate,
        )


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


class NodeTipWatcher:
    """
    Wartet per Long-Poll auf einen neuen Node-Tip.

    Dadurch entfallen die alten /tip-Abfragen alle 120 ms. Der CUDA-Worker
    kann ohne HTTP-Unterbrechung weiterrechnen und bekommt einen neuen Block
    trotzdem praktisch sofort mit.
    """

    def __init__(
        self,
        node_url: str,
        tip_hash: str,
        height: int,
    ) -> None:
        self.node_url = node_url.rstrip("/")
        self.tip_hash = str(tip_hash)
        self.height = int(height)
        self.changed_event = threading.Event()
        self.stop_event = threading.Event()
        self.thread: threading.Thread | None = None

    def start(self) -> None:
        if self.thread is not None:
            return

        self.thread = threading.Thread(
            target=self._run,
            daemon=True,
            name=f"logicoin-tip-watch-{self.height}",
        )
        self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()

    def changed(self) -> bool:
        return self.changed_event.is_set()

    def _run(self) -> None:
        query = urllib.parse.urlencode({
            "hash": self.tip_hash,
            "height": self.height,
            "timeout": 20,
        })
        url = (
            self.node_url
            + "/wait_tip?"
            + query
        )

        while not self.stop_event.is_set():
            try:
                data = get_json(
                    url,
                    timeout=24,
                )

                if bool(data.get("changed")):
                    self.changed_event.set()
                    return
            except Exception:
                # Kompatibler Fallback, falls ein alter Node den
                # Long-Poll-Endpunkt noch nicht kennt.
                if not tip_is_current(
                    self.node_url,
                    self.tip_hash,
                    self.height,
                ):
                    self.changed_event.set()
                    return

                self.stop_event.wait(0.25)


def find_cuda_worker() -> Path | None:
    """
    v0.12.15.3:
    Sucht logicoin_cuda_worker.exe robuster.

    Problem:
    Nach EXE-Build läuft LogicoinGpuMiner.exe meist aus dem dist-Ordner,
    während logicoin_cuda_worker.exe oft im Hauptordner gebaut wurde.
    """
    names = [
        "logicoin_cuda_worker.exe",
        "LogicoinCudaWorker.exe",
        "logicoin_cuda_worker",
    ]

    dirs = [
        BASE_DIR,
        BASE_DIR / "dist",
        BASE_DIR.parent,
        BASE_DIR.parent / "dist",
        Path.cwd(),
        Path.cwd() / "dist",
        Path.cwd().parent,
    ]

    candidates: list[Path] = []
    for d in dirs:
        for name in names:
            candidates.append(d / name)

    seen: set[str] = set()
    for candidate in candidates:
        key = str(candidate).lower()
        if key in seen:
            continue
        seen.add(key)
        if candidate.exists():
            return candidate

    return None


def parse_scan_response(text: str) -> dict[str, object]:
    """
    Parses v0.12.15.3 worker responses.

    Compatible forms:
      FOUND nonce hash
      FOUND nonce hash tested active_ms
      NONE
      NONE tested active_ms
    """
    line = str(text).strip()
    parts = line.split()

    if not parts:
        return {
            "found": False,
            "nonce": None,
            "hash": None,
            "tested": 0,
            "active_ms": 0.0,
            "raw": line,
        }

    kind = parts[0].upper()

    try:
        if kind == "FOUND" and len(parts) >= 3:
            return {
                "found": True,
                "nonce": int(parts[1]),
                "hash": parts[2].strip(),
                "tested": (
                    int(parts[3])
                    if len(parts) >= 4
                    else 0
                ),
                "active_ms": (
                    float(parts[4])
                    if len(parts) >= 5
                    else 0.0
                ),
                "raw": line,
            }

        if kind == "NONE":
            return {
                "found": False,
                "nonce": None,
                "hash": None,
                "tested": (
                    int(parts[1])
                    if len(parts) >= 2
                    else 0
                ),
                "active_ms": (
                    float(parts[2])
                    if len(parts) >= 3
                    else 0.0
                ),
                "raw": line,
            }
    except (TypeError, ValueError):
        pass

    raise ValueError(
        f"Ungültige CUDA-SCAN-Antwort: {line}"
    )


def parse_found_line(text: str) -> Tuple[int | None, str | None]:
    try:
        parsed = parse_scan_response(text)
    except Exception:
        return None, None

    if not bool(parsed.get("found")):
        return None, None

    try:
        return (
            int(parsed.get("nonce")),
            str(parsed.get("hash") or ""),
        )
    except Exception:
        return None, None


def suppress_windows_error_dialogs() -> None:
    """
    Unterdrückt Windows-Loader-/WER-Dialoge in diesem Prozess und seinen
    Kindprozessen. Ein Worker-Fehler landet dadurch im Log und blockiert
    nicht mehr mit einem sichtbaren 0xc0000142-Fenster.
    """
    if os.name != "nt":
        return

    try:
        sem_failcriticalerrors = 0x0001
        sem_nogpfaulterrorbox = 0x0002
        sem_noopenfileerrorbox = 0x8000
        ctypes.windll.kernel32.SetErrorMode(
            sem_failcriticalerrors
            | sem_nogpfaulterrorbox
            | sem_noopenfileerrorbox
        )
    except Exception:
        pass


def hidden_worker_process_kwargs() -> dict[str, object]:
    kwargs: dict[str, object] = {}

    if os.name == "nt":
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= getattr(
            subprocess,
            "STARTF_USESHOWWINDOW",
            1,
        )
        startupinfo.wShowWindow = getattr(
            subprocess,
            "SW_HIDE",
            0,
        )

        kwargs["startupinfo"] = startupinfo
        kwargs["creationflags"] = getattr(
            subprocess,
            "CREATE_NO_WINDOW",
            0,
        )

    return kwargs


def parse_stream_event(line: str) -> dict[str, object]:
    parts = str(line).strip().split()

    if not parts:
        return {"type": "empty", "raw": line}

    kind = parts[0].upper()

    try:
        if kind == "PROGRESS" and len(parts) >= 6:
            return {
                "type": "progress",
                "job_id": parts[1],
                "tested": int(parts[2]),
                "active_ms": float(parts[3]),
                "wall_ms": float(parts[4]),
                "next_nonce": int(parts[5]),
                "raw": line,
            }

        if kind == "STREAM_FOUND" and len(parts) >= 7:
            return {
                "type": "found",
                "job_id": parts[1],
                "nonce": int(parts[2]),
                "hash": parts[3],
                "tested": int(parts[4]),
                "active_ms": float(parts[5]),
                "wall_ms": float(parts[6]),
                "raw": line,
            }

        if kind == "STOPPED" and len(parts) >= 6:
            return {
                "type": "stopped",
                "job_id": parts[1],
                "tested": int(parts[2]),
                "active_ms": float(parts[3]),
                "wall_ms": float(parts[4]),
                "next_nonce": int(parts[5]),
                "raw": line,
            }

        if kind == "STARTED" and len(parts) >= 2:
            return {
                "type": "started",
                "job_id": parts[1],
                "raw": line,
            }

        if kind == "STOP_ACK":
            return {
                "type": "stop_ack",
                "job_id": parts[1] if len(parts) >= 2 else "",
                "raw": line,
            }

        if kind == "ERROR":
            return {
                "type": "error",
                "message": " ".join(parts[1:]),
                "raw": line,
            }
    except (TypeError, ValueError):
        pass

    return {
        "type": "diagnostic",
        "raw": line,
    }



class CudaWorkerSession:
    """
    Ein dauerhaft laufender CUDA-Worker pro GPU.

    Vor v0.12.15.3 wurde logicoin_cuda_worker.exe für jeden Nonce-Batch neu
    gestartet. Bei zwei GPUs entstanden dadurch sehr viele DLL-/CUDA-
    Initialisierungen und schließlich Windows-Fehler 0xc0000142.

    Diese Session startet die EXE genau einmal und sendet SCAN-Befehle
    über stdin/stdout.
    """

    def __init__(self, worker: Path, device: int) -> None:
        self.worker = Path(worker)
        self.device = int(device)
        self.process: subprocess.Popen[str] | None = None
        self.output_queue: queue.Queue[str] = queue.Queue()
        self.reader_thread: threading.Thread | None = None
        self.command_lock = threading.Lock()
        self.restart_count = 0
        self.active_job_id: str | None = None

    def _reader_loop(
        self,
        process: subprocess.Popen[str],
        output_queue: queue.Queue[str],
    ) -> None:
        stream = process.stdout
        if stream is None:
            output_queue.put("__EOF__")
            return

        try:
            for line in stream:
                output_queue.put(line.rstrip("\r\n"))
        except Exception as exc:
            output_queue.put(f"__READER_ERROR__ {exc}")
        finally:
            output_queue.put("__EOF__")

    def _clear_queue(self) -> None:
        while True:
            try:
                self.output_queue.get_nowait()
            except queue.Empty:
                break

    def start(self, timeout: float = 20.0) -> None:
        self.stop()
        self.output_queue = queue.Queue()
        suppress_windows_error_dialogs()

        env = os.environ.copy()
        env["PYTHONUTF8"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"
        env.setdefault("CUDA_MODULE_LOADING", "LAZY")

        cmd = [
            str(self.worker),
            "--server",
            "--device",
            str(self.device),
        ]

        self.process = subprocess.Popen(
            cmd,
            cwd=str(self.worker.parent),
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            **hidden_worker_process_kwargs(),
        )

        self.reader_thread = threading.Thread(
            target=self._reader_loop,
            args=(self.process, self.output_queue),
            daemon=True,
            name=f"logicoin-cuda-reader-gpu{self.device}",
        )
        self.reader_thread.start()

        deadline = time.monotonic() + timeout
        diagnostic_lines: list[str] = []

        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                break

            try:
                line = self.output_queue.get(timeout=0.25)
            except queue.Empty:
                continue

            if line.startswith("READY "):
                return
            if line == "__EOF__":
                break
            diagnostic_lines.append(line)

        exit_code = self.process.poll() if self.process else None
        details = " | ".join(diagnostic_lines[-5:])
        self.stop()

        raise RuntimeError(
            "Persistenter CUDA-Worker konnte nicht initialisiert werden. "
            f"GPU {self.device}, Exitcode {exit_code}. "
            f"{details}"
        )

    def is_alive(self) -> bool:
        return (
            self.process is not None
            and self.process.poll() is None
        )

    def _send_command(
        self,
        command: str,
        timeout: float,
    ) -> str:
        process = self.process

        if (
            process is None
            or process.poll() is not None
            or process.stdin is None
        ):
            raise RuntimeError(
                "Persistenter CUDA-Worker ist nicht aktiv."
            )

        process.stdin.write(command + "\n")
        process.stdin.flush()

        deadline = time.monotonic() + timeout
        diagnostics: list[str] = []

        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError(
                    "CUDA-Worker wurde unerwartet beendet. "
                    f"Exitcode {process.poll()}."
                )

            try:
                line = self.output_queue.get(timeout=0.25)
            except queue.Empty:
                continue

            if line in {"PONG", "BYE"} or line == "NONE" or line.startswith("NONE "):
                return line
            if line.startswith("FOUND "):
                return line
            if line.startswith("ERROR "):
                raise RuntimeError(
                    "CUDA-Worker: " + line[6:]
                )
            if line == "__EOF__":
                raise RuntimeError(
                    "CUDA-Worker-Pipe wurde geschlossen."
                )
            if line.startswith("__READER_ERROR__"):
                raise RuntimeError(line)

            diagnostics.append(line)

        raise TimeoutError(
            "CUDA-Worker antwortete nicht rechtzeitig. "
            + " | ".join(diagnostics[-5:])
        )

    def ping(self) -> bool:
        with self.command_lock:
            return self._send_command(
                "PING",
                timeout=5.0,
            ) == "PONG"

    def scan_detailed(
        self,
        base_hash: str,
        difficulty_bits: int,
        start_nonce: int,
        count: int,
        timeout: float = 120.0,
    ) -> dict[str, object]:
        command = (
            f"SCAN {base_hash} {int(difficulty_bits)} "
            f"{int(start_nonce)} {int(count)}"
        )

        last_error: Exception | None = None

        for attempt in range(2):
            try:
                with self.command_lock:
                    if not self.is_alive():
                        self.start()
                    response = self._send_command(
                        command,
                        timeout=timeout,
                    )

                parsed = parse_scan_response(response)
                tested = int(parsed.get("tested") or 0)

                if tested < 0 or tested > int(count):
                    raise RuntimeError(
                        "CUDA-Worker meldet ungültige "
                        f"Testanzahl {tested} für Count {count}."
                    )

                return parsed
            except Exception as exc:
                last_error = exc
                self.restart_count += 1
                self.stop()

                if attempt == 0:
                    time.sleep(1.0)
                    try:
                        self.start()
                    except Exception as restart_exc:
                        last_error = restart_exc
                        break

        raise RuntimeError(
            f"Persistenter CUDA-Worker fehlgeschlagen: {last_error}"
        )

    def scan(
        self,
        base_hash: str,
        difficulty_bits: int,
        start_nonce: int,
        count: int,
        timeout: float = 120.0,
    ) -> Tuple[int | None, str | None]:
        parsed = self.scan_detailed(
            base_hash=base_hash,
            difficulty_bits=difficulty_bits,
            start_nonce=start_nonce,
            count=count,
            timeout=timeout,
        )

        if not bool(parsed.get("found")):
            return None, None

        return (
            int(parsed.get("nonce")),
            str(parsed.get("hash") or ""),
        )

    def _write_command(self, command: str) -> None:
        process = self.process

        if (
            process is None
            or process.poll() is not None
            or process.stdin is None
        ):
            raise RuntimeError(
                "CUDA-Worker ist nicht aktiv."
            )

        process.stdin.write(command + "\n")
        process.stdin.flush()

    def _read_worker_line(
        self,
        timeout: float,
    ) -> str | None:
        try:
            line = self.output_queue.get(
                timeout=max(0.001, float(timeout))
            )
        except queue.Empty:
            return None

        if line == "__EOF__":
            raise RuntimeError(
                "CUDA-Worker-Pipe wurde geschlossen."
            )

        if line.startswith("__READER_ERROR__"):
            raise RuntimeError(line)

        return line

    def start_stream(
        self,
        job_id: str,
        base_hash: str,
        difficulty_bits: int,
        start_nonce: int,
        chunk_count: int,
        duty_percent: int,
        progress_ms: int = 250,
        timeout: float = 15.0,
    ) -> None:
        if not self.is_alive():
            self.start()

        job = str(job_id)
        command = (
            f"START {job} {base_hash} "
            f"{int(difficulty_bits)} {int(start_nonce)} "
            f"{max(8192, int(chunk_count))} "
            f"{max(50, int(progress_ms))} "
            f"{max(5, min(100, int(duty_percent)))}"
        )

        deadline = time.monotonic() + max(
            1.0,
            float(timeout),
        )

        with self.command_lock:
            self._write_command(command)

            while time.monotonic() < deadline:
                line = self._read_worker_line(0.25)

                if line is None:
                    continue

                event = parse_stream_event(line)

                if (
                    event.get("type") == "started"
                    and event.get("job_id") == job
                ):
                    self.active_job_id = job
                    return

                if event.get("type") == "error":
                    raise RuntimeError(
                        "CUDA-Streaming-Worker: "
                        + str(event.get("message", ""))
                    )

        raise TimeoutError(
            f"Streaming-Job {job} wurde nicht gestartet."
        )

    def read_stream_event(
        self,
        timeout: float = 0.05,
    ) -> dict[str, object] | None:
        line = self._read_worker_line(timeout)

        if line is None:
            return None

        event = parse_stream_event(line)

        if event.get("type") == "error":
            raise RuntimeError(
                "CUDA-Streaming-Worker: "
                + str(event.get("message", ""))
            )

        return event

    def stop_stream(
        self,
        job_id: str | None = None,
        timeout: float = 8.0,
    ) -> dict[str, object] | None:
        if not self.is_alive():
            self.active_job_id = None
            return None

        job = str(
            job_id
            or self.active_job_id
            or ""
        )
        deadline = time.monotonic() + max(
            1.0,
            float(timeout),
        )
        last_stopped: dict[str, object] | None = None

        with self.command_lock:
            self._write_command(
                f"STOP {job}".rstrip()
            )

            while time.monotonic() < deadline:
                line = self._read_worker_line(0.25)

                if line is None:
                    continue

                event = parse_stream_event(line)
                event_type = event.get("type")

                if event_type == "stopped":
                    last_stopped = event
                    continue

                if event_type == "stop_ack":
                    ack_job = str(event.get("job_id", ""))
                    if not job or not ack_job or ack_job == job:
                        self.active_job_id = None
                        return last_stopped or event

                if event_type == "error":
                    raise RuntimeError(
                        "CUDA-Streaming-Worker: "
                        + str(event.get("message", ""))
                    )

        self.active_job_id = None
        return last_stopped

    def self_test(self) -> tuple[bool, str]:
        test_base = (
            "000102030405060708090a0b0c0d0e0f"
            "101112131415161718191a1b1c1d1e1f"
        )

        # Test 1: Exakter Hash für eine bekannte Nonce.
        test_nonce = 123456789
        expected = logic_hash_v2_cuda_mix_from_base_hex(
            test_base,
            test_nonce,
        )

        try:
            direct = self.scan_detailed(
                base_hash=test_base,
                difficulty_bits=0,
                start_nonce=test_nonce,
                count=1,
                timeout=30.0,
            )
        except Exception as exc:
            return False, f"CUDA-Direkttest: {exc}"

        if not bool(direct.get("found")):
            return False, "CUDA-Direkttest fand die einzelne Nonce nicht."

        if int(direct.get("nonce") or -1) != test_nonce:
            return (
                False,
                "CUDA-Direkttest falsche Nonce: "
                f"erwartet {test_nonce}, "
                f"erhalten {direct.get('nonce')}",
            )

        direct_hash = str(direct.get("hash") or "")
        if direct_hash.lower() != expected.lower():
            return (
                False,
                "CUDA-Direkttest Hash stimmt nicht mit Python überein. "
                f"CUDA={direct_hash}, Python={expected}",
            )

        if int(direct.get("tested") or 0) != 1:
            return (
                False,
                "CUDA-Direkttest meldet keine exakte Testanzahl 1: "
                f"{direct.get('tested')}",
            )

        # Test 2: Echte Zielsuche. In diesem Bereich liegt mindestens ein
        # bekannter 20-Bit-Treffer. Der Worker darf irgendeinen gültigen
        # Treffer aus dem Bereich liefern.
        search_count = 2_000_000

        try:
            proof = self.scan_detailed(
                base_hash=test_base,
                difficulty_bits=20,
                start_nonce=0,
                count=search_count,
                timeout=30.0,
            )
        except Exception as exc:
            return False, f"CUDA-20-Bit-Suchtest: {exc}"

        if not bool(proof.get("found")):
            return (
                False,
                "CUDA-20-Bit-Suchtest fand keinen Treffer "
                f"in {search_count:,} Nonces.",
            )

        proof_nonce = int(proof.get("nonce") or -1)
        proof_hash = str(proof.get("hash") or "")
        proof_tested = int(proof.get("tested") or 0)

        if not (0 <= proof_nonce < search_count):
            return (
                False,
                "CUDA-20-Bit-Suchtest meldet Nonce außerhalb "
                f"des Bereichs: {proof_nonce}",
            )

        python_hash = logic_hash_v2_cuda_mix_from_base_hex(
            test_base,
            proof_nonce,
        )

        if proof_hash.lower() != python_hash.lower():
            return (
                False,
                "CUDA-20-Bit-Suchtest Hash stimmt nicht mit Python überein. "
                f"CUDA={proof_hash}, Python={python_hash}",
            )

        if not hash_meets_difficulty_bits(
            python_hash,
            20,
        ):
            return (
                False,
                "CUDA-20-Bit-Suchtest meldete keinen gültigen Zielhash.",
            )

        if not (1 <= proof_tested <= search_count):
            return (
                False,
                "CUDA-20-Bit-Suchtest meldet ungültige exakte "
                f"Testanzahl: {proof_tested}",
            )

        return (
            True,
            "Streaming CUDA-Worker Self-Test OK: "
            "Hash, exakte Nonce-Zählung und 20-Bit-Suche bestätigt.",
        )

    def stop(self) -> None:
        process = self.process

        if (
            process is not None
            and process.poll() is None
            and self.active_job_id is not None
        ):
            try:
                self.stop_stream(
                    self.active_job_id,
                    timeout=3.0,
                )
            except Exception:
                pass

        self.process = None
        self.active_job_id = None

        if process is None:
            return

        try:
            if process.poll() is None and process.stdin is not None:
                process.stdin.write("QUIT\n")
                process.stdin.flush()
        except Exception:
            pass

        try:
            process.wait(timeout=2.0)
        except Exception:
            try:
                process.terminate()
                process.wait(timeout=2.0)
            except Exception:
                try:
                    process.kill()
                except Exception:
                    pass

        try:
            if process.stdin is not None:
                process.stdin.close()
        except Exception:
            pass
        try:
            if process.stdout is not None:
                process.stdout.close()
        except Exception:
            pass


def create_cuda_session(
    worker: Path,
    device: int,
) -> CudaWorkerSession:
    session = CudaWorkerSession(
        worker=worker,
        device=device,
    )
    session.start()

    ok, message = session.self_test()
    safe_console_print(
        f"[SELFTEST] {message}",
        flush=True,
    )

    if not ok:
        session.stop()
        raise RuntimeError(message)

    return session


def cpu_fallback_batch(
    block: Dict[str, Any],
    difficulty_bits: int,
    start_nonce: int,
    count: int,
) -> Tuple[int | None, str | None]:
    end = start_nonce + count

    for nonce in range(start_nonce, end):
        block["nonce"] = nonce
        h = calculate_block_hash(block)

        if hash_meets_difficulty_bits(
            h,
            difficulty_bits,
        ):
            return nonce, h

    return None, None


def mine_cuda_streaming_job(
    node_url: str,
    device: int,
    tip: Dict[str, Any],
    candidate: Dict[str, Any],
    base_hash: str,
    difficulty_bits: int,
    batch_nonces: int,
    gpu_percent: int,
    stats: MinerStats,
    cuda_session: CudaWorkerSession,
) -> Tuple[bool, bool]:
    """
    v0.12.15.3:
    Der CUDA-Worker startet intern Kernel direkt hintereinander.

    Python übernimmt nur noch:
    - Tip-Überwachung
    - Statistik
    - lokale Hash-Prüfung
    - Block-Submit
    """
    tip_hash = str(tip.get("hash", ""))
    tip_height = int(tip.get("index", 0))
    job_id = (
        f"gpu{device}-h{tip_height}-"
        f"{time.time_ns() & 0xFFFFFFFFFFFF:x}"
    )

    machine_name = (
        os.environ.get("COMPUTERNAME")
        or platform.node()
        or "logicoin-pc"
    )
    nonce_seed = hashlib.sha256(
        (
            f"{machine_name}|"
            f"{candidate.get('miner_address')}|"
            f"gpu{device}|{tip_hash}"
        ).encode("utf-8")
    ).digest()
    start_nonce = (
        int.from_bytes(nonce_seed[:7], "big")
        << 8
    )

    chunk_count = max(
        4_194_304,
        min(
            int(batch_nonces) * 64,
            67_108_864,
        ),
    )

    cuda_session.start_stream(
        job_id=job_id,
        base_hash=base_hash,
        difficulty_bits=difficulty_bits,
        start_nonce=start_nonce,
        chunk_count=chunk_count,
        duty_percent=gpu_percent,
        progress_ms=200,
    )

    tip_watcher = NodeTipWatcher(
        node_url=node_url,
        tip_hash=tip_hash,
        height=tip_height,
    )
    tip_watcher.start()

    stats.last_status = (
        f"Streaming CUDA · {difficulty_bits} Bits · "
        f"Chunk {chunk_count:,} · Long-Poll"
    )
    stats.write_stats_file()

    started_at = time.time()
    last_dashboard = 0.0
    last_active_ms = 0.0
    last_wall_ms = 0.0
    last_tested_total = 0
    tested_total = 0
    found_event: dict[str, object] | None = None

    expected_hashes = 1 << max(
        0,
        min(62, int(difficulty_bits)),
    )
    proof_watchdog_limit = (
        max(
            chunk_count * 8,
            expected_hashes * 32,
        )
        if difficulty_bits <= 28
        else None
    )

    safe_console_print(
        f"[STREAM] Job {job_id} gestartet · "
        f"Chunk {chunk_count:,} · Ziel {gpu_percent}%",
        flush=True,
    )

    try:
        while True:
            event = cuda_session.read_stream_event(
                timeout=0.04
            )
            now_monotonic = time.monotonic()

            if event is not None:
                event_type = str(
                    event.get("type", "")
                )
                event_job = str(
                    event.get("job_id", "")
                )

                if (
                    event_job
                    and event_job != job_id
                ):
                    continue

                if event_type in {
                    "progress",
                    "found",
                    "stopped",
                }:
                    new_tested_total = int(
                        event.get(
                            "tested",
                            tested_total,
                        )
                        or 0
                    )
                    active_ms = float(
                        event.get(
                            "active_ms",
                            last_active_ms,
                        )
                        or 0.0
                    )
                    wall_ms = float(
                        event.get(
                            "wall_ms",
                            last_wall_ms,
                        )
                        or 0.0
                    )

                    if new_tested_total < last_tested_total:
                        raise RuntimeError(
                            "CUDA-Zähler ist rückwärts gesprungen: "
                            f"{last_tested_total} -> {new_tested_total}"
                        )

                    delta_tested = (
                        new_tested_total
                        - last_tested_total
                    )
                    delta_active_ms = max(
                        0.0,
                        active_ms - last_active_ms,
                    )
                    delta_wall_ms = max(
                        delta_active_ms,
                        wall_ms - last_wall_ms,
                    )

                    if delta_wall_ms > 0:
                        stats.add_activity_sample(
                            active_seconds=(
                                delta_active_ms / 1000.0
                            ),
                            wall_seconds=(
                                delta_wall_ms / 1000.0
                            ),
                        )

                    if delta_tested > 0:
                        stats.add_exact_work(
                            tested_nonces=delta_tested,
                            interval_seconds=max(
                                0.000001,
                                delta_wall_ms / 1000.0,
                            ),
                            difficulty_bits=difficulty_bits,
                        )

                        if delta_active_ms > 0:
                            cuda_raw_hps = (
                                delta_tested
                                / (
                                    delta_active_ms
                                    / 1000.0
                                )
                            )
                            stats.add_hashrate_sample(
                                cuda_raw_hps
                            )

                    tested_total = new_tested_total
                    last_tested_total = new_tested_total
                    last_active_ms = active_ms
                    last_wall_ms = wall_ms

                    stats.last_status = (
                        f"Effektiv "
                        f"{format_hashrate(stats.current_hs)} · "
                        f"CUDA roh "
                        f"{format_hashrate(stats.last_raw_hashrate)} · "
                        f"{tested_total:,} Nonces"
                    )
                    stats.write_stats_file()

                    if (
                        event_type != "found"
                        and proof_watchdog_limit is not None
                        and tested_total >= proof_watchdog_limit
                    ):
                        raise RuntimeError(
                            "CUDA-Zielsuche unplausibel: "
                            f"{tested_total:,} echte Nonces ohne "
                            f"{difficulty_bits}-Bit-Treffer. "
                            "Worker wird aus Sicherheitsgründen gestoppt."
                        )

                if event_type == "found":
                    found_event = event
                    break

                if event_type == "stopped":
                    raise RuntimeError(
                        "CUDA-Streaming-Job wurde "
                        "unerwartet beendet."
                    )

            if tip_watcher.changed():
                stop_event = (
                    cuda_session.stop_stream(
                        job_id,
                        timeout=5.0,
                    )
                )

                if stop_event:
                    tested_total = max(
                        tested_total,
                        int(
                            stop_event.get(
                                "tested",
                                tested_total,
                            )
                            or tested_total
                        ),
                    )

                tip_watcher.stop()
                stats.job_refreshes += 1
                stats.last_status = (
                    "Neuer Node-Tip per Long-Poll · "
                    "Streaming-Job gewechselt"
                )
                stats.write_stats_file()
                safe_console_print(
                    "[STREAM] Neuer Block per Long-Poll erkannt.",
                    flush=True,
                )
                return False, True

            if (
                now_monotonic - last_dashboard
                >= 1.0
            ):
                last_dashboard = now_monotonic
                stats.print_dashboard(
                    node_url,
                    "CUDA-STREAM",
                )

        tip_watcher.stop()

        if found_event is None:
            raise RuntimeError(
                "Streaming-Worker meldete keinen Fund."
            )

        found_nonce = int(
            found_event.get("nonce", -1)
        )
        found_hash = str(
            found_event.get("hash", "")
        )
        tested_total = int(
            found_event.get(
                "tested",
                tested_total,
            )
            or tested_total
        )

        if tested_total <= 0:
            raise RuntimeError(
                "CUDA-Fund ohne bestätigte echte Nonce-Zählung."
            )

        candidate["nonce"] = found_nonce
        python_hash = calculate_block_hash(candidate)

        if found_hash.lower() != python_hash.lower():
            raise RuntimeError(
                "CUDA- und Python-Hash unterscheiden sich. "
                f"CUDA={found_hash}, Python={python_hash}"
            )

        if not hash_meets_difficulty_bits(
            python_hash,
            difficulty_bits,
        ):
            raise RuntimeError(
                "Streaming-CUDA-Nonce erfüllt "
                "das Node-Target nicht."
            )

        stats.record_proof_found(
            difficulty_bits
        )

        candidate["hash"] = python_hash
        candidate["mining_time_seconds"] = max(
            0.000001,
            time.time() - started_at,
        )
        candidate["hashrate_hs"] = (
            tested_total
            / candidate["mining_time_seconds"]
        )

        safe_console_print(
            "[FOUND] Streaming-CUDA-Block gefunden "
            "und lokal geprüft.",
            flush=True,
        )

        response = submit_candidate_with_stale_guard(
            node_url=node_url,
            tip_hash=tip_hash,
            tip_height=tip_height,
            candidate=candidate,
        )

        if response.get("stale_avoided"):
            stats.stale_avoided += 1
            stats.job_refreshes += 1
            stats.last_status = (
                "Stale vor Submit lokal vermieden"
            )
            stats.write_stats_file()
            return False, True

        if response.get("accepted"):
            stats.record_accepted(
                difficulty_bits
            )
            stats.last_accepted_height = int(
                response.get("height")
                or candidate.get("index")
                or 0
            )
            stats.last_hash = python_hash
            stats.last_nonce = found_nonce
            stats.last_status = (
                f"ACCEPTED Block "
                f"#{stats.last_accepted_height}"
            )
            stats.write_stats_file()
            safe_console_print(
                f"[ACCEPTED] Block "
                f"#{stats.last_accepted_height}",
                flush=True,
            )
            return True, False

        error_msg = str(
            response.get(
                "error",
                "Unbekannter Fehler",
            )
        )

        if is_stale_rejection(response):
            stats.stale += 1
            stats.last_status = (
                f"STALE: {error_msg[:45]}"
            )
            stats.write_stats_file()
            return False, True

        stats.invalid += 1
        stats.rejected = stats.invalid
        stats.last_status = (
            f"INVALID: {error_msg[:45]}"
        )
        stats.write_stats_file()
        safe_console_print(
            f"[INVALID] {error_msg}",
            flush=True,
        )
        return False, False
    except Exception:
        tip_watcher.stop()
        try:
            cuda_session.stop_stream(
                job_id,
                timeout=3.0,
            )
        except Exception:
            pass
        raise



def mine_once(
    node_url: str,
    miner_address: str,
    backend: str,
    device: int,
    batch_nonces: int,
    max_batches_per_job: int,
    stats: MinerStats,
    gpu_percent: int,
    cuda_session: CudaWorkerSession | None,
) -> Tuple[bool, bool]:
    encoded = urllib.parse.quote(miner_address)
    template = get_json(node_url + f"/mining_template?miner={encoded}")

    if not template.get("ok"):
        safe_console_print(f"[ERROR] Node-Fehler: {template}", flush=True)
        return False, False

    if not template.get("chain_valid"):
        safe_console_print(f"[ERROR] Node-Chain ungültig: {template.get('chain_status')}", flush=True)
        return False, False

    supported = template.get("supported_algorithms", [])
    if supported and GPU_ALGORITHM not in supported:
        safe_console_print("[WARN] Der Node meldet keine Unterstützung für GPU-v1.", flush=True)
        safe_console_print(f"[WARN] Node supported_algorithms: {supported}", flush=True)
        safe_console_print("[WARN] Vermutlich läuft noch ein alter Node. Bitte alle alten Node/Control-Center-Prozesse schließen und v0.12.15.3 starten.", flush=True)

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

    stats.target_percent = int(gpu_percent)
    stats.jobs += 1
    stats.last_height = int(tip.get("index", 0))
    stats.last_diff = difficulty
    stats.last_diff_bits = difficulty_bits
    stats.last_txs = len(transactions)
    stats.last_status = f"Job #{stats.jobs}: Tip #{tip.get('index')} → Block #{int(tip.get('index', 0)) + 1}"

    candidate = create_candidate_block_from_tip(
        tip_block=tip,
        miner_address=miner_address,
        difficulty=difficulty,
        reward=reward,
        transactions=transactions,
        algorithm=GPU_ALGORITHM,
        difficulty_rule=difficulty_rule,
        difficulty_bits=difficulty_bits,
    )

    # Wichtig: timestamp bleibt pro Job konstant.
    # v0.12.15.3 Fix:
    # CUDA muss denselben Header-BaseHash bekommen, den Python/Node später
    # bei calculate_block_hash() nutzen. Vorher wurde versehentlich der volle
    # Block als BaseHash-Grundlage genutzt.
    candidate_header = make_block_header(candidate)
    base_hash = logic_hash_gpu_mix_base_hex(candidate_header)

    use_cuda = cuda_session is not None

    if backend == "cpu-fallback":
        use_cuda = False
    elif backend in {"cuda", "auto"}:
        if backend == "cuda" and cuda_session is None:
            raise RuntimeError(
                "CUDA-Backend verlangt eine aktive persistente Worker-Session."
            )
    else:
        raise ValueError(f"Unbekanntes Backend: {backend}")

    backend_name = "CUDA" if use_cuda else "CPU-FALLBACK"
    stats.last_status = (
        f"Mining {backend_name} | {difficulty_bits} Bits | "
        f"Batch {batch_nonces:,}"
    )
    stats.print_dashboard(node_url, backend_name, force=True)

    safe_console_print("=" * 78, flush=True)
    safe_console_print(
        f"[GPU-JOB] Tip #{tip.get('index')} -> Block #{candidate['index']} "
        f"| Difficulty {difficulty_bits} Bits | TXs {len(transactions)}",
        flush=True,
    )
    safe_console_print(f"[GPU-JOB] Algorithmus: {GPU_ALGORITHM}", flush=True)
    safe_console_print(f"[GPU-JOB] Backend: {backend_name} | Batch: {batch_nonces:,} nonces", flush=True)
    safe_console_print(f"[GPU-JOB] BaseHash: {base_hash}", flush=True)

    start_time = time.time()

    machine_name = (
        os.environ.get("COMPUTERNAME")
        or platform.node()
        or "logicoin-pc"
    )
    nonce_seed = hashlib.sha256(
        f"{machine_name}|{miner_address}|gpu{device}".encode("utf-8")
    ).digest()
    start_nonce = int.from_bytes(nonce_seed[:6], "big") << 16
    current_batch_nonces = max(
        8192,
        min(int(batch_nonces), 32768),
    )
    tested_total = 0

    if use_cuda:
        if cuda_session is None:
            raise RuntimeError(
                "CUDA-Streaming-Session fehlt."
            )

        return mine_cuda_streaming_job(
            node_url=node_url,
            device=device,
            tip=tip,
            candidate=candidate,
            base_hash=base_hash,
            difficulty_bits=difficulty_bits,
            batch_nonces=batch_nonces,
            gpu_percent=gpu_percent,
            stats=stats,
            cuda_session=cuda_session,
        )

    for batch_index in range(max_batches_per_job):
        cycle_started = time.perf_counter()

        if batch_index > 0:
            if not tip_is_current(
                node_url,
                str(tip.get("hash", "")),
                int(tip.get("index", 0)),
            ):
                stats.job_refreshes += 1
                stats.last_status = "Neuer Block – Job aktualisiert"
                stats.write_stats_file()
                safe_console_print(
                    "[JOB-REFRESH] Neuer Node-Tip erkannt. "
                    "Wechsle ohne Stale-Zählung.",
                    flush=True,
                )
                return False, True

        batch_count = current_batch_nonces
        batch_started = time.perf_counter()

        found_nonce, found_hash = cpu_fallback_batch(
            candidate,
            difficulty_bits,
            start_nonce,
            batch_count,
        )

        active_seconds = max(
            time.perf_counter() - batch_started,
            0.000001,
        )
        actual_batch_tested = (
            int(found_nonce) - int(start_nonce) + 1
            if found_nonce is not None
            else batch_count
        )
        actual_batch_tested = max(
            0,
            min(
                batch_count,
                actual_batch_tested,
            ),
        )
        tested_total += actual_batch_tested

        current_batch_nonces = choose_next_batch_size(
            current_count=batch_count,
            maximum_count=batch_nonces,
            active_seconds=active_seconds,
            target_seconds=0.10,
        )

        if gpu_percent < 100:
            pause = active_seconds * ((100.0 - gpu_percent) / gpu_percent)
            if pause > 0:
                time.sleep(min(pause, 3.0))

        cycle_wall_seconds = max(
            time.perf_counter() - cycle_started,
            active_seconds,
        )
        stats.add_activity_sample(
            active_seconds=active_seconds,
            wall_seconds=cycle_wall_seconds,
        )

        stats.add_exact_work(
            tested_nonces=actual_batch_tested,
            interval_seconds=cycle_wall_seconds,
            difficulty_bits=difficulty_bits,
        )
        if active_seconds > 0:
            stats.add_hashrate_sample(
                actual_batch_tested
                / active_seconds
            )

        tested = tested_total
        elapsed = max(
            time.time() - start_time,
            0.000001,
        )
        hps = stats.current_hs

        stats.last_status = (
            f"Mining {backend_name} batch {batch_index + 1}/"
            f"{max_batches_per_job} · {batch_count:,} nonces"
        )
        safe_console_print(f"[{backend_name}] {now_time()} | Batch {batch_index + 1}/{max_batches_per_job} | Nonce {start_nonce:,}-{start_nonce + batch_nonces - 1:,} | {format_hashrate(hps)}", flush=True)
        stats.print_dashboard(node_url, backend_name)

        if found_nonce is not None and found_hash:
            candidate["nonce"] = int(found_nonce)

            # v0.12.15.3: Sicherheitsprüfung direkt im Python-Miner.
            # Damit sehen wir sofort, ob CUDA-Worker und Node/Python exakt denselben Hash berechnen.
            python_hash = calculate_block_hash(candidate)

            if str(found_hash).lower() != python_hash.lower():
                safe_console_print("[VERIFY] CUDA-Hash unterscheidet sich vom Python/Node-Hash. Das sollte ab v0.12.15.3 nicht mehr passieren.", flush=True)
                safe_console_print(f"[VERIFY] CUDA:   {found_hash}", flush=True)
                safe_console_print(f"[VERIFY] Python: {python_hash}", flush=True)
                safe_console_print("[VERIFY] Nutze Python-Hash für Submit, falls er das Target erfüllt.", flush=True)

            if not hash_meets_difficulty_bits(
                python_hash,
                difficulty_bits,
            ):
                safe_console_print(
                    "[VERIFY] Gefundene CUDA-Nonce erfüllt das "
                    "Python/Node-Bit-Target nicht. Suche weiter.",
                    flush=True,
                )
                stats.last_status = "CUDA-Nonce nicht Python/Node-kompatibel, suche weiter"
                start_nonce += batch_count
                continue

            stats.record_proof_found(
                difficulty_bits
            )

            candidate["hash"] = python_hash
            candidate["mining_time_seconds"] = time.time() - start_time
            candidate["hashrate_hs"] = tested_total / max(
                candidate["mining_time_seconds"],
                0.000001,
            )

            safe_console_print("[FOUND] GPU-Mix Block gefunden und lokal verifiziert!", flush=True)
            safe_console_print(f"- Nonce: {found_nonce}", flush=True)
            safe_console_print(f"- Hash:  {python_hash}", flush=True)

            safe_console_print(
                "[SUBMIT] Lokale GPU-Sperre und letzter Tip-Check...",
                flush=True,
            )
            response = submit_candidate_with_stale_guard(
                node_url=node_url,
                tip_hash=str(tip.get("hash", "")),
                tip_height=int(tip.get("index", 0)),
                candidate=candidate,
            )

            if response.get("stale_avoided"):
                stats.stale_avoided += 1
                stats.job_refreshes += 1
                stats.last_status = "Stale vor Submit lokal vermieden"
                stats.write_stats_file()
                safe_console_print(
                    "[STALE-VERMIEDEN] Neuer Tip erkannt; "
                    "der alte Kandidat wurde nicht gesendet.",
                    flush=True,
                )
                return False, True

            if response.get("accepted"):
                stats.record_accepted(
                    difficulty_bits
                )
                stats.last_accepted_height = int(response.get("height") or candidate.get("index") or 0)
                stats.last_hash = python_hash
                stats.last_nonce = int(found_nonce)
                stats.last_status = f"ACCEPTED Block #{stats.last_accepted_height}"
                safe_console_print(
                    f"[ACCEPTED] Block #{response.get('height')} akzeptiert "
                    f"| Fees {response.get('fees')} | Next bits "
                    f"{response.get('network_params', {}).get('next_difficulty_bits')}",
                    flush=True,
                )
                stats.print_dashboard(node_url, backend_name, force=True)
                return True, False

            error_msg = str(response.get("error", "Unbekannter Fehler"))
            if is_stale_rejection(response):
                stats.stale += 1
                stats.last_status = f"STALE: {error_msg[:45]}"
                stats.write_stats_file()
                safe_console_print(f"[STALE] {error_msg}", flush=True)
                return False, True

            stats.invalid += 1
            stats.rejected = stats.invalid
            stats.last_status = f"INVALID: {error_msg[:45]}"
            stats.write_stats_file()
            safe_console_print(f"[INVALID] {error_msg}", flush=True)
            if "Falscher Algorithmus" in error_msg:
                safe_console_print("[HINWEIS] Prüfe, ob App und Node dieselbe Version nutzen.", flush=True)
            return False, False

        start_nonce += batch_count

    stats.job_refreshes += 1
    stats.last_status = "Job-Fenster beendet – lade aktuellen Tip"
    safe_console_print(
        "[JOB-REFRESH] Kein Treffer. Aktueller Tip wird neu geladen.",
        flush=True,
    )
    stats.print_dashboard(node_url, backend_name, force=True)
    return False, True


def main() -> None:
    configure_utf8_stdio()
    suppress_windows_error_dialogs()
    parser = argparse.ArgumentParser(description="Logicoin GPU Streaming Miner v0.12.15.3")
    parser.add_argument("--node-url", default="http://127.0.0.1:8080")
    parser.add_argument("--miner-address", default=DEFAULT_MINER_ADDRESS)
    parser.add_argument("--backend", default="auto", choices=["auto", "cuda", "cpu-fallback"])
    parser.add_argument("--device", type=int, default=0)
    parser.add_argument("--batch-nonces", type=int, default=262144)
    parser.add_argument("--max-batches-per-job", type=int, default=128)
    parser.add_argument("--max-stale-retries", type=int, default=3)
    parser.add_argument("--sleep-after-block", type=float, default=0.02)
    parser.add_argument("--max-missing-worker-errors", type=int, default=3)
    parser.add_argument("--stats-file", default="", help="Optionaler Pfad für Stats-JSON. Standard: logicoin_gpu_miner_stats_gpuX.json")
    parser.add_argument(
        "--gpu-percent",
        type=int,
        default=int(os.environ.get("LOGIC_GPU_USAGE_PERCENT", "100")),
        help="Software-Duty-Cycle 5-100 Prozent.",
    )
    args = parser.parse_args()

    node_url = normalize_node_url(args.node_url)
    miner_address = args.miner_address

    batch_nonces = max(1024, int(args.batch_nonces))
    max_batches = max(1, int(args.max_batches_per_job))
    gpu_percent = max(5, min(100, int(args.gpu_percent)))

    safe_console_print("=" * 78, flush=True)
    safe_console_print("Logicoin / LOGIC GPU Streaming Miner v0.12.15.3", flush=True)
    safe_console_print("=" * 78, flush=True)
    safe_console_print(f"Node: {node_url}", flush=True)
    safe_console_print(f"Miner-Adresse: {miner_address}", flush=True)
    safe_console_print(f"Backend: {args.backend}", flush=True)
    safe_console_print(f"CUDA Device: {args.device}", flush=True)
    safe_console_print(f"Batch Nonces: {batch_nonces:,}", flush=True)
    safe_console_print(f"GPU-Ziel: {gpu_percent}% (Software-Duty-Cycle)", flush=True)
    safe_console_print("", flush=True)

    worker = find_cuda_worker()
    if worker:
        safe_console_print(f"[INFO] CUDA Worker gefunden: {worker}", flush=True)
    else:
        safe_console_print(f"[INFO] Miner-Basisordner: {BASE_DIR}", flush=True)
        safe_console_print(f"[INFO] Suche Worker auch in Parent: {BASE_DIR.parent}", flush=True)
        if args.backend == "cuda":
            safe_console_print("[INFO] CUDA Worker nicht gefunden. Backend 'cuda' braucht logicoin_cuda_worker.exe.", flush=True)
            safe_console_print("[INFO] Bitte BUILD_CUDA_WORKER_SAFE.bat ausführen.", flush=True)
        elif args.backend == "auto":
            safe_console_print("[INFO] CUDA Worker nicht gefunden. Auto nutzt CPU-Fallback.", flush=True)
            safe_console_print("[INFO] Für echtes GPU-Mining: BUILD_CUDA_WORKER_SAFE.bat ausführen.", flush=True)
        else:
            safe_console_print("[INFO] CPU-Fallback aktiv. Das ist Testmodus, kein echtes GPU-Mining.", flush=True)

    accepted = 0
    stale_retries = 0
    missing_worker_errors = 0
    stats_path = Path(args.stats_file) if str(args.stats_file).strip() else stats_file_for_device(args.device)
    if not stats_path.is_absolute():
        stats_path = DATA_DIR / stats_path
    stats = MinerStats(device=args.device, stats_file=stats_path)
    stats.last_status = "Miner gestartet"
    stats.print_dashboard(node_url, args.backend.upper(), force=True)

    cuda_session: CudaWorkerSession | None = None

    def ensure_session() -> CudaWorkerSession | None:
        nonlocal cuda_session

        if args.backend == "cpu-fallback":
            return None

        worker_path = find_cuda_worker()
        if worker_path is None:
            if args.backend == "cuda":
                raise FileNotFoundError(
                    "logicoin_cuda_worker.exe nicht gefunden. "
                    "Bitte BUILD_CUDA_WORKER_SAFE.bat ausführen."
                )
            return None

        if cuda_session is not None and cuda_session.is_alive():
            return cuda_session

        if cuda_session is not None:
            cuda_session.stop()
            cuda_session = None

        stats.last_status = "Initialisiere persistenten CUDA-Worker"
        stats.write_stats_file()

        cuda_session = create_cuda_session(
            worker=worker_path,
            device=args.device,
        )

        stats.proof_search_selftest_ok = True
        stats.last_status = (
            "Streaming CUDA-Worker aktiv · "
            "20-Bit-Suchtest bestanden"
        )
        stats.write_stats_file()
        return cuda_session

    while True:
        try:
            active_session = ensure_session()

            if (
                args.backend in {"cuda", "auto"}
                and worker is not None
                and active_session is None
            ):
                raise RuntimeError(
                    "CUDA-Worker vorhanden, aber Session nicht aktiv."
                )

            ok, stale = mine_once(
                node_url=node_url,
                miner_address=miner_address,
                backend=args.backend,
                device=args.device,
                batch_nonces=batch_nonces,
                max_batches_per_job=max_batches,
                stats=stats,
                gpu_percent=gpu_percent,
                cuda_session=active_session,
            )

            if ok:
                accepted += 1
                stale_retries = 0
                safe_console_print(f"[STATS] Akzeptierte GPU-Mix-Blöcke in dieser Session: {stats.accepted}", flush=True)
                time.sleep(float(args.sleep_after_block))
                continue

            if stale:
                stale_retries += 1
                safe_console_print(
                    f"[JOB] Neuer Mining-Job wird sofort geladen "
                    f"(Wechsel {stale_retries}).",
                    flush=True,
                )
                time.sleep(0.05)
                continue

            stale_retries = 0
            safe_console_print(
                "[WAIT] Warte 5 Sekunden nach einem echten Fehler...",
                flush=True,
            )
            stats.last_status = (
                f"ERROR: {error_text[:55]}"
                if "error_text" in locals()
                else "ERROR"
            )
            stats.write_stats_file()
            time.sleep(5)

        except KeyboardInterrupt:
            if cuda_session is not None:
                cuda_session.stop()
            safe_console_print("\n[STOP] GPU-Miner beendet.", flush=True)
            break
        except Exception as e:
            error_text = str(e)
            safe_console_print(f"[ERROR] {error_text}", flush=True)

            if cuda_session is not None:
                cuda_session.stop()
                cuda_session = None

            if "logicoin_cuda_worker.exe nicht gefunden" in error_text:
                missing_worker_errors += 1
                if args.backend == "cuda":
                    safe_console_print("[HINWEIS] Backend 'cuda' braucht den CUDA-Worker.", flush=True)
                    safe_console_print("[HINWEIS] Ohne CUDA Toolkit bitte --backend auto oder --backend cpu-fallback nutzen.", flush=True)
                if missing_worker_errors >= args.max_missing_worker_errors:
                    if args.backend == "cuda":
                        print(
                            "[WAIT] CUDA-Worker fehlt weiterhin. Miner bleibt aktiv "
                            "und prüft in 15 Sekunden erneut.",
                            flush=True,
                        )
                        stats.last_status = "Warte auf CUDA-Worker – Prozess bleibt aktiv"
                        stats.write_stats_file()
                        missing_worker_errors = 0
                        time.sleep(15)
                        continue
            else:
                missing_worker_errors = 0

            time.sleep(5)


if __name__ == "__main__":
    main()
