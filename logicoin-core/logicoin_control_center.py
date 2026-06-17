#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import csv
import ctypes
import hashlib
import io
import ipaddress
import json
import math
import os
import platform
import subprocess
import shutil
import sys
import threading
import time
import tkinter as tk
from tkinter import ttk, messagebox, filedialog, simpledialog
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from collections import deque
from pathlib import Path
from typing import Any, Dict, List, Optional

from logicoin_core import (
    PROJECT_NAME,
    COIN_NAME,
    TICKER,
    NETWORK_ID,
    NETWORK_NAME,
    RELEASE_CHANNEL,
    DEFAULT_MINER_ADDRESS,
    create_transfer_transaction,
    generate_keypair_wallet,
    logic_hash_v0,
    GPU_ALGORITHM,
    configure_utf8_stdio,
)

from logicoin_release_tools import (
    backup_wallet,
    restore_wallet,
    export_diagnostics,
    readiness_report,
)

APP_VERSION = "0.12.15.3"
DEFAULT_NODE_URL = "http://127.0.0.1:8080"

BASE_DIR = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
SETTINGS_FILE = BASE_DIR / "logicoin_control_center_settings.json"
WALLET_FILE = BASE_DIR / "logic_wallet.json"
NODE_LOG_FILE = BASE_DIR / "logicoin_node.log"
CPU_MINER_LOG_FILE = BASE_DIR / "logicoin_cpu_miner.log"
CPU_MINER_STATS_FILE = BASE_DIR / "logicoin_cpu_miner_stats.json"
EXTERNAL_MINER_LOG_FILE = BASE_DIR / "logicoin_external_miner.log"
GPU_MINER_STATS_FILE = BASE_DIR / "logicoin_gpu_miner_stats.json"
GPU_BENCHMARK_FILE = BASE_DIR / "logicoin_gpu_benchmark_results.json"
NODE_IDENTITY_FILE = BASE_DIR / "logicoin_node_identity.json"
GPU_MINER_STATS_GLOB = "logicoin_gpu_miner_stats_gpu*.json"
MINER_PROFILES_FILE = BASE_DIR / "logicoin_miner_profiles.json"

POWER_PROFILE_PRESETS = {
    "eco": {
        "cpu": 25,
        "gpu": 30,
        "label": "Eco",
        "description": "Leise, stromsparend, ideal nebenbei.",
    },
    "medium": {
        "cpu": 50,
        "gpu": 60,
        "label": "Medium",
        "description": "Guter Kompromiss aus Leistung und Alltag.",
    },
    "high": {
        "cpu": 75,
        "gpu": 85,
        "label": "High",
        "description": "Hohe Leistung, aber noch kontrolliert.",
    },
    "ultra": {
        "cpu": 100,
        "gpu": 100,
        "label": "Ultra",
        "description": "Maximale Leistung.",
    },
}


DEFAULT_SETTINGS = {
    "node_url": DEFAULT_NODE_URL,
    "miner_address": DEFAULT_MINER_ADDRESS,
    "coin_algorithm": "LOGIC / LogicHash CPU",
    "external_miner_path": "",
    "external_miner_args": "",
    "auto_start_node_with_app": True,

    # Mining-Optimierungsmodus:
    # auto_safe = App/Miner darf nur sichere Software-Parameter wie Intensity/Worksize automatisch wählen.
    # manual_afterburner = App macht keine GPU-Optimierungen; OC/UV komplett manuell über MSI Afterburner.
    # legacy_gpu = konservatives Profil für alte Karten wie GTX 1050 Ti.
    # custom = Nutzer setzt Intensity/Worksize selbst.
    "mining_optimization_mode": "manual_afterburner",
    "gpu_auto_intensity": True,
    "gpu_custom_intensity": 50,
    "gpu_custom_worksize": 128,
    "gpu_temp_safety_enabled": True,
    "gpu_temp_limit_c": 78,
    "gpu_power_limit_control": False,
    "gpu_clock_control": False,
    "afterburner_manual_mode_note": True,

    # v0.10.1 Leistungsprofile
    "mining_power_profile": "medium",
    "cpu_usage_percent": 50,
    "gpu_usage_percent": 70,
    "manual_miner_visible_console": True,
    "auto_apply_power_profile": True,
    "internal_cpu_miner_mode": "visible",

    # v0.12 LAN-Testnet
    "last_peer_url": "",
    "gpu_benchmark_duration_seconds": 10,
    "compact_ui": True,
    "easy_mining_mode": "gpu_cpu",
    "easy_miner_visible": False,
    "logic_test_rate_eur": 0.0,
    "logic_test_rate_usd": 0.0,
}


def creationflags_no_window() -> int:
    if os.name == "nt":
        return getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return 0


def creationflags_new_console() -> int:
    if os.name == "nt":
        return getattr(subprocess, "CREATE_NEW_CONSOLE", 0)
    return 0


def hidden_subprocess_kwargs() -> dict[str, Any]:
    """
    Verhindert unter Windows das kurze Aufblitzen von Konsolenfenstern.

    CREATE_NO_WINDOW reicht je nach EXE/Windows-Kontext nicht immer allein.
    Deshalb wird zusätzlich STARTUPINFO mit SW_HIDE gesetzt.
    """
    kwargs: dict[str, Any] = {
        "creationflags": creationflags_no_window(),
    }

    if os.name == "nt":
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= getattr(subprocess, "STARTF_USESHOWWINDOW", 1)
        startupinfo.wShowWindow = getattr(subprocess, "SW_HIDE", 0)
        kwargs["startupinfo"] = startupinfo

    return kwargs


def run_command_in_visible_console(cmd: list[str], title: str = "Logicoin Miner") -> subprocess.Popen:
    """
    Öffnet ein echtes sichtbares Konsolenfenster und gibt den Prozess zurück.

    v0.12.15.3 Fix:
    Nicht mehr über cmd.exe /k mit verschachtelten Quotes starten.
    Stattdessen direkt Popen(cmd, CREATE_NEW_CONSOLE).
    Das behebt:
        "\"C:\\...\\LogicoinCpuMiner.exe\"" wurde nicht gefunden
    """
    if os.name == "nt":
        return subprocess.Popen(
            cmd,
            cwd=str(BASE_DIR),
            creationflags=creationflags_new_console()
        )

    return subprocess.Popen(cmd, cwd=str(BASE_DIR))


def cpu_miner_console_command(extra_args: list[str]) -> list[str]:
    """
    Bevorzugt die echte Console-EXE LogicoinCpuMiner.exe.
    Falls sie noch nicht gebaut wurde, nutzt es Python + logicoin_headless_miner.py.

    v0.12.15.3:
    - prüft mehrere sinnvolle Orte
    - keine eingebauten Anführungszeichen im Pfad
    """
    candidates = [
        BASE_DIR / "LogicoinCpuMiner.exe",
        BASE_DIR / "dist" / "LogicoinCpuMiner.exe",
    ]

    for cpu_miner_exe in candidates:
        if cpu_miner_exe.exists():
            return [str(cpu_miner_exe)] + extra_args

    cpu_miner_py = BASE_DIR / "logicoin_headless_miner.py"
    if cpu_miner_py.exists():
        return [sys.executable, str(cpu_miner_py)] + extra_args

    raise FileNotFoundError(
        "LogicoinCpuMiner.exe wurde nicht gefunden.\n\n"
        "Bitte BUILD_LOGICOIN_APP_EXE.bat ausführen.\n"
        "Danach BEIDE Dateien aus dist in deinen Logicoin-Ordner kopieren:\n"
        "- LogicoinControlCenter.exe\n"
        "- LogicoinCpuMiner.exe\n\n"
        f"Gesucht wurde in:\n{BASE_DIR}\n{BASE_DIR / 'dist'}"
    )


def python_cmd() -> str:
    return sys.executable


def app_command_for_role(role: str, extra_args: list[str] | None = None) -> list[str]:
    """
    v0.12.15.3: Der verwaltete Node wird bei einer gebauten EXE immer aus
    derselben Control-Center-EXE gestartet. Dadurch sind App und Node
    garantiert dieselbe Version. LogicoinNode.exe bleibt für Standalone.
    """
    extra_args = extra_args or []

    if getattr(sys, "frozen", False):
        if role == "node":
            return [sys.executable, "--role", "node"] + extra_args

        if role == "cpu-miner":
            for candidate in (
                BASE_DIR / "LogicoinCpuMiner.exe",
                BASE_DIR / "dist" / "LogicoinCpuMiner.exe",
            ):
                if candidate.exists():
                    return [str(candidate)] + extra_args
            return [sys.executable, "--role", "cpu-miner"] + extra_args

        if role == "gpu-miner":
            # Der interne GPU-Miner stammt aus derselben EXE wie die Oberfläche.
            # Damit können App und Miner nicht aus verschiedenen Releases stammen.
            return [sys.executable, "--role", "gpu-miner"] + extra_args

        if role == "config":
            return [sys.executable, "--role", "config"] + extra_args

        raise ValueError(f"Unbekannte Rolle: {role}")

    script_map = {
        "node": "logicoin_node.py",
        "cpu-miner": "logicoin_headless_miner.py",
        "gpu-miner": "logicoin_gpu_miner.py",
        "config": "logicoin_config_editor.py",
    }
    script_name = script_map.get(role)
    if not script_name:
        raise ValueError(f"Unbekannte Rolle: {role}")
    script_path = BASE_DIR / script_name
    if not script_path.exists():
        raise FileNotFoundError(f"Datei nicht gefunden: {script_path}")
    return [sys.executable, str(script_path)] + extra_args


def normalize_node_url(url: str) -> str:
    url = url.strip().rstrip("/")
    if not url.startswith("http://") and not url.startswith("https://"):
        url = "http://" + url
    return url


def is_local_node_url(url: str) -> bool:
    try:
        parsed = urllib.parse.urlparse(normalize_node_url(url))
        host = (parsed.hostname or "").strip().lower()
        if host in {"localhost", "127.0.0.1", "::1"}:
            return True
        return ipaddress.ip_address(host).is_loopback
    except Exception:
        return False


def node_port_from_url(url: str) -> int:
    try:
        parsed = urllib.parse.urlparse(normalize_node_url(url))
        return int(parsed.port or (443 if parsed.scheme == "https" else 80))
    except Exception:
        return 8080


def parse_cuda_benchmark_output(text: str) -> dict[str, float] | None:
    for raw_line in str(text or "").splitlines():
        line = raw_line.strip()
        if not line.startswith("BENCH "):
            continue
        parts = line.split()
        if len(parts) < 4:
            continue
        try:
            return {
                "hashes": float(parts[1]),
                "elapsed_ms": float(parts[2]),
                "hashrate_hs": float(parts[3]),
            }
        except Exception:
            continue
    return None


def get_json(url: str, timeout: int = 5) -> Dict[str, Any]:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        try:
            payload = json.loads(body)
            message = payload.get("error") or payload.get("message") or body
        except Exception:
            message = body or str(exc)
        raise RuntimeError(f"HTTP {exc.code}: {message}") from exc


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


def fmt_amount(value: Any) -> str:
    try:
        return f"{float(value):,.8f}"
    except Exception:
        return "0.00000000"


def short_hash(value: Any, length: int = 14) -> str:
    text = str(value)
    if len(text) <= length * 2 + 3:
        return text
    return text[:length] + "..." + text[-length:]


def load_json_file(path: Path, default: Dict[str, Any]) -> Dict[str, Any]:
    if not path.exists():
        path.write_text(json.dumps(default, indent=2, ensure_ascii=False), encoding="utf-8")
        return dict(default)
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            out = dict(default)
            out.update(data)
            return out
    except Exception:
        pass
    return dict(default)


def save_json_file(path: Path, data: Dict[str, Any]) -> None:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def generate_wallet() -> Dict[str, Any]:
    return generate_keypair_wallet(APP_VERSION)


def mining_test_wallet() -> Dict[str, Any]:
    wallet = generate_keypair_wallet(APP_VERSION)
    wallet["note"] = (
        "Signierte Public-Testnet-Mining-Wallet. "
        "Private Key geheim halten und sichern."
    )
    return wallet


def load_wallet() -> Optional[Dict[str, Any]]:
    if not WALLET_FILE.exists():
        return None
    try:
        with WALLET_FILE.open("r", encoding="utf-8") as f:
            data = json.load(f)
        if (
            isinstance(data, dict)
            and data.get("address")
            and data.get("network_id") == NETWORK_ID
        ):
            return data
    except Exception:
        return None
    return None


def save_wallet(wallet: Dict[str, Any]) -> None:
    save_json_file(WALLET_FILE, wallet)


def resolve_miner_path(path_text: str) -> Path:
    """
    v0.12.15.3:
    Robuste Miner-Pfadsuche.

    Problem:
    Nach PyInstaller-Build liegt im dist-Ordner nicht logicoin_gpu_miner.py,
    sondern LogicoinGpuMiner.exe.

    Deshalb werden bekannte LOGIC-Miner automatisch auf die passende EXE
    oder Python-Datei aufgelöst.
    """
    raw = str(path_text).strip().strip('"')

    if not raw:
        raise FileNotFoundError("Miner-Datei nicht angegeben.")

    # Alias-Unterstützung für unseren internen GPU-Miner
    lowered = raw.lower().replace("\\", "/")
    gpu_aliases = {
        "logicoin_gpu_miner.py",
        "./logicoin_gpu_miner.py",
        "logicoin_gpuminer.exe",
        "logicoingpuminer.exe",
        "logicoin gpu miner",
        "logicoin-gpu-miner",
    }

    candidates: list[Path] = []

    # Original
    p = Path(raw)
    if p.is_absolute():
        candidates.append(p)
    else:
        candidates.append(BASE_DIR / p)

    # Wenn das Profil die Python-Datei nennt, aber die App als EXE läuft,
    # suche automatisch die gebaute EXE.
    if lowered in gpu_aliases or lowered.endswith("/logicoin_gpu_miner.py"):
        candidates.extend([
            BASE_DIR / "LogicoinGpuMiner.exe",
            BASE_DIR / "dist" / "LogicoinGpuMiner.exe",
            BASE_DIR / "logicoin_gpu_miner.py",
            BASE_DIR / "dist" / "logicoin_gpu_miner.py",
        ])

    # Allgemeine EXE/PY-Fallbacks
    if raw.lower().endswith(".py"):
        stem = Path(raw).stem
        exe_name = "".join(part.capitalize() for part in stem.split("_")) + ".exe"
        candidates.extend([
            BASE_DIR / exe_name,
            BASE_DIR / "dist" / exe_name,
        ])

    # Duplikate entfernen
    seen: set[str] = set()
    unique_candidates: list[Path] = []
    for c in candidates:
        key = str(c).lower()
        if key not in seen:
            seen.add(key)
            unique_candidates.append(c)

    for candidate in unique_candidates:
        if candidate.exists():
            return candidate

    searched = "\n".join(str(c) for c in unique_candidates)
    raise FileNotFoundError(
        "Miner-Datei nicht gefunden.\n\n"
        f"Eingetragen war:\n{raw}\n\n"
        f"Gesucht wurde in:\n{searched}\n\n"
        "Tipp:\n"
        "- Nach EXE-Build nutze LogicoinGpuMiner.exe\n"
        "- Oder starte die Python-Version im entpackten Quellordner."
    )


def build_external_miner_command(path_text: str, args_text: str) -> tuple[list[str], Path]:
    path = resolve_miner_path(path_text)
    args = args_text.split() if args_text.strip() else []

    if path.suffix.lower() == ".py":
        return [sys.executable, str(path)] + args, path.parent

    return [str(path)] + args, path.parent


# ========================================================
# MINER PROFILE MANAGEMENT
# ========================================================

def default_miner_profiles() -> list[dict[str, Any]]:
    return [
        {
            "name": "LOGIC CPU Medium Background",
            "type": "internal_cpu",
            "coin_algorithm": "LOGIC / LogicHash CPU",
            "miner_address": DEFAULT_MINER_ADDRESS,
            "mining_power_profile": "medium",
            "cpu_usage_percent": 50,
            "gpu_usage_percent": 60,
            "internal_cpu_miner_mode": "background",
            "mining_optimization_mode": "manual_afterburner",
            "external_miner_path": "",
            "external_miner_args": "",
            "manual_miner_visible_console": True,
            "gpu_custom_intensity": 55,
            "gpu_custom_worksize": 128,
            "note": "Standardprofil für leises LOGIC-CPU-Mining im Hintergrund."
        },
        {
            "name": "LOGIC CPU Ultra Visible",
            "type": "internal_cpu",
            "coin_algorithm": "LOGIC / LogicHash CPU",
            "miner_address": DEFAULT_MINER_ADDRESS,
            "mining_power_profile": "ultra",
            "cpu_usage_percent": 100,
            "gpu_usage_percent": 100,
            "internal_cpu_miner_mode": "visible",
            "mining_optimization_mode": "manual_afterburner",
            "external_miner_path": "",
            "external_miner_args": "",
            "manual_miner_visible_console": True,
            "gpu_custom_intensity": 100,
            "gpu_custom_worksize": 128,
            "note": "Maximales sichtbares LOGIC-CPU-Mining."
        },
        {
            "name": "LOGIC CPU Eco Background",
            "type": "internal_cpu",
            "coin_algorithm": "LOGIC / LogicHash CPU",
            "miner_address": DEFAULT_MINER_ADDRESS,
            "mining_power_profile": "eco",
            "cpu_usage_percent": 25,
            "gpu_usage_percent": 30,
            "internal_cpu_miner_mode": "background",
            "mining_optimization_mode": "manual_afterburner",
            "external_miner_path": "",
            "external_miner_args": "",
            "manual_miner_visible_console": True,
            "gpu_custom_intensity": 25,
            "gpu_custom_worksize": 128,
            "note": "Sehr schonendes Profil für nebenbei."
        },

        {
            "name": "LOGIC GPU CUDA Test Auto",
            "type": "external",
            "coin_algorithm": "External Miner / Custom",
            "miner_address": DEFAULT_MINER_ADDRESS,
            "mining_power_profile": "high",
            "cpu_usage_percent": 10,
            "gpu_usage_percent": 85,
            "internal_cpu_miner_mode": "background",
            "mining_optimization_mode": "auto_safe",
            "external_miner_path": "LogicoinGpuMiner.exe",
            "external_miner_args": "--backend auto --device 0 --batch-nonces 262144",
            "manual_miner_visible_console": True,
            "gpu_custom_intensity": 85,
            "gpu_custom_worksize": 256,
            "note": "Erster LOGIC GPU-Testminer. Nutzt CUDA-Worker, falls gebaut, sonst CPU-Fallback."
        },
        {
            "name": "GTX 1050 Ti Legacy vorbereitet",
            "type": "external",
            "coin_algorithm": "External Miner / Custom",
            "miner_address": DEFAULT_MINER_ADDRESS,
            "mining_power_profile": "eco",
            "cpu_usage_percent": 10,
            "gpu_usage_percent": 45,
            "internal_cpu_miner_mode": "background",
            "mining_optimization_mode": "legacy_gpu",
            "external_miner_path": "",
            "external_miner_args": "--device 0 --profile legacy --intensity 45",
            "manual_miner_visible_console": True,
            "gpu_custom_intensity": 45,
            "gpu_custom_worksize": 128,
            "note": "Platzhalter für späteren Legacy-GPU-Miner. Noch kein nativer LOGIC-GPU-Miner enthalten."
        },
        {
            "name": "RTX High vorbereitet",
            "type": "external",
            "coin_algorithm": "External Miner / Custom",
            "miner_address": DEFAULT_MINER_ADDRESS,
            "mining_power_profile": "high",
            "cpu_usage_percent": 10,
            "gpu_usage_percent": 85,
            "internal_cpu_miner_mode": "background",
            "mining_optimization_mode": "auto_safe",
            "external_miner_path": "",
            "external_miner_args": "--profile rtx-high --intensity 85",
            "manual_miner_visible_console": True,
            "gpu_custom_intensity": 85,
            "gpu_custom_worksize": 256,
            "note": "Platzhalter für späteren RTX-CUDA/OpenCL-Miner."
        },
    ]


def load_miner_profiles() -> list[dict[str, Any]]:
    if not MINER_PROFILES_FILE.exists():
        profiles = default_miner_profiles()
        MINER_PROFILES_FILE.write_text(json.dumps(profiles, indent=2, ensure_ascii=False), encoding="utf-8")
        return profiles

    try:
        data = json.loads(MINER_PROFILES_FILE.read_text(encoding="utf-8"))
        if isinstance(data, list) and data:
            return [p for p in data if isinstance(p, dict) and p.get("name")]
    except Exception:
        pass

    profiles = default_miner_profiles()
    MINER_PROFILES_FILE.write_text(json.dumps(profiles, indent=2, ensure_ascii=False), encoding="utf-8")
    return profiles


def save_miner_profiles(profiles: list[dict[str, Any]]) -> None:
    MINER_PROFILES_FILE.write_text(json.dumps(profiles, indent=2, ensure_ascii=False), encoding="utf-8")


def profile_summary_text(profile: dict[str, Any]) -> str:
    if not profile:
        return "Kein Profil ausgewählt."

    lines = [
        f"Name: {profile.get('name', '-')}",
        f"Typ: {profile.get('type', '-')}",
        f"Coin/Algo: {profile.get('coin_algorithm', '-')}",
        f"Power-Profil: {profile.get('mining_power_profile', '-')}",
        f"CPU/GPU: {profile.get('cpu_usage_percent', '-')}% / {profile.get('gpu_usage_percent', '-')}%",
        f"Interner CPU-Miner-Modus: {profile.get('internal_cpu_miner_mode', '-')}",
        f"Optimierung: {profile.get('mining_optimization_mode', '-')}",
        f"Miner-Adresse: {profile.get('miner_address', '-')}",
        f"Externer Miner: {profile.get('external_miner_path', '-') or '-'}",
        f"Argumente: {profile.get('external_miner_args', '-') or '-'}",
        f"GPU Intensity/Worksize: {profile.get('gpu_custom_intensity', '-')} / {profile.get('gpu_custom_worksize', '-')}",
        "",
        f"Notiz: {profile.get('note', '')}",
    ]
    return "\n".join(lines)



class _NvmlUtilization(ctypes.Structure):
    _fields_ = [
        ("gpu", ctypes.c_uint),
        ("memory", ctypes.c_uint),
    ]


class _NvmlMemory(ctypes.Structure):
    _fields_ = [
        ("total", ctypes.c_ulonglong),
        ("free", ctypes.c_ulonglong),
        ("used", ctypes.c_ulonglong),
    ]


_NVML_LOCK = threading.Lock()
_NVML_LIB: Any = None
_NVML_READY = False
_NVML_FAILED = False


def _configure_nvml_signatures(lib: Any) -> None:
    """Setzt korrekte 64-Bit-Signaturen für die verwendeten NVML-Funktionen."""
    function_specs = {
        "nvmlInit_v2": ([], ctypes.c_int),
        "nvmlInit": ([], ctypes.c_int),
        "nvmlDeviceGetCount_v2": (
            [ctypes.POINTER(ctypes.c_uint)],
            ctypes.c_int,
        ),
        "nvmlDeviceGetCount": (
            [ctypes.POINTER(ctypes.c_uint)],
            ctypes.c_int,
        ),
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
        "nvmlDeviceGetPowerManagementLimit": (
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

    for name, (argtypes, restype) in function_specs.items():
        fn = getattr(lib, name, None)
        if fn is None:
            continue
        try:
            fn.argtypes = argtypes
            fn.restype = restype
        except Exception:
            pass


def _load_nvml_library() -> Any:
    global _NVML_LIB, _NVML_READY, _NVML_FAILED

    if os.name != "nt":
        return None
    if _NVML_READY:
        return _NVML_LIB
    if _NVML_FAILED:
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

    with _NVML_LOCK:
        if _NVML_READY:
            return _NVML_LIB

        for candidate in candidates:
            try:
                if not candidate.exists():
                    continue

                lib = ctypes.WinDLL(str(candidate))
                _configure_nvml_signatures(lib)
                init_fn = (
                    getattr(lib, "nvmlInit_v2", None)
                    or getattr(lib, "nvmlInit", None)
                )
                if init_fn is None:
                    continue

                result = int(init_fn())
                if result not in (0, 5):
                    continue

                _NVML_LIB = lib
                _NVML_READY = True
                return _NVML_LIB
            except Exception:
                continue

        _NVML_FAILED = True
        return None


def read_nvidia_gpus_nvml() -> list[dict[str, object]]:
    """
    Liest NVIDIA-GPUs direkt aus nvml.dll.

    Dadurch startet die App nicht mehr regelmäßig nvidia-smi.exe.
    Das verhindert Terminal-Flackern und den Windows-Fehler 0xc0000142.
    """
    lib = _load_nvml_library()
    if lib is None:
        return []

    try:
        count_fn = (
            getattr(lib, "nvmlDeviceGetCount_v2", None)
            or getattr(lib, "nvmlDeviceGetCount", None)
        )
        handle_fn = (
            getattr(lib, "nvmlDeviceGetHandleByIndex_v2", None)
            or getattr(lib, "nvmlDeviceGetHandleByIndex", None)
        )
        name_fn = getattr(lib, "nvmlDeviceGetName", None)

        if count_fn is None or handle_fn is None or name_fn is None:
            return []

        count = ctypes.c_uint(0)
        if int(count_fn(ctypes.byref(count))) != 0:
            return []

        gpus: list[dict[str, object]] = []

        with _NVML_LOCK:
            for index in range(int(count.value)):
                handle = ctypes.c_void_p()
                if int(handle_fn(ctypes.c_uint(index), ctypes.byref(handle))) != 0:
                    continue

                name_buffer = ctypes.create_string_buffer(128)
                name = f"NVIDIA GPU {index}"
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

                temperature = None
                power_w = None
                power_limit_w = None
                utilization = None
                memory_used_mb = None
                memory_total_mb = None

                try:
                    value = ctypes.c_uint(0)
                    fn = getattr(lib, "nvmlDeviceGetTemperature", None)
                    if fn is not None and int(
                        fn(handle, ctypes.c_uint(0), ctypes.byref(value))
                    ) == 0:
                        temperature = float(value.value)
                except Exception:
                    pass

                try:
                    value = ctypes.c_uint(0)
                    fn = getattr(lib, "nvmlDeviceGetPowerUsage", None)
                    if fn is not None and int(fn(handle, ctypes.byref(value))) == 0:
                        power_w = float(value.value) / 1000.0
                except Exception:
                    pass

                try:
                    value = ctypes.c_uint(0)
                    fn = getattr(lib, "nvmlDeviceGetPowerManagementLimit", None)
                    if fn is not None and int(fn(handle, ctypes.byref(value))) == 0:
                        power_limit_w = float(value.value) / 1000.0
                except Exception:
                    pass

                try:
                    util = _NvmlUtilization()
                    fn = getattr(lib, "nvmlDeviceGetUtilizationRates", None)
                    if fn is not None and int(fn(handle, ctypes.byref(util))) == 0:
                        utilization = float(util.gpu)
                except Exception:
                    pass

                try:
                    memory = _NvmlMemory()
                    fn = getattr(lib, "nvmlDeviceGetMemoryInfo", None)
                    if fn is not None and int(fn(handle, ctypes.byref(memory))) == 0:
                        memory_used_mb = float(memory.used) / (1024.0 * 1024.0)
                        memory_total_mb = float(memory.total) / (1024.0 * 1024.0)
                except Exception:
                    pass

                gpus.append({
                    "index": index,
                    "name": name,
                    "temperature_c": temperature,
                    "power_w": power_w,
                    "power_limit_w": power_limit_w,
                    "utilization_percent": utilization,
                    "memory_used_mb": memory_used_mb,
                    "memory_total_mb": memory_total_mb,
                })

        return gpus
    except Exception:
        return []



class NvmlRollingMonitor:
    """
    Sammelt NVIDIA-Sensordaten kontinuierlich.

    Einzelne NVML-Abfragen können bei kurzen CUDA-Batches genau in einer
    Leerlaufphase landen. Deshalb zeigt die Oberfläche einen 5-Sekunden-
    Mittelwert und das Maximum desselben Fensters.
    """

    def __init__(
        self,
        sample_interval_seconds: float = 0.25,
        retention_seconds: float = 70.0,
    ) -> None:
        self.sample_interval_seconds = max(
            0.10,
            float(sample_interval_seconds),
        )
        self.retention_seconds = max(
            10.0,
            float(retention_seconds),
        )
        self._samples: dict[
            int,
            deque[dict[str, float | str | None]],
        ] = {}
        self._latest: dict[int, dict[str, object]] = {}
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return

        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._loop,
            daemon=True,
            name="logicoin-nvml-monitor",
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()

    def _loop(self) -> None:
        while not self._stop_event.is_set():
            started = time.monotonic()

            try:
                gpus = read_nvidia_gpus_nvml()
                now = time.time()

                with self._lock:
                    for gpu in gpus:
                        try:
                            device = int(gpu.get("index", -1))
                        except Exception:
                            continue

                        self._latest[device] = dict(gpu)
                        bucket = self._samples.setdefault(
                            device,
                            deque(),
                        )
                        bucket.append({
                            "time": now,
                            "utilization_percent": self._number(
                                gpu.get("utilization_percent")
                            ),
                            "power_w": self._number(
                                gpu.get("power_w")
                            ),
                            "temperature_c": self._number(
                                gpu.get("temperature_c")
                            ),
                        })

                    cutoff = now - self.retention_seconds
                    for bucket in self._samples.values():
                        while (
                            bucket
                            and float(bucket[0].get("time") or 0.0) < cutoff
                        ):
                            bucket.popleft()
            except Exception:
                pass

            elapsed = time.monotonic() - started
            wait_seconds = max(
                0.02,
                self.sample_interval_seconds - elapsed,
            )
            self._stop_event.wait(wait_seconds)

    @staticmethod
    def _number(value: object) -> float | None:
        try:
            return float(value)
        except Exception:
            return None

    @staticmethod
    def _average(values: list[float]) -> float | None:
        if not values:
            return None
        return sum(values) / len(values)

    def snapshot(
        self,
        window_seconds: float = 5.0,
    ) -> list[dict[str, object]]:
        now = time.time()
        cutoff = now - max(1.0, float(window_seconds))
        result: list[dict[str, object]] = []

        with self._lock:
            devices = sorted(
                set(self._latest) | set(self._samples)
            )

            for device in devices:
                latest = dict(self._latest.get(device, {}))
                samples = [
                    sample
                    for sample in self._samples.get(device, ())
                    if float(sample.get("time") or 0.0) >= cutoff
                ]

                util_values = [
                    float(sample["utilization_percent"])
                    for sample in samples
                    if sample.get("utilization_percent") is not None
                ]
                power_values = [
                    float(sample["power_w"])
                    for sample in samples
                    if sample.get("power_w") is not None
                ]
                temp_values = [
                    float(sample["temperature_c"])
                    for sample in samples
                    if sample.get("temperature_c") is not None
                ]

                util_avg = self._average(util_values)
                power_avg = self._average(power_values)
                temp_avg = self._average(temp_values)

                latest["index"] = device
                latest["nvml_sample_count"] = len(samples)
                latest["nvml_window_seconds"] = float(window_seconds)
                latest["utilization_instant_percent"] = (
                    self._number(
                        latest.get("utilization_percent")
                    )
                )
                latest["utilization_avg_5s_percent"] = util_avg
                latest["utilization_max_5s_percent"] = (
                    max(util_values)
                    if util_values
                    else None
                )
                latest["power_instant_w"] = self._number(
                    latest.get("power_w")
                )
                latest["power_avg_5s_w"] = power_avg
                latest["temperature_avg_5s_c"] = temp_avg

                # Kompatibilitätsfelder nutzen künftig den stabilen Mittelwert.
                if util_avg is not None:
                    latest["utilization_percent"] = util_avg
                if power_avg is not None:
                    latest["power_w"] = power_avg

                result.append(latest)

        return result



UI_COLORS = {
    "window": "#22314D",
    "surface": "#2D3F61",
    "surface_alt": "#364C73",
    "surface_light": "#435F8C",
    "surface_soft": "#4C6B9C",
    "border": "#6D88AE",
    "text": "#F7FAFF",
    "muted": "#C9D5E8",
    "entry": "#F7FAFF",
    "entry_text": "#172033",
    "green": "#68E7C2",
    "cyan": "#76D7FF",
    "purple": "#D0B0FF",
    "orange": "#FFC77D",
    "red": "#FF8FA2",
    "yellow": "#FFE08A",
}


def enable_windows_11_rounded_corners(window: tk.Misc) -> None:
    """Aktiviert native Windows-11-Fensterrundungen, falls verfügbar."""
    if os.name != "nt":
        return

    try:
        window.update_idletasks()
        hwnd = int(window.winfo_id())
        preference = ctypes.c_int(2)  # DWMWCP_ROUND
        attribute = 33  # DWMWA_WINDOW_CORNER_PREFERENCE

        result = ctypes.windll.dwmapi.DwmSetWindowAttribute(
            ctypes.c_void_p(hwnd),
            ctypes.c_uint(attribute),
            ctypes.byref(preference),
            ctypes.sizeof(preference),
        )

        if result != 0:
            parent_hwnd = ctypes.windll.user32.GetParent(ctypes.c_void_p(hwnd))
            if parent_hwnd:
                ctypes.windll.dwmapi.DwmSetWindowAttribute(
                    ctypes.c_void_p(parent_hwnd),
                    ctypes.c_uint(attribute),
                    ctypes.byref(preference),
                    ctypes.sizeof(preference),
                )
    except Exception:
        pass


def calculate_expected_mining_returns(
    hashrate_hs: float,
    difficulty: int,
    block_reward: float,
) -> dict[str, float]:
    """
    Statistischer Erwartungswert für Leading-Hex-Zero-PoW.

    Bei Difficulty d liegt die Trefferwahrscheinlichkeit pro Hash ungefähr bei
    1 / 16**d. Das ist eine Schätzung und keine garantierte Auszahlung.
    """
    hashrate = max(0.0, float(hashrate_hs))
    diff = max(0, int(difficulty))
    reward = max(0.0, float(block_reward))

    expected_hashes_per_block = float(16 ** diff)
    logic_per_second = (
        hashrate / expected_hashes_per_block * reward
        if expected_hashes_per_block > 0
        else 0.0
    )

    return {
        "hour": logic_per_second * 3600,
        "day": logic_per_second * 86400,
        "month": logic_per_second * 86400 * 30,
        "year": logic_per_second * 86400 * 365,
    }


class RoundedMetricCard(tk.Canvas):
    """Leichte Canvas-Karte mit abgerundeten Ecken und farbigem Akzent."""

    def __init__(
        self,
        parent: tk.Widget,
        title: str,
        value_var: tk.StringVar,
        subtitle: str = "",
        accent: str = UI_COLORS["green"],
        height: int = 138,
    ) -> None:
        super().__init__(
            parent,
            height=height,
            bg=UI_COLORS["window"],
            highlightthickness=0,
            bd=0,
        )
        self.title_text = title
        self.value_var = value_var
        self.subtitle_text = subtitle
        self.accent = accent
        self.card_height = height
        self.bind("<Configure>", self._redraw)
        self.value_var.trace_add("write", lambda *_args: self._redraw())

    def _rounded_polygon(
        self,
        x1: float,
        y1: float,
        x2: float,
        y2: float,
        radius: float,
        **kwargs: Any,
    ) -> int:
        points = [
            x1 + radius, y1,
            x2 - radius, y1,
            x2, y1,
            x2, y1 + radius,
            x2, y2 - radius,
            x2, y2,
            x2 - radius, y2,
            x1 + radius, y2,
            x1, y2,
            x1, y2 - radius,
            x1, y1 + radius,
            x1, y1,
        ]
        return self.create_polygon(
            points,
            smooth=True,
            splinesteps=30,
            **kwargs,
        )

    def _redraw(self, *_args: Any) -> None:
        self.delete("all")
        width = max(190, self.winfo_width())
        height = max(self.card_height, self.winfo_height())
        margin = 4
        radius = 17

        self._rounded_polygon(
            margin,
            margin,
            width - margin,
            height - margin,
            radius,
            fill=UI_COLORS["surface_alt"],
            outline=UI_COLORS["border"],
            width=1,
        )
        self._rounded_polygon(
            margin + 7,
            margin + 10,
            margin + 13,
            height - margin - 10,
            3,
            fill=self.accent,
            outline=self.accent,
        )

        text_x = margin + 24
        wrap = max(145, width - text_x - 12)

        self.create_text(
            text_x,
            23,
            anchor="nw",
            text=self.title_text,
            fill=UI_COLORS["muted"],
            font=("Segoe UI", 10),
            width=wrap,
        )
        value_lines = max(1, self.value_var.get().count("\n") + 1)
        if self.card_height <= 116 and value_lines >= 3:
            value_font_size = 9
        elif self.card_height <= 116:
            value_font_size = 10
        else:
            value_font_size = 17
        self.create_text(
            text_x,
            47,
            anchor="nw",
            text=self.value_var.get(),
            fill=self.accent,
            font=("Segoe UI", value_font_size, "bold"),
            width=wrap,
        )
        if self.subtitle_text:
            self.create_text(
                text_x,
                height - 31,
                anchor="nw",
                text=self.subtitle_text,
                fill=UI_COLORS["muted"],
                font=("Segoe UI", 8),
                width=wrap,
            )


class LogicoinControlCenter(tk.Tk):
    def __init__(self) -> None:
        self.gpu_miner_processes: dict[int, subprocess.Popen] = {}
        self.gpu_miner_log_handles: dict[int, object] = {}
        self.gpu_miner_desired_devices: set[int] = set()
        self.gpu_miner_visible_by_device: dict[int, bool] = {}
        self.gpu_miner_restart_attempts: dict[int, int] = {}
        self.gpu_miner_next_restart: dict[int, float] = {}
        self.gpu_miner_started_at: dict[int, float] = {}
        self.gpu_miner_last_exit: dict[int, int | None] = {}
        super().__init__()

        self.title(f"Logicoin / LOGIC Control Center v{APP_VERSION}")
        self.geometry("1180x760")
        self.minsize(980, 650)

        self.settings = load_json_file(SETTINGS_FILE, DEFAULT_SETTINGS)
        self.miner_profiles = load_miner_profiles()
        self.wallet = load_wallet()
        self.first_run_wallet_created = False
        self.incompatible_wallet_present = (
            WALLET_FILE.exists()
            and self.wallet is None
        )

        # In einem komplett neuen Public-Testnet-Ordner wird automatisch
        # eine signierte Wallet erstellt. Eine vorhandene inkompatible Wallet
        # wird niemals überschrieben.
        if self.wallet is None and not WALLET_FILE.exists():
            self.wallet = generate_wallet()
            save_wallet(self.wallet)
            self.settings["miner_address"] = self.wallet["address"]
            save_json_file(SETTINGS_FILE, self.settings)
            self.first_run_wallet_created = True

        self.node_process: Optional[subprocess.Popen] = None
        self.cpu_miner_process: Optional[subprocess.Popen] = None
        self.external_miner_process: Optional[subprocess.Popen] = None
        self.node_log_handle = None
        self.cpu_miner_log_handle = None
        self.external_miner_log_handle = None
        self.node_repair_in_progress = False
        self.node_verify_attempt = 0
        self.connected_node_version = ""
        self.gpu_benchmark_running = False
        self.gpu_benchmark_results: list[dict[str, Any]] = []
        self.latest_network_params: dict[str, Any] = {}
        self.current_total_mining_hashrate_hs = 0.0
        self.earnings_expanded = False
        self._card_accent_index = 0

        self.nvml_monitor = NvmlRollingMonitor(
            sample_interval_seconds=0.25,
            retention_seconds=70.0,
        )
        self.nvml_monitor.start()

        self.node_url_var = tk.StringVar(value=self.settings.get("node_url", DEFAULT_NODE_URL))
        self.miner_address_var = tk.StringVar(value=self.settings.get("miner_address", DEFAULT_MINER_ADDRESS))
        self.coin_algo_var = tk.StringVar(value=self.settings.get("coin_algorithm", "LOGIC / LogicHash CPU"))
        self.external_path_var = tk.StringVar(value=self.settings.get("external_miner_path", ""))
        self.external_args_var = tk.StringVar(value=self.settings.get("external_miner_args", ""))
        self.auto_start_node_var = tk.BooleanVar(value=bool(self.settings.get("auto_start_node_with_app", True)))

        self.optimization_mode_var = tk.StringVar(value=self.settings.get("mining_optimization_mode", "manual_afterburner"))
        self.gpu_auto_intensity_var = tk.BooleanVar(value=bool(self.settings.get("gpu_auto_intensity", True)))
        self.gpu_custom_intensity_var = tk.IntVar(value=int(self.settings.get("gpu_custom_intensity", 50)))
        self.gpu_custom_worksize_var = tk.IntVar(value=int(self.settings.get("gpu_custom_worksize", 128)))
        self.gpu_temp_safety_var = tk.BooleanVar(value=bool(self.settings.get("gpu_temp_safety_enabled", True)))
        self.gpu_temp_limit_var = tk.IntVar(value=int(self.settings.get("gpu_temp_limit_c", 78)))
        self.gpu_power_limit_control_var = tk.BooleanVar(value=bool(self.settings.get("gpu_power_limit_control", False)))
        self.gpu_clock_control_var = tk.BooleanVar(value=bool(self.settings.get("gpu_clock_control", False)))

        self.mining_power_profile_var = tk.StringVar(value=self.settings.get("mining_power_profile", "medium"))
        self.cpu_usage_percent_var = tk.IntVar(value=int(self.settings.get("cpu_usage_percent", 50)))
        self.gpu_usage_percent_var = tk.IntVar(value=int(self.settings.get("gpu_usage_percent", 70)))
        self.manual_miner_console_var = tk.BooleanVar(value=bool(self.settings.get("manual_miner_visible_console", True)))
        self.auto_apply_power_profile_var = tk.BooleanVar(value=bool(self.settings.get("auto_apply_power_profile", True)))
        self.internal_cpu_miner_mode_var = tk.StringVar(value=self.settings.get("internal_cpu_miner_mode", "visible"))
        self.easy_mining_mode_var = tk.StringVar(value=self.settings.get("easy_mining_mode", "gpu_cpu"))
        self.easy_miner_visible_var = tk.BooleanVar(value=False)
        self.logic_test_rate_eur_var = tk.DoubleVar(value=float(self.settings.get("logic_test_rate_eur", 0.0)))
        self.logic_test_rate_usd_var = tk.DoubleVar(value=float(self.settings.get("logic_test_rate_usd", 0.0)))

        self.auto_refresh_var = tk.BooleanVar(value=True)

        self.mining_power_profile_var.trace_add("write", self.on_power_profile_changed)
        self.status_var = tk.StringVar(value="Bereit.")
        self.benchmark_var = tk.StringVar(value="Noch kein Benchmark.")

        self.dash_height_value_var = tk.StringVar(value="-")
        self.dash_node_value_var = tk.StringVar(value="-")
        self.dash_cpu_miner_value_var = tk.StringVar(value="INAKTIV")
        self.dash_gpu_miner_value_var = tk.StringVar(value="INAKTIV")
        self.dash_gpu_total_hashrate_var = tk.StringVar(value="0 H/s")
        self.dash_wallet_balance_var = tk.StringVar(value="0 LOGIC")
        self.dash_wallet_value_var = tk.StringVar(value="0,00 € | 0,00 $")
        self.dash_mined_value_var = tk.StringVar(value="0 LOGIC gemint")
        self.wallet_address_var = tk.StringVar(value=self.wallet.get("address") if self.wallet else "-")
        self.send_to_var = tk.StringVar(value="")
        self.send_amount_var = tk.StringVar(value="1")
        self.send_fee_var = tk.StringVar(value="0.01")
        self.send_memo_var = tk.StringVar(value="")
        first_profile = self.miner_profiles[0]["name"] if self.miner_profiles else ""
        self.selected_miner_profile_var = tk.StringVar(value=first_profile)

        self.peer_url_var = tk.StringVar(value=self.settings.get("last_peer_url", ""))
        self.local_lan_url_var = tk.StringVar(value="-")
        self.network_summary_var = tk.StringVar(value="Noch keine Netzwerkdaten.")
        self.node_version_badge_var = tk.StringVar(value=f"App v{APP_VERSION} | Node wird geprüft")
        self.gpu_benchmark_device_var = tk.StringVar(value="0")
        self.gpu_benchmark_duration_var = tk.IntVar(value=int(self.settings.get("gpu_benchmark_duration_seconds", 20)))
        self.gpu_benchmark_status_var = tk.StringVar(value="GPU-Benchmark bereit.")
        self.earnings_summary_var = tk.StringVar(value="Mining starten, um eine Schätzung zu sehen.")
        self.earnings_hour_var = tk.StringVar(value="0 LOGIC")
        self.earnings_day_var = tk.StringVar(value="0 LOGIC")
        self.earnings_month_var = tk.StringVar(value="0 LOGIC")
        self.earnings_year_var = tk.StringVar(value="0 LOGIC")

        self._build_ui()

        self.after(80, lambda: enable_windows_11_rounded_corners(self))
        self.after(300, self.show_public_testnet_first_run_notice)
        self.after(350, lambda: self.ensure_current_node_async(force_start=bool(self.auto_start_node_var.get())))

        self.after(1000, self.refresh_all_async)
        self.after(1400, self._gpu_miner_supervisor_loop)
        self.after(1500, self._mining_dashboard_loop)
        self.after(2500, self._wallet_dashboard_loop)
        self.after(5000, self._auto_refresh_loop)
        self.protocol("WM_DELETE_WINDOW", self.on_close)

    # UI

    def _build_ui(self) -> None:
        self.configure(bg=UI_COLORS["window"])
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except Exception:
            pass

        style.configure("TFrame", background=UI_COLORS["window"])
        style.configure("TLabel", background=UI_COLORS["window"], foreground=UI_COLORS["text"])
        style.configure(
            "Header.TLabel",
            background=UI_COLORS["window"],
            foreground=UI_COLORS["green"],
            font=("Segoe UI Variable Display", 16, "bold"),
        )
        style.configure("Small.TLabel", background=UI_COLORS["window"], foreground=UI_COLORS["muted"])

        style.configure(
            "TButton",
            background=UI_COLORS["surface_light"],
            foreground=UI_COLORS["text"],
            borderwidth=0,
            padding=(9, 6),
            font=("Segoe UI", 9),
        )
        style.map(
            "TButton",
            background=[("active", UI_COLORS["surface_soft"]), ("pressed", UI_COLORS["surface_alt"])],
            foreground=[("disabled", UI_COLORS["muted"])],
        )
        style.configure(
            "Accent.TButton",
            background=UI_COLORS["green"],
            foreground="#10243A",
            borderwidth=0,
            padding=(11, 7),
            font=("Segoe UI", 9, "bold"),
        )
        style.map(
            "Accent.TButton",
            background=[("active", "#82EBCB"), ("pressed", "#43CFA4")],
        )
        style.configure(
            "Stop.TButton",
            background=UI_COLORS["red"],
            foreground="#31101A",
            borderwidth=0,
            padding=(10, 7),
            font=("Segoe UI", 9, "bold"),
        )
        style.map("Stop.TButton", background=[("active", "#FF9CAF")])

        style.configure(
            "TEntry",
            fieldbackground=UI_COLORS["entry"],
            foreground=UI_COLORS["entry_text"],
            bordercolor=UI_COLORS["border"],
            insertcolor=UI_COLORS["entry_text"],
            padding=5,
        )
        style.configure(
            "TCombobox",
            fieldbackground=UI_COLORS["entry"],
            background=UI_COLORS["entry"],
            foreground=UI_COLORS["entry_text"],
            arrowcolor=UI_COLORS["entry_text"],
            padding=4,
        )
        style.map(
            "TCombobox",
            fieldbackground=[("readonly", UI_COLORS["entry"])],
            foreground=[("readonly", UI_COLORS["entry_text"])],
        )
        style.configure("TCheckbutton", background=UI_COLORS["window"], foreground=UI_COLORS["text"])
        style.configure("TRadiobutton", background=UI_COLORS["window"], foreground=UI_COLORS["text"])

        style.configure(
            "TLabelframe",
            background=UI_COLORS["surface"],
            foreground=UI_COLORS["text"],
            bordercolor=UI_COLORS["border"],
            relief="flat",
        )
        style.configure(
            "TLabelframe.Label",
            background=UI_COLORS["surface"],
            foreground=UI_COLORS["cyan"],
            font=("Segoe UI", 9, "bold"),
        )

        style.configure("TNotebook", background=UI_COLORS["window"], borderwidth=0)
        style.configure(
            "TNotebook.Tab",
            background=UI_COLORS["surface"],
            foreground=UI_COLORS["muted"],
            borderwidth=0,
            padding=(11, 7),
        )
        style.map(
            "TNotebook.Tab",
            background=[
                ("selected", UI_COLORS["surface_light"]),
                ("active", UI_COLORS["surface_alt"]),
            ],
            foreground=[("selected", UI_COLORS["text"])],
        )

        style.configure(
            "Treeview",
            background=UI_COLORS["entry"],
            fieldbackground=UI_COLORS["entry"],
            foreground=UI_COLORS["entry_text"],
            rowheight=25,
            borderwidth=0,
            font=("Segoe UI", 9),
        )
        style.configure(
            "Treeview.Heading",
            background=UI_COLORS["surface_light"],
            foreground=UI_COLORS["text"],
            borderwidth=0,
            font=("Segoe UI", 9, "bold"),
        )
        style.map("Treeview", background=[("selected", "#BDEBFF")], foreground=[("selected", "#10243A")])

        header = ttk.Frame(self)
        header.pack(fill="x", padx=9, pady=(7, 3))
        ttk.Label(header, text="Logicoin / LOGIC Control Center", style="Header.TLabel").pack(side="left")
        ttk.Label(header, textvariable=self.node_version_badge_var, style="Small.TLabel").pack(side="right", padx=6)

        controls = ttk.Frame(self)
        controls.pack(fill="x", padx=9, pady=3)
        ttk.Label(controls, text="Node:").pack(side="left")
        ttk.Entry(controls, textvariable=self.node_url_var, width=27).pack(side="left", padx=(4, 5))
        ttk.Button(controls, text="Speichern", command=self.save_current_settings).pack(side="left", padx=2)
        ttk.Button(controls, text="▶ MINING STARTEN", command=self.open_easy_mining_dialog, style="Accent.TButton").pack(side="left", padx=2)
        ttk.Button(controls, text="■ MINING STOPPEN", command=self.stop_easy_mining, style="Stop.TButton").pack(side="left", padx=2)
        ttk.Button(controls, text="Node ▶", command=self.start_node).pack(side="left", padx=2)
        ttk.Button(controls, text="Node ■", command=self.stop_node).pack(side="left", padx=2)
        ttk.Button(controls, text="↻", width=3, command=self.refresh_all_async).pack(side="left", padx=2)
        ttk.Button(controls, text="Explorer", command=self.open_explorer).pack(side="left", padx=2)
        ttk.Label(controls, text="Profil:").pack(side="left", padx=(10, 3))
        ttk.Combobox(controls, textvariable=self.mining_power_profile_var, width=11, state="readonly", values=["eco", "medium", "high", "ultra", "custom"]).pack(side="left", padx=2)
        ttk.Button(controls, text="Anwenden", command=self.apply_power_profile).pack(side="left", padx=2)
        ttk.Checkbutton(controls, text="Auto", variable=self.auto_refresh_var).pack(side="left", padx=6)

        ttk.Label(self, textvariable=self.status_var, style="Small.TLabel").pack(fill="x", padx=10, pady=(1, 4))

        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill="both", expand=True, padx=9, pady=(2, 8))
        tabs = [
            ("Dashboard", "_build_dashboard_tab"),
            ("Mining", "_build_mining_workspace_tab"),
            ("Wallet", "_build_wallet_tab"),
            ("Netzwerk", "_build_network_tab"),
            ("Blockchain", "_build_blockchain_workspace_tab"),
            ("Erweitert", "_build_advanced_workspace_tab"),
        ]
        self.tabs = {}
        for label, method in tabs:
            frame = ttk.Frame(self.notebook)
            self.tabs[label] = frame
            self.notebook.add(frame, text=label)
            getattr(self, method)(frame)

    def _make_subnotebook(self, parent: ttk.Frame) -> ttk.Notebook:
        notebook = ttk.Notebook(parent)
        notebook.pack(fill="both", expand=True, padx=3, pady=3)
        return notebook

    def _add_subtab(self, notebook: ttk.Notebook, label: str, builder: Any) -> ttk.Frame:
        frame = ttk.Frame(notebook)
        notebook.add(frame, text=label)
        builder(frame)
        return frame

    def _build_mining_workspace_tab(self, tab: ttk.Frame) -> None:
        notebook = self._make_subnotebook(tab)
        self._add_subtab(notebook, "Einfach starten", self._build_easy_mining_tab)
        self._add_subtab(notebook, "GPU & Multi-GPU", self._build_multi_gpu_tab)
        self._add_subtab(notebook, "Leistungsprofil", self._build_mining_tab)
        self._add_subtab(notebook, "Live-Stats", self._build_gpu_stats_tab)
        self._add_subtab(notebook, "Benchmark", self._build_benchmark_tab)
        self._add_subtab(notebook, "Profile", self._build_miner_profiles_tab)
        self._add_subtab(notebook, "Erweitert", self._build_miner_manager_tab)
        self._add_subtab(notebook, "GPU-Optimierung", self._build_gpu_optimization_tab)
        self._add_subtab(notebook, "Hardware", self._build_hardware_tab)

    def _build_easy_mining_tab(self, tab: ttk.Frame) -> None:
        box = ttk.LabelFrame(tab, text="LOGIC Mining für Einsteiger")
        box.pack(fill="x", padx=12, pady=12)

        ttk.Label(
            box,
            text="Wähle nur aus, welche Hardware minen soll. Node, Wallet-Adresse und Miner werden automatisch vorbereitet.",
            wraplength=820,
        ).pack(anchor="w", padx=12, pady=(10, 8))

        modes = ttk.Frame(box)
        modes.pack(fill="x", padx=10, pady=5)
        ttk.Radiobutton(modes, text="Nur GPU", variable=self.easy_mining_mode_var, value="gpu").pack(side="left", padx=10)
        ttk.Radiobutton(modes, text="Nur CPU", variable=self.easy_mining_mode_var, value="cpu").pack(side="left", padx=10)
        ttk.Radiobutton(modes, text="GPU + CPU", variable=self.easy_mining_mode_var, value="gpu_cpu").pack(side="left", padx=10)
        ttk.Label(modes, text="Interne Miner laufen ohne Terminalfenster.", style="Small.TLabel").pack(side="left", padx=18)

        actions = ttk.Frame(box)
        actions.pack(fill="x", padx=10, pady=12)
        ttk.Button(actions, text="▶ MINING STARTEN", command=self.start_easy_mining, style="Accent.TButton").pack(side="left", padx=6)
        ttk.Button(actions, text="■ MINING STOPPEN", command=self.stop_easy_mining, style="Stop.TButton").pack(side="left", padx=6)
        ttk.Button(actions, text="Leistungswerte anwenden", command=self.apply_custom_power_values).pack(side="left", padx=6)

        note = tk.Text(tab, height=14, bg="#22324D", fg="#F5F8FF", relief="flat")
        note.pack(fill="both", expand=True, padx=12, pady=(0, 12))
        note.insert(
            "1.0",
            "Accepted = gültiger Block angenommen.\n"
            "Stale = eine andere GPU oder Node war schneller; kein echter Fehler.\n"
            "Invalid = wirklich ungültiger Block und muss untersucht werden.\n"
        )
        note.configure(state="disabled")

    def _build_gpu_workspace_tab(self, tab: ttk.Frame) -> None:
        notebook = self._make_subnotebook(tab)
        self._add_subtab(notebook, "Multi-GPU", self._build_multi_gpu_tab)
        self._add_subtab(notebook, "Benchmark", self._build_benchmark_tab)
        self._add_subtab(notebook, "Live-Stats", self._build_gpu_stats_tab)
        self._add_subtab(notebook, "Optimierung", self._build_gpu_optimization_tab)
        self._add_subtab(notebook, "Hardware", self._build_hardware_tab)

    def _build_blockchain_workspace_tab(self, tab: ttk.Frame) -> None:
        notebook = self._make_subnotebook(tab)
        self._add_subtab(notebook, "Übersicht", self._build_overview_tab)
        self._add_subtab(notebook, "Blöcke", self._build_blocks_tab)
        self._add_subtab(notebook, "Mempool", self._build_mempool_tab)
        self._add_subtab(notebook, "Balances", self._build_balances_tab)

    def _build_advanced_workspace_tab(self, tab: ttk.Frame) -> None:
        notebook = self._make_subnotebook(tab)
        self._add_subtab(notebook, "Steuerung", self._build_control_tab)
        self._add_subtab(notebook, "Logs", self._build_logs_tab)
        self._add_subtab(
            notebook,
            "Release & Diagnose",
            self._build_release_tools_tab,
        )

    def _build_release_tools_tab(self, tab: ttk.Frame) -> None:
        header = ttk.LabelFrame(
            tab,
            text="Logicoin Public Testnet RC1",
        )
        header.pack(fill="x", padx=8, pady=8)

        ttk.Label(
            header,
            text=(
                f"{NETWORK_NAME} | Netzwerk-ID: {NETWORK_ID} | "
                f"Release: {RELEASE_CHANNEL}"
            ),
        ).pack(anchor="w", padx=8, pady=6)

        actions = ttk.Frame(tab)
        actions.pack(fill="x", padx=8, pady=6)

        ttk.Button(
            actions,
            text="Release-Readiness prüfen",
            command=self.run_release_readiness_ui,
        ).pack(side="left", padx=4)

        ttk.Button(
            actions,
            text="Diagnosepaket exportieren",
            command=self.export_diagnostics_ui,
        ).pack(side="left", padx=4)

        ttk.Button(
            actions,
            text="Wallet sichern",
            command=self.backup_wallet_ui,
        ).pack(side="left", padx=4)

        ttk.Button(
            actions,
            text="Public-Release-Ordner öffnen",
            command=self.open_release_folder_ui,
        ).pack(side="left", padx=4)

        ttk.Button(
            actions,
            text="Public-Network-JSON öffnen",
            command=self.open_public_network_json_ui,
        ).pack(side="left", padx=4)

        self.release_tools_text = tk.Text(
            tab,
            bg="#22324D",
            fg="#F5F8FF",
            relief="flat",
        )
        self.release_tools_text.pack(
            fill="both",
            expand=True,
            padx=8,
            pady=8,
        )
        self._set_text(
            self.release_tools_text,
            (
                "Noch nicht geprüft.\n\n"
                "Hinweis: Testcoins besitzen keinen garantierten Wert. "
                "Ein öffentlicher Seed-Node muss vor einer komfortablen "
                "Verteilung konfiguriert werden."
            ),
        )

    def metric(self, parent: tk.Widget, label: str) -> tk.StringVar:
        var = tk.StringVar(value="-")
        card = RoundedMetricCard(
            parent,
            title=label,
            value_var=var,
            accent=UI_COLORS["cyan"],
            height=112,
        )
        card.pack(side="left", fill="both", expand=True, padx=6, pady=6)
        return var

    def big_card(
        self,
        parent: tk.Widget,
        title: str,
        value_var: tk.StringVar,
        subtitle: str = "",
    ) -> RoundedMetricCard:
        accents = [
            UI_COLORS["green"],
            UI_COLORS["cyan"],
            UI_COLORS["purple"],
            UI_COLORS["orange"],
        ]
        accent = accents[self._card_accent_index % len(accents)]
        self._card_accent_index += 1

        card = RoundedMetricCard(
            parent,
            title=title,
            value_var=value_var,
            subtitle=subtitle,
            accent=accent,
            height=124,
        )
        card.pack(side="left", fill="both", expand=True, padx=7, pady=7)
        return card


    def small_card(
        self,
        parent: tk.Widget,
        title: str,
        value_var: tk.StringVar,
        subtitle: str = "",
        accent: str | None = None,
    ) -> RoundedMetricCard:
        accents = [
            UI_COLORS["cyan"],
            UI_COLORS["green"],
            UI_COLORS["purple"],
            UI_COLORS["orange"],
        ]
        if accent is None:
            accent = accents[self._card_accent_index % len(accents)]
            self._card_accent_index += 1

        card = RoundedMetricCard(
            parent,
            title=title,
            value_var=value_var,
            subtitle=subtitle,
            accent=accent,
            height=112,
        )
        card.pack(side="left", fill="both", expand=True, padx=6, pady=6)
        return card



    def _build_dashboard_tab(self, tab: ttk.Frame) -> None:
        outer = tk.Frame(tab, bg=UI_COLORS["window"])
        outer.pack(fill="both", expand=True)

        canvas = tk.Canvas(
            outer,
            bg=UI_COLORS["window"],
            highlightthickness=0,
            bd=0,
        )
        scrollbar = ttk.Scrollbar(outer, orient="vertical", command=canvas.yview)
        content = ttk.Frame(canvas)

        window_id = canvas.create_window((0, 0), window=content, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)

        def update_scrollregion(_event: object = None) -> None:
            canvas.configure(scrollregion=canvas.bbox("all"))

        def fit_width(event: object) -> None:
            canvas.itemconfigure(window_id, width=int(getattr(event, "width", 0)))

        def mousewheel(event: object) -> None:
            delta = int(getattr(event, "delta", 0))
            if delta:
                canvas.yview_scroll(int(-delta / 120), "units")

        content.bind("<Configure>", update_scrollregion)
        canvas.bind("<Configure>", fit_width)
        canvas.bind("<Enter>", lambda _event: canvas.bind_all("<MouseWheel>", mousewheel))
        canvas.bind("<Leave>", lambda _event: canvas.unbind_all("<MouseWheel>"))

        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        hero = tk.Frame(
            content,
            bg=UI_COLORS["surface"],
            bd=0,
            highlightbackground=UI_COLORS["border"],
            highlightthickness=1,
        )
        hero.pack(fill="x", padx=4, pady=(4, 6))

        title_row = tk.Frame(hero, bg=UI_COLORS["surface"])
        title_row.pack(fill="x", padx=12, pady=(8, 3))
        tk.Label(
            title_row,
            text="LOGIC – Einfaches Mining",
            bg=UI_COLORS["surface"],
            fg=UI_COLORS["text"],
            font=("Segoe UI", 15, "bold"),
        ).pack(side="left")
        tk.Label(
            title_row,
            text="Starten, Hardware wählen, fertig.",
            bg=UI_COLORS["surface"],
            fg=UI_COLORS["muted"],
            font=("Segoe UI", 9),
        ).pack(side="left", padx=12)

        quick = ttk.Frame(hero)
        quick.pack(fill="x", padx=8, pady=(0, 8))
        ttk.Button(quick, text="▶ MINING STARTEN", command=self.open_easy_mining_dialog, style="Accent.TButton").pack(side="left", padx=4)
        ttk.Button(quick, text="■ MINING STOPPEN", command=self.stop_easy_mining, style="Stop.TButton").pack(side="left", padx=4)
        ttk.Button(quick, text="Mining", command=lambda: self.notebook.select(self.tabs["Mining"])).pack(side="left", padx=4)
        ttk.Button(quick, text="Wallet", command=lambda: self.notebook.select(self.tabs["Wallet"])).pack(side="left", padx=4)
        ttk.Button(quick, text="Netzwerk", command=lambda: self.notebook.select(self.tabs["Netzwerk"])).pack(side="left", padx=4)

        row1 = ttk.Frame(content)
        row1.pack(fill="x")
        self.dashboard_node_var = self.big_card(row1, "Node", self.dash_node_value_var, "automatisch verwaltet")
        self.dashboard_cpu_miner_var = self.big_card(row1, "CPU-Mining", self.dash_cpu_miner_value_var, "Status • Ziel • Hashrate")
        self.dashboard_gpu_miner_var = self.big_card(row1, "GPU-Mining", self.dash_gpu_miner_value_var, "aktive GPUs")
        self.dashboard_gpu_hash_var = self.big_card(row1, "GPU gesamt effektiv", self.dash_gpu_total_hashrate_var, "End-to-End inklusive Node & Jobwechsel")

        row2 = ttk.Frame(content)
        row2.pack(fill="x")
        self.dashboard_wallet_var = self.big_card(row2, "Wallet-Balance", self.dash_wallet_balance_var, "alle 10 Sekunden")
        self.dashboard_wallet_value_card = self.big_card(row2, "Testwert", self.dash_wallet_value_var, "manueller EUR/USD-Testkurs")
        self.dashboard_mined_value_card = self.big_card(row2, "Gemint", self.dash_mined_value_var, "Rewards + Fees")
        self.dashboard_height_var = self.big_card(row2, "Blockhöhe", self.dash_height_value_var, "lokale Chain")

        earnings = tk.Frame(
            content,
            bg=UI_COLORS["surface"],
            highlightbackground=UI_COLORS["border"],
            highlightthickness=1,
        )
        earnings.pack(fill="x", padx=4, pady=(4, 5))

        head = tk.Frame(earnings, bg=UI_COLORS["surface"])
        head.pack(fill="x", padx=10, pady=(6, 1))
        tk.Label(
            head,
            text="Ertragsprognose & Blockchance",
            bg=UI_COLORS["surface"],
            fg=UI_COLORS["text"],
            font=("Segoe UI", 10, "bold"),
        ).pack(side="left")
        tk.Label(
            head,
            textvariable=self.earnings_summary_var,
            bg=UI_COLORS["surface"],
            fg=UI_COLORS["muted"],
            font=("Segoe UI", 8),
        ).pack(side="left", padx=10)

        cards = ttk.Frame(earnings)
        cards.pack(fill="x", padx=4, pady=(0, 4))
        self.earnings_hour_card = self.small_card(cards, "1 Stunde", self.earnings_hour_var, "", UI_COLORS["cyan"])
        self.earnings_day_card = self.small_card(cards, "1 Tag", self.earnings_day_var, "", UI_COLORS["green"])
        self.earnings_month_card = self.small_card(cards, "30 Tage", self.earnings_month_var, "", UI_COLORS["purple"])
        self.earnings_year_card = self.small_card(cards, "365 Tage", self.earnings_year_var, "", UI_COLORS["orange"])

        profile = ttk.LabelFrame(content, text="Leistung einstellen")
        profile.pack(fill="x", padx=4, pady=(3, 4))
        profile.columnconfigure(9, weight=1)

        ttk.Label(profile, text="Profil").grid(row=0, column=0, sticky="w", padx=6, pady=5)
        ttk.Combobox(
            profile,
            textvariable=self.mining_power_profile_var,
            width=10,
            state="readonly",
            values=["eco", "medium", "high", "ultra", "custom"],
        ).grid(row=0, column=1, sticky="w", padx=4, pady=5)

        ttk.Label(profile, text="CPU").grid(row=0, column=2, sticky="w", padx=(10, 3))
        ttk.Spinbox(profile, from_=5, to=100, textvariable=self.cpu_usage_percent_var, width=5, command=self.on_manual_power_changed).grid(row=0, column=3, padx=3)
        ttk.Label(profile, text="%").grid(row=0, column=4)

        ttk.Label(profile, text="GPU").grid(row=0, column=5, sticky="w", padx=(10, 3))
        ttk.Spinbox(profile, from_=5, to=100, textvariable=self.gpu_usage_percent_var, width=5, command=self.on_manual_power_changed).grid(row=0, column=6, padx=3)
        ttk.Label(profile, text="%").grid(row=0, column=7)

        ttk.Button(profile, text="Anwenden", command=self.apply_custom_power_values, style="Accent.TButton").grid(row=0, column=8, padx=8, pady=5)

        ttk.Label(profile, text="CPU-Regler").grid(row=1, column=0, sticky="w", padx=6, pady=(1, 6))
        ttk.Scale(
            profile,
            from_=5,
            to=100,
            variable=self.cpu_usage_percent_var,
            orient="horizontal",
            command=lambda *_args: self.on_manual_power_changed(),
        ).grid(row=1, column=1, columnspan=4, sticky="we", padx=4, pady=(1, 6))

        ttk.Label(profile, text="GPU-Regler").grid(row=1, column=5, sticky="w", padx=(10, 3), pady=(1, 6))
        ttk.Scale(
            profile,
            from_=5,
            to=100,
            variable=self.gpu_usage_percent_var,
            orient="horizontal",
            command=lambda *_args: self.on_manual_power_changed(),
        ).grid(row=1, column=6, columnspan=3, sticky="we", padx=4, pady=(1, 6))

        rates = ttk.LabelFrame(content, text="LOGIC Testkurs")
        rates.pack(fill="x", padx=4, pady=(2, 4))
        ttk.Label(rates, text="1 LOGIC =").pack(side="left", padx=(8, 3), pady=5)
        ttk.Entry(rates, textvariable=self.logic_test_rate_eur_var, width=9).pack(side="left", padx=3)
        ttk.Label(rates, text="EUR").pack(side="left", padx=(0, 10))
        ttk.Entry(rates, textvariable=self.logic_test_rate_usd_var, width=9).pack(side="left", padx=3)
        ttk.Label(rates, text="USD").pack(side="left", padx=(0, 10))
        ttk.Button(rates, text="Speichern", command=self.apply_test_rates).pack(side="left", padx=5)

        gpu_box = ttk.LabelFrame(content, text="GPU-Übersicht")
        gpu_box.pack(fill="both", expand=True, padx=4, pady=(0, 5))
        self.dashboard_gpu_text = tk.Text(
            gpu_box,
            height=5,
            bg=UI_COLORS["surface_alt"],
            fg=UI_COLORS["text"],
            relief="flat",
            highlightthickness=0,
        )
        self.dashboard_gpu_text.pack(fill="both", expand=True, padx=6, pady=6)
        self._set_text(self.dashboard_gpu_text, "Noch keine GPU-Miner-Statistiken.")

    def _build_control_tab(self, tab: ttk.Frame) -> None:
        row = ttk.Frame(tab)
        row.pack(fill="x", pady=8)
        self.node_state_var = self.metric(row, "Node")
        self.cpu_miner_state_var = self.metric(row, "CPU-Miner")
        self.external_miner_state_var = self.metric(row, "Externer Miner")
        self.app_mode_var = self.metric(row, "Modus")

        actions = ttk.Frame(tab)
        actions.pack(fill="x", pady=12)
        ttk.Button(actions, text="Alles starten", command=self.start_all, style="Accent.TButton").pack(side="left", padx=5)
        ttk.Button(actions, text="Alles stoppen", command=self.stop_all).pack(side="left", padx=5)
        ttk.Button(actions, text="Node starten", command=self.start_node).pack(side="left", padx=5)
        ttk.Button(actions, text="Node stoppen", command=self.stop_node).pack(side="left", padx=5)
        ttk.Button(actions, text="CPU-Miner starten", command=self.start_cpu_miner).pack(side="left", padx=5)
        ttk.Button(actions, text="CPU-Miner stoppen", command=self.stop_cpu_miner).pack(side="left", padx=5)
        ttk.Button(actions, text="Internen CPU-Miner starten", command=self.open_cpu_miner_console).pack(side="left", padx=5)
        ttk.Button(actions, text="Wallet aktualisieren", command=self.refresh_wallet).pack(side="left", padx=5)
        ttk.Button(actions, text="Config öffnen", command=self.start_config_editor).pack(side="left", padx=5)

        ttk.Checkbutton(tab, text="Node automatisch mit App starten", variable=self.auto_start_node_var, command=self.save_current_settings).pack(anchor="w", padx=6, pady=8)

        txt = tk.Text(tab, height=16, bg="#22324D", fg="#F5F8FF", relief="flat")
        txt.pack(fill="both", expand=True, pady=10)
        txt.insert("1.0",
            "Das ist jetzt das Hauptprogramm für dein lokales LOGIC-Testnet.\n\n"
            "Integriert:\n"
            "- Node starten/stoppen\n"
            "- LOGIC CPU-Miner starten/stoppen\n"
            "- Wallet erstellen/verwenden\n"
            "- Balance anzeigen\n"
            "- LOGIC senden\n"
            "- Mempool/Blocks/Balances anzeigen\n"
            "- CPU und NVIDIA-GPUs überwachen\n"
            "- CPU-Benchmark\n"
            "- externer GPU-Miner-Start vorbereitet\n\n"
            "GPU-Mining für LOGIC selbst braucht später einen eigenen CUDA/OpenCL-Miner.\nv0.9.6 speichert dafür schon den Optimierungsmodus: Auto, Legacy, Custom oder Manual/Afterburner.\n"
        )
        txt.configure(state="disabled")

    def _build_wallet_tab(self, tab: ttk.Frame) -> None:
        top = ttk.Frame(tab)
        top.pack(fill="x", pady=8)
        ttk.Button(top, text="Neue Wallet", command=self.create_new_wallet).pack(side="left", padx=4)
        ttk.Button(top, text="Mining-Testwallet benutzen", command=self.use_mining_wallet).pack(side="left", padx=4)
        ttk.Button(top, text="Wallet laden/aktualisieren", command=self.refresh_wallet).pack(side="left", padx=4)
        ttk.Button(top, text="Balance anzeigen", command=self.refresh_wallet_balance).pack(side="left", padx=4)
        ttk.Button(top, text="Mempool aktualisieren", command=self.refresh_all_async).pack(side="left", padx=4)
        ttk.Button(top, text="Wallet sichern", command=self.backup_wallet_ui).pack(side="left", padx=4)
        ttk.Button(top, text="Wallet wiederherstellen", command=self.restore_wallet_ui).pack(side="left", padx=4)

        row = ttk.Frame(tab)
        row.pack(fill="x", pady=6)
        ttk.Label(row, text="Aktuelle Wallet:").pack(side="left")
        ttk.Entry(row, textvariable=self.wallet_address_var, width=70).pack(side="left", padx=8)

        self.wallet_info_text = tk.Text(tab, height=9, bg="#22324D", fg="#F5F8FF", relief="flat")
        self.wallet_info_text.pack(fill="x", pady=8)

        send = ttk.LabelFrame(tab, text="LOGIC senden")
        send.pack(fill="x", pady=8)

        ttk.Label(send, text="Empfänger:").grid(row=0, column=0, padx=6, pady=5, sticky="w")
        ttk.Entry(send, textvariable=self.send_to_var, width=70).grid(row=0, column=1, padx=6, pady=5, sticky="w")

        ttk.Label(send, text="Amount:").grid(row=1, column=0, padx=6, pady=5, sticky="w")
        ttk.Entry(send, textvariable=self.send_amount_var, width=18).grid(row=1, column=1, padx=6, pady=5, sticky="w")

        ttk.Label(send, text="Fee:").grid(row=2, column=0, padx=6, pady=5, sticky="w")
        ttk.Entry(send, textvariable=self.send_fee_var, width=18).grid(row=2, column=1, padx=6, pady=5, sticky="w")

        ttk.Label(send, text="Memo:").grid(row=3, column=0, padx=6, pady=5, sticky="w")
        ttk.Entry(send, textvariable=self.send_memo_var, width=70).grid(row=3, column=1, padx=6, pady=5, sticky="w")

        ttk.Button(send, text="Transaktion senden", command=self.send_logic_transaction).grid(row=4, column=1, padx=6, pady=8, sticky="w")

    def _build_miner_profiles_tab(self, tab: ttk.Frame) -> None:
        left = ttk.Frame(tab)
        left.pack(side="left", fill="y", padx=(4, 10), pady=6)

        ttk.Label(left, text="Gespeicherte Profile").pack(anchor="w", pady=(0, 4))

        self.miner_profile_listbox = tk.Listbox(
            left,
            height=18,
            width=36,
            bg="#22324D",
            fg="#F5F8FF",
            selectbackground="#1f7a4d",
            selectforeground="#ffffff",
            relief="flat",
            activestyle="none",
        )
        self.miner_profile_listbox.pack(fill="y", expand=False)
        self.miner_profile_listbox.bind("<<ListboxSelect>>", lambda event: self.on_miner_profile_select())

        left_buttons = ttk.Frame(left)
        left_buttons.pack(fill="x", pady=8)
        ttk.Button(left_buttons, text="Neu aus aktuellen Einstellungen", command=self.save_current_as_new_miner_profile).pack(fill="x", pady=2)
        ttk.Button(left_buttons, text="Überschreiben", command=self.overwrite_selected_miner_profile).pack(fill="x", pady=2)
        ttk.Button(left_buttons, text="Duplizieren", command=self.duplicate_selected_miner_profile).pack(fill="x", pady=2)
        ttk.Button(left_buttons, text="Löschen", command=self.delete_selected_miner_profile).pack(fill="x", pady=2)

        right = ttk.Frame(tab)
        right.pack(side="left", fill="both", expand=True, padx=4, pady=6)

        top = ttk.Frame(right)
        top.pack(fill="x", pady=(0, 8))

        ttk.Button(top, text="Profil anwenden", command=self.apply_selected_miner_profile, style="Accent.TButton").pack(side="left", padx=4)
        ttk.Button(top, text="Profil starten", command=self.start_selected_miner_profile).pack(side="left", padx=4)
        ttk.Button(top, text="Profile neu laden", command=self.reload_miner_profiles).pack(side="left", padx=4)
        ttk.Button(top, text="Profil-Datei öffnen/Ordner", command=self.open_logicoin_folder).pack(side="left", padx=4)

        self.miner_profile_detail_text = tk.Text(right, height=22, bg="#22324D", fg="#F5F8FF", relief="flat")
        self.miner_profile_detail_text.pack(fill="both", expand=True)

        hint = tk.Text(right, height=7, bg="#1C2942", fg="#B9C6DA", relief="flat")
        hint.pack(fill="x", pady=(8, 0))
        hint.insert(
            "1.0",
            "Hinweis:\n"
            "- Profile speichern nur App-/Miner-Einstellungen, nicht den Coin selbst.\n"
            "- GTX/RTX-Profile sind Platzhalter für den kommenden GPU-Miner.\n"
            "- Externe Profile starten nur, wenn ein Miner-Pfad gesetzt ist.\n"
            "- Interne LOGIC-CPU-Profile nutzen den mitgelieferten LogicoinCpuMiner.\n"
        )
        hint.configure(state="disabled")

        self.refresh_miner_profile_list()

    def get_selected_miner_profile(self) -> dict[str, Any] | None:
        if not hasattr(self, "miner_profile_listbox"):
            name = self.selected_miner_profile_var.get()
        else:
            selection = self.miner_profile_listbox.curselection()
            if selection:
                name = self.miner_profile_listbox.get(selection[0])
                self.selected_miner_profile_var.set(name)
            else:
                name = self.selected_miner_profile_var.get()

        for profile in self.miner_profiles:
            if profile.get("name") == name:
                return profile
        return self.miner_profiles[0] if self.miner_profiles else None

    def refresh_miner_profile_list(self, select_name: str | None = None) -> None:
        if not hasattr(self, "miner_profile_listbox"):
            return

        current = select_name or self.selected_miner_profile_var.get()
        self.miner_profile_listbox.delete(0, "end")

        selected_index = 0
        for index, profile in enumerate(self.miner_profiles):
            name = profile.get("name", f"Profil {index + 1}")
            self.miner_profile_listbox.insert("end", name)
            if name == current:
                selected_index = index

        if self.miner_profiles:
            self.miner_profile_listbox.selection_clear(0, "end")
            self.miner_profile_listbox.selection_set(selected_index)
            self.miner_profile_listbox.activate(selected_index)
            self.selected_miner_profile_var.set(self.miner_profiles[selected_index].get("name", ""))

        self.refresh_selected_miner_profile_details()

    def refresh_selected_miner_profile_details(self) -> None:
        if not hasattr(self, "miner_profile_detail_text"):
            return
        profile = self.get_selected_miner_profile()
        self._set_text(self.miner_profile_detail_text, profile_summary_text(profile or {}))

    def on_miner_profile_select(self) -> None:
        profile = self.get_selected_miner_profile()
        if profile:
            self.selected_miner_profile_var.set(profile.get("name", ""))
        self.refresh_selected_miner_profile_details()

    def capture_current_miner_profile(self, name: str) -> dict[str, Any]:
        profile_type = "internal_cpu" if self.coin_algo_var.get() == "LOGIC / LogicHash CPU" else "external"
        return {
            "name": name,
            "type": profile_type,
            "coin_algorithm": self.coin_algo_var.get(),
            "miner_address": self.miner_address_var.get().strip() or DEFAULT_MINER_ADDRESS,
            "mining_power_profile": self.mining_power_profile_var.get(),
            "cpu_usage_percent": int(float(self.cpu_usage_percent_var.get())),
            "gpu_usage_percent": int(float(self.gpu_usage_percent_var.get())),
            "internal_cpu_miner_mode": self.internal_cpu_miner_mode_var.get(),
            "mining_optimization_mode": self.optimization_mode_var.get(),
            "external_miner_path": self.external_path_var.get(),
            "external_miner_args": self.external_args_var.get(),
            "manual_miner_visible_console": self.manual_miner_console_var.get(),
            "gpu_custom_intensity": int(self.gpu_custom_intensity_var.get()),
            "gpu_custom_worksize": int(self.gpu_custom_worksize_var.get()),
            "note": "Vom Nutzer gespeichertes Miner-Profil.",
        }

    def apply_miner_profile_to_app(self, profile: dict[str, Any], save: bool = True) -> None:
        if not profile:
            return

        self.coin_algo_var.set(profile.get("coin_algorithm", "LOGIC / LogicHash CPU"))
        self.miner_address_var.set(profile.get("miner_address", DEFAULT_MINER_ADDRESS))
        self.mining_power_profile_var.set(profile.get("mining_power_profile", "medium"))
        self.cpu_usage_percent_var.set(int(profile.get("cpu_usage_percent", 50)))
        self.gpu_usage_percent_var.set(int(profile.get("gpu_usage_percent", 60)))
        self.internal_cpu_miner_mode_var.set(profile.get("internal_cpu_miner_mode", "visible"))
        self.optimization_mode_var.set(profile.get("mining_optimization_mode", "manual_afterburner"))
        self.external_path_var.set(profile.get("external_miner_path", ""))
        self.external_args_var.set(profile.get("external_miner_args", ""))
        self.manual_miner_console_var.set(bool(profile.get("manual_miner_visible_console", True)))
        self.gpu_custom_intensity_var.set(int(profile.get("gpu_custom_intensity", 55)))
        self.gpu_custom_worksize_var.set(int(profile.get("gpu_custom_worksize", 128)))

        self.refresh_profile_preview()
        self.update_process_state()

        if save:
            self.save_current_settings()

    def apply_selected_miner_profile(self) -> None:
        profile = self.get_selected_miner_profile()
        if not profile:
            messagebox.showinfo("Miner-Profil", "Kein Profil ausgewählt.")
            return

        self.apply_miner_profile_to_app(profile, save=True)
        self.status_var.set(f"Profil angewendet: {profile.get('name')}")

    def start_selected_miner_profile(self) -> None:
        profile = self.get_selected_miner_profile()
        if not profile:
            messagebox.showinfo("Miner-Profil", "Kein Profil ausgewählt.")
            return

        self.apply_miner_profile_to_app(profile, save=True)
        profile_type = profile.get("type", "internal_cpu")

        if profile_type == "internal_cpu":
            if self.node_process is None or self.node_process.poll() is not None:
                self.start_node()
            self.after(1200, self.start_cpu_miner)
            self.status_var.set(f"Starte internes LOGIC-Profil: {profile.get('name')}")
            return

        path = self.external_path_var.get().strip()
        if not path:
            messagebox.showinfo(
                "Externer Miner",
                "Dieses Profil ist für einen externen/GPU-Miner vorbereitet, aber es ist noch kein Miner-Pfad gesetzt.\n\n"
                "Sobald wir den LOGIC-GPU-Miner bauen, kannst du hier die .exe eintragen."
            )
            return

        if self.manual_miner_console_var.get():
            self.open_external_miner_console()
        else:
            self.start_external_miner()

        self.status_var.set(f"Starte externes Profil: {profile.get('name')}")

    def save_current_as_new_miner_profile(self) -> None:
        name = simpledialog.askstring("Neues Miner-Profil", "Profilname:", parent=self)
        if not name:
            return

        if any(p.get("name") == name for p in self.miner_profiles):
            messagebox.showerror("Miner-Profil", "Ein Profil mit diesem Namen existiert bereits.")
            return

        profile = self.capture_current_miner_profile(name)
        self.miner_profiles.append(profile)
        save_miner_profiles(self.miner_profiles)
        self.refresh_miner_profile_list(select_name=name)
        self.status_var.set(f"Neues Profil gespeichert: {name}")

    def overwrite_selected_miner_profile(self) -> None:
        old_profile = self.get_selected_miner_profile()
        if not old_profile:
            return

        name = old_profile.get("name")
        if not messagebox.askyesno("Profil überschreiben", f"Profil '{name}' mit aktuellen Einstellungen überschreiben?"):
            return

        new_profile = self.capture_current_miner_profile(name)
        self.miner_profiles = [new_profile if p.get("name") == name else p for p in self.miner_profiles]
        save_miner_profiles(self.miner_profiles)
        self.refresh_miner_profile_list(select_name=name)
        self.status_var.set(f"Profil überschrieben: {name}")

    def duplicate_selected_miner_profile(self) -> None:
        profile = self.get_selected_miner_profile()
        if not profile:
            return

        default_name = profile.get("name", "Profil") + " Kopie"
        name = simpledialog.askstring("Profil duplizieren", "Neuer Profilname:", initialvalue=default_name, parent=self)
        if not name:
            return

        if any(p.get("name") == name for p in self.miner_profiles):
            messagebox.showerror("Miner-Profil", "Ein Profil mit diesem Namen existiert bereits.")
            return

        new_profile = dict(profile)
        new_profile["name"] = name
        self.miner_profiles.append(new_profile)
        save_miner_profiles(self.miner_profiles)
        self.refresh_miner_profile_list(select_name=name)
        self.status_var.set(f"Profil dupliziert: {name}")

    def delete_selected_miner_profile(self) -> None:
        profile = self.get_selected_miner_profile()
        if not profile:
            return

        name = profile.get("name")
        if not messagebox.askyesno("Profil löschen", f"Profil '{name}' wirklich löschen?"):
            return

        self.miner_profiles = [p for p in self.miner_profiles if p.get("name") != name]
        if not self.miner_profiles:
            self.miner_profiles = default_miner_profiles()
        save_miner_profiles(self.miner_profiles)
        self.refresh_miner_profile_list()
        self.status_var.set(f"Profil gelöscht: {name}")

    def reload_miner_profiles(self) -> None:
        self.miner_profiles = load_miner_profiles()
        self.refresh_miner_profile_list()
        self.status_var.set("Miner-Profile neu geladen.")


    def _build_mining_tab(self, tab: ttk.Frame) -> None:
        top = ttk.Frame(tab)
        top.pack(fill="x", pady=8)
        ttk.Label(top, text="Miner-Adresse:").grid(row=0, column=0, sticky="w", padx=5, pady=5)
        ttk.Entry(top, textvariable=self.miner_address_var, width=60).grid(row=0, column=1, sticky="w", padx=5, pady=5)
        ttk.Button(top, text="Wallet-Adresse übernehmen", command=self.use_wallet_as_miner_address).grid(row=0, column=2, padx=5)

        ttk.Label(top, text="Coin/Algorithmus:").grid(row=1, column=0, sticky="w", padx=5, pady=5)
        ttk.Combobox(top, textvariable=self.coin_algo_var, width=45, state="readonly", values=[
            "LOGIC / LogicHash CPU",
            "LOGIC / LogicHash GPU CUDA geplant",
            "External Miner / Custom",
            "PEARL / externer Miner Profil",
            "KawPow / externer Miner Profil",
            "Etchash / externer Miner Profil",
        ]).grid(row=1, column=1, sticky="w", padx=5, pady=5)

        ttk.Label(top, text="Leistungsprofil:").grid(row=2, column=0, sticky="w", padx=5, pady=5)
        ttk.Combobox(top, textvariable=self.mining_power_profile_var, width=18, state="readonly", values=["eco", "medium", "high", "ultra", "custom"]).grid(row=2, column=1, sticky="w", padx=5, pady=5)
        ttk.Button(top, text="Preset anwenden", command=self.apply_power_profile).grid(row=2, column=2, sticky="w", padx=5, pady=5)

        ttk.Label(top, text="CPU-Miner-Modus:").grid(row=5, column=0, sticky="w", padx=5, pady=5)
        ttk.Combobox(top, textvariable=self.internal_cpu_miner_mode_var, width=18, state="readonly", values=["visible", "background"]).grid(row=5, column=1, sticky="w", padx=5, pady=5)

        ttk.Label(top, text="CPU %:").grid(row=3, column=0, sticky="w", padx=5, pady=5)
        ttk.Spinbox(top, from_=5, to=100, textvariable=self.cpu_usage_percent_var, width=8, command=self.on_manual_power_changed).grid(row=3, column=1, sticky="w", padx=5, pady=5)

        ttk.Label(top, text="GPU %:").grid(row=4, column=0, sticky="w", padx=5, pady=5)
        ttk.Spinbox(top, from_=5, to=100, textvariable=self.gpu_usage_percent_var, width=8, command=self.on_manual_power_changed).grid(row=4, column=1, sticky="w", padx=5, pady=5)

        btns = ttk.Frame(tab)
        btns.pack(fill="x", pady=8)
        ttk.Button(btns, text="Einstellungen speichern", command=self.save_current_settings).pack(side="left", padx=5)
        ttk.Button(btns, text="LOGIC CPU-Miner starten", command=self.start_cpu_miner).pack(side="left", padx=5)
        ttk.Button(btns, text="LOGIC CPU-Miner stoppen", command=self.stop_cpu_miner).pack(side="left", padx=5)

        ext = ttk.LabelFrame(tab, text="Externer GPU-Miner / Custom Miner")
        ext.pack(fill="x", pady=12, padx=4)
        ttk.Label(ext, text="Miner .exe Pfad:").grid(row=0, column=0, sticky="w", padx=5, pady=5)
        ttk.Entry(ext, textvariable=self.external_path_var, width=80).grid(row=0, column=1, sticky="w", padx=5, pady=5)
        ttk.Button(ext, text="Durchsuchen", command=self.browse_external_miner).grid(row=0, column=2, sticky="w", padx=5, pady=5)
        ttk.Label(ext, text="Argumente:").grid(row=1, column=0, sticky="w", padx=5, pady=5)
        ttk.Entry(ext, textvariable=self.external_args_var, width=80).grid(row=1, column=1, sticky="w", padx=5, pady=5)
        ttk.Button(ext, text="Externen Miner starten", command=self.start_external_miner).grid(row=2, column=1, sticky="w", padx=5, pady=6)
        ttk.Button(ext, text="Externen Miner manuell öffnen", command=self.open_external_miner_console).grid(row=2, column=1, sticky="w", padx=170, pady=6)
        ttk.Button(ext, text="Externen Miner stoppen", command=self.stop_external_miner).grid(row=2, column=1, sticky="w", padx=390, pady=6)
        ttk.Checkbutton(ext, text="Manuelle Miner mit sichtbarem Fenster öffnen", variable=self.manual_miner_console_var, command=self.save_current_settings).grid(row=3, column=1, sticky="w", padx=5, pady=5)

        note = tk.Text(tab, height=8, bg="#22324D", fg="#F5F8FF", relief="flat")
        note.pack(fill="both", expand=True, pady=8)
        note.insert("1.0", "LOGIC-GPU-Mining v0.12.15.3 ist als Testminer vorbereitet: LogicoinGpuMiner.exe oder logicoin_gpu_miner.py + optional logicoin_cuda_worker.exe. Ohne CUDA-Worker nutzt Backend auto den CPU-Fallback.\n")
        note.configure(state="disabled")

    def _build_miner_manager_tab(self, tab: ttk.Frame) -> None:
        top = ttk.Frame(tab)
        top.pack(fill="x", pady=8)

        ttk.Button(top, text="Internen CPU-Miner starten", command=self.open_cpu_miner_console, style="Accent.TButton").pack(side="left", padx=5)
        ttk.Button(top, text="Externen Miner sichtbar öffnen", command=self.open_external_miner_console).pack(side="left", padx=5)
        ttk.Button(top, text="Externen Miner im Hintergrund starten", command=self.start_external_miner).pack(side="left", padx=5)
        ttk.Button(top, text="Externen Miner stoppen", command=self.stop_external_miner).pack(side="left", padx=5)
        ttk.Button(top, text="LOGIC GPU-Testminer", command=self.prepare_logicoin_gpu_miner_profile).pack(side="left", padx=5)
        ttk.Button(top, text="Logicoin-Ordner öffnen", command=self.open_logicoin_folder).pack(side="left", padx=5)

        config = ttk.LabelFrame(tab, text="Manueller Miner / Profil")
        config.pack(fill="x", padx=4, pady=10)

        ttk.Label(config, text="Miner .exe Pfad:").grid(row=0, column=0, sticky="w", padx=8, pady=6)
        ttk.Entry(config, textvariable=self.external_path_var, width=85).grid(row=0, column=1, sticky="w", padx=8, pady=6)
        ttk.Button(config, text="Durchsuchen", command=self.browse_external_miner).grid(row=0, column=2, sticky="w", padx=8, pady=6)

        ttk.Label(config, text="Argumente:").grid(row=1, column=0, sticky="w", padx=8, pady=6)
        ttk.Entry(config, textvariable=self.external_args_var, width=85).grid(row=1, column=1, sticky="w", padx=8, pady=6)

        ttk.Label(config, text="Leistungsprofil:").grid(row=2, column=0, sticky="w", padx=8, pady=6)
        ttk.Combobox(config, textvariable=self.mining_power_profile_var, width=18, state="readonly", values=["eco", "medium", "high", "ultra", "custom"]).grid(row=2, column=1, sticky="w", padx=8, pady=6)

        ttk.Label(config, text="Interner CPU-Miner-Modus:").grid(row=3, column=0, sticky="w", padx=8, pady=6)
        ttk.Combobox(config, textvariable=self.internal_cpu_miner_mode_var, width=18, state="readonly", values=["visible", "background"]).grid(row=3, column=1, sticky="w", padx=8, pady=6)
        ttk.Button(config, text="Modus speichern", command=self.save_current_settings).grid(row=3, column=2, sticky="w", padx=8, pady=6)

        ttk.Checkbutton(config, text="Externe Miner mit sichtbarem Fenster öffnen", variable=self.manual_miner_console_var, command=self.save_current_settings).grid(row=4, column=1, sticky="w", padx=8, pady=6)

        info = tk.Text(tab, height=18, bg="#22324D", fg="#F5F8FF", relief="flat")
        info.pack(fill="both", expand=True, padx=4, pady=8)
        info.insert("1.0",
            "Miner-Manager v0.12.15.3\\n\\n"
            "Hier kannst du Miner manuell öffnen, damit du sie später leichter bearbeiten/testen kannst.\\n\\n"
            "Wichtig:\\n"
            "- Internen CPU-Miner starten = neues Konsolenfenster mit LOGIC CPU-Miner\\n"
            "- LOGIC GPU-Testminer = LogicoinGpuMiner.exe / logicoin_gpu_miner.py mit auto/cuda/cpu-fallback Backend\\n"
            "- Externer Miner im Hintergrund starten = wie normaler App-gesteuerter Start\\n\\n"
            "Die App übergibt an externe Miner Umgebungswerte:\\n"
            "- LOGIC_MINING_POWER_PROFILE\\n"
            "- LOGIC_CPU_USAGE_PERCENT\\n"
            "- LOGIC_GPU_USAGE_PERCENT\\n"
            "- LOGIC_MINING_OPTIMIZATION_MODE\\n\\n"
            "Damit können wir die Miner später sauberer und weniger primitiv machen."
        )
        info.configure(state="disabled")

    def ensure_multi_gpu_state(self) -> None:
        if not hasattr(self, "gpu_miner_processes") or self.gpu_miner_processes is None:
            self.gpu_miner_processes = {}
        if not hasattr(self, "gpu_miner_log_handles") or self.gpu_miner_log_handles is None:
            self.gpu_miner_log_handles = {}
        if not hasattr(self, "gpu_miner_desired_devices"):
            self.gpu_miner_desired_devices = set()
        if not hasattr(self, "gpu_miner_visible_by_device"):
            self.gpu_miner_visible_by_device = {}
        if not hasattr(self, "gpu_miner_restart_attempts"):
            self.gpu_miner_restart_attempts = {}
        if not hasattr(self, "gpu_miner_next_restart"):
            self.gpu_miner_next_restart = {}
        if not hasattr(self, "gpu_miner_started_at"):
            self.gpu_miner_started_at = {}
        if not hasattr(self, "gpu_miner_last_exit"):
            self.gpu_miner_last_exit = {}

    def find_nvidia_smi_path(self) -> str | None:
        candidates: list[str] = []

        try:
            found = shutil.which("nvidia-smi")
            if found:
                candidates.append(found)
        except Exception:
            pass

        candidates.extend([
            r"C:\Windows\System32\nvidia-smi.exe",
            r"C:\Program Files\NVIDIA Corporation\NVSMI\nvidia-smi.exe",
        ])

        for candidate in candidates:
            try:
                if candidate and Path(candidate).exists():
                    return candidate
            except Exception:
                pass

        return None

    def detect_nvidia_gpus(self) -> list[dict[str, object]]:
        monitor = getattr(self, "nvml_monitor", None)

        if monitor is not None:
            gpus = monitor.snapshot(window_seconds=5.0)
            if gpus:
                self._last_nvml_gpus = gpus
                return gpus

        gpus = read_nvidia_gpus_nvml()
        if gpus:
            self._last_nvml_gpus = gpus
            return gpus

        return list(getattr(self, "_last_nvml_gpus", []))

    def format_hs_ui(self, value: object) -> str:
        try:
            v = float(value)
        except Exception:
            return "--"
        if v >= 1_000_000_000_000:
            return f"{v/1_000_000_000_000:.2f} TH/s"
        if v >= 1_000_000_000:
            return f"{v/1_000_000_000:.2f} GH/s"
        if v >= 1_000_000:
            return f"{v/1_000_000:.2f} MH/s"
        if v >= 1_000:
            return f"{v/1_000:.2f} KH/s"
        return f"{v:.2f} H/s"

    def read_gpu_stats_files(self) -> list[dict[str, object]]:
        candidates: list[Path] = []
        directories = [
            BASE_DIR,
            BASE_DIR / "dist",
            BASE_DIR.parent,
            BASE_DIR.parent / "dist",
        ]

        seen_paths: set[str] = set()

        for directory in directories:
            try:
                for path in directory.glob(
                    GPU_MINER_STATS_GLOB
                ):
                    key = str(
                        path.resolve()
                    ).lower()
                    if key in seen_paths:
                        continue
                    seen_paths.add(key)
                    candidates.append(path)
            except Exception:
                continue

        newest_by_device: dict[
            int,
            tuple[float, dict[str, object]],
        ] = {}

        for path in candidates:
            try:
                data = json.loads(
                    path.read_text(
                        encoding="utf-8"
                    )
                )

                if str(
                    data.get("version", "")
                ) != APP_VERSION:
                    continue

                device = int(
                    data.get("device", -1)
                )
                if device < 0:
                    continue

                modified = path.stat().st_mtime
                previous = newest_by_device.get(
                    device
                )

                if (
                    previous is None
                    or modified > previous[0]
                ):
                    data["_file"] = str(path)
                    newest_by_device[device] = (
                        modified,
                        data,
                    )
            except Exception:
                continue

        return [
            item[1]
            for _, item in sorted(
                newest_by_device.items()
            )
        ]

    def mining_env(self) -> dict[str, str]:
        """
        v0.12.15.3:
        Robuste Mining-Umgebung für interne/externe/GPU-Miner.

        Fix:
        Keine Abhängigkeit mehr von SETTINGS_DEFAULTS, weil diese Variable
        in der EXE/Control-Center-Klasse nicht garantiert existiert.
        """
        env = os.environ.copy()

        # Verhindert cp1252/charmap-Abstürze in versteckten PyInstaller-Minern.
        env["PYTHONUTF8"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONLEGACYWINDOWSSTDIO"] = "0"

        safe_defaults = {
            "mining_power_profile": "medium",
            "cpu_usage_percent": 50,
            "gpu_usage_percent": 70,
            "gpu_optimization_mode": "auto_safe",
            "gpu_temp_limit_c": 78,
        }

        def get_var(name: str, default: object) -> object:
            try:
                var = getattr(self, name)
                if hasattr(var, "get"):
                    value = var.get()
                else:
                    value = var
                if value is None or value == "":
                    return default
                return value
            except Exception:
                return default

        power_profile = str(get_var("mining_power_profile_var", safe_defaults["mining_power_profile"]))
        cpu_percent = str(get_var("cpu_usage_percent_var", safe_defaults["cpu_usage_percent"]))
        gpu_percent = str(get_var("gpu_usage_percent_var", safe_defaults["gpu_usage_percent"]))
        optimization = str(get_var("optimization_mode_var", safe_defaults["gpu_optimization_mode"]))

        env["LOGIC_MINING_POWER_PROFILE"] = power_profile
        env["LOGIC_CPU_USAGE_PERCENT"] = cpu_percent
        env["LOGIC_GPU_USAGE_PERCENT"] = gpu_percent
        env["LOGIC_MINING_OPTIMIZATION_MODE"] = optimization

        # GPU-spezifische Optimierungsvariablen, vorbereitet für spätere Miner.
        env["LOGIC_GPU_AUTO_INTENSITY"] = str(get_var("gpu_auto_intensity_var", True))
        env["LOGIC_GPU_CUSTOM_INTENSITY"] = str(get_var("gpu_custom_intensity_var", ""))
        env["LOGIC_GPU_CUSTOM_WORKSIZE"] = str(get_var("gpu_custom_worksize_var", ""))
        env["LOGIC_GPU_TEMP_SAFETY"] = str(get_var("gpu_temp_safety_var", True))
        env["LOGIC_GPU_TEMP_LIMIT_C"] = str(get_var("gpu_temp_limit_var", safe_defaults["gpu_temp_limit_c"]))
        env["LOGIC_GPU_ALLOW_POWER_LIMIT_CONTROL"] = str(get_var("gpu_power_limit_control_var", False))
        env["LOGIC_GPU_ALLOW_CLOCK_CONTROL"] = str(get_var("gpu_clock_control_var", False))

        return env

    def build_gpu_miner_command(self, device: int) -> list[str]:
        node_url = self.node_url()
        miner_address = self.miner_address_var.get().strip() or DEFAULT_MINER_ADDRESS
        stats_path = (
            BASE_DIR
            / f"logicoin_gpu_miner_stats_gpu{int(device)}.json"
        ).resolve()

        cmd = app_command_for_role("gpu-miner")
        cmd += [
            "--backend", "auto",
            "--device", str(int(device)),
            "--node-url", node_url,
            "--miner-address", miner_address,
            "--batch-nonces", "262144",
            "--stats-file", str(stats_path),
            "--gpu-percent", str(
                max(5, min(100, int(float(self.gpu_usage_percent_var.get()))))
            ),
        ]
        return cmd

    def start_gpu_miner_device(
        self,
        device: int,
        visible: bool = True,
        supervised: bool = True,
    ) -> None:
        if not self.ensure_public_wallet_for_mining():
            return

        self.ensure_multi_gpu_state()
        device = int(device)

        if supervised:
            self.gpu_miner_desired_devices.add(device)
            self.gpu_miner_visible_by_device[device] = bool(visible)

        old = self.gpu_miner_processes.get(device)
        if old is not None and old.poll() is None:
            self.status_var.set(f"GPU-Miner {device} läuft bereits und wird überwacht.")
            return

        self._spawn_gpu_miner_process(device, visible=visible)

    def _spawn_gpu_miner_process(self, device: int, visible: bool) -> bool:
        device = int(device)

        old_handle = self.gpu_miner_log_handles.pop(device, None)
        if old_handle is not None:
            try:
                old_handle.close()
            except Exception:
                pass

        try:
            cmd = self.build_gpu_miner_command(device)
            env = self.mining_env()
            env["LOGIC_GPU_DEVICE"] = str(device)
            env["LOGICOIN_DATA_DIR"] = str(
                BASE_DIR.resolve()
            )

            if visible and os.name == "nt":
                proc = subprocess.Popen(
                    cmd,
                    cwd=str(BASE_DIR),
                    env=env,
                    creationflags=creationflags_new_console(),
                )
            else:
                log_path = BASE_DIR / f"logicoin_gpu_miner_gpu{device}.log"
                handle = open(log_path, "a", encoding="utf-8", buffering=1)
                handle.write(
                    "\n" + "=" * 72 + "\n"
                    f"INTERNER GPU-MINER START v{APP_VERSION} | "
                    f"{time.strftime('%d.%m.%Y %H:%M:%S')} | Device {device}\n"
                    f"Befehl: {cmd}\n"
                    + "=" * 72 + "\n"
                )
                handle.flush()

                proc = subprocess.Popen(
                    cmd,
                    cwd=str(BASE_DIR),
                    env=env,
                    stdout=handle,
                    stderr=subprocess.STDOUT,
                    stdin=subprocess.DEVNULL,
                    **hidden_subprocess_kwargs(),
                )
                self.gpu_miner_log_handles[device] = handle

            self.gpu_miner_processes[device] = proc
            self.gpu_miner_started_at[device] = time.time()
            self.gpu_miner_last_exit.pop(device, None)
            self.status_var.set(
                f"GPU-Miner {device} gestartet – automatische Überwachung aktiv."
            )
            self.after(
                1200,
                lambda d=device, expected_pid=proc.pid: self._verify_gpu_miner_start(
                    d,
                    expected_pid,
                ),
            )
            return True
        except Exception as exc:
            attempts = self.gpu_miner_restart_attempts.get(device, 0) + 1
            self.gpu_miner_restart_attempts[device] = attempts
            self.gpu_miner_next_restart[device] = time.time() + min(30, 2 ** min(attempts, 5))
            self.status_var.set(f"GPU-Miner {device} Startfehler – neuer Versuch folgt.")
            if not self.gpu_miner_desired_devices:
                messagebox.showerror(
                    "GPU-Miner Start Fehler",
                    f"GPU {device} konnte nicht gestartet werden:\n{exc}",
                )
            return False

    def _verify_gpu_miner_start(self, device: int, expected_pid: int) -> None:
        self.ensure_multi_gpu_state()
        process = self.gpu_miner_processes.get(device)

        if process is None or process.pid != expected_pid:
            return

        exit_code = process.poll()
        if exit_code is None:
            self.gpu_miner_restart_attempts[device] = 0
            self.gpu_miner_next_restart[device] = 0.0
            self.status_var.set(f"GPU-Miner {device} läuft stabil.")
            self.update_process_state()
            return

        self.gpu_miner_last_exit[device] = exit_code
        if self.gpu_miner_visible_by_device.get(device, False):
            self.gpu_miner_visible_by_device[device] = False
            self.status_var.set(
                f"GPU-Miner {device} wurde beendet – Neustarts laufen jetzt leise im Hintergrund."
            )
        else:
            self.status_var.set(
                f"GPU-Miner {device} wurde mit Code {exit_code} beendet – "
                "automatischer Neustart läuft."
            )
        self.update_process_state()

    def _gpu_miner_supervisor_loop(self) -> None:
        try:
            self.ensure_multi_gpu_state()
            now = time.time()

            for device in sorted(self.gpu_miner_desired_devices):
                process = self.gpu_miner_processes.get(device)

                if process is not None and process.poll() is None:
                    runtime = now - self.gpu_miner_started_at.get(device, now)
                    if runtime >= 20:
                        self.gpu_miner_restart_attempts[device] = 0
                    continue

                if process is not None:
                    self.gpu_miner_last_exit[device] = process.poll()

                next_restart = float(self.gpu_miner_next_restart.get(device, 0.0))
                if now < next_restart:
                    continue

                attempts = self.gpu_miner_restart_attempts.get(device, 0) + 1
                self.gpu_miner_restart_attempts[device] = attempts
                delay = min(30, 2 ** min(attempts, 5))
                self.gpu_miner_next_restart[device] = now + delay

                visible = False
                self.gpu_miner_visible_by_device[device] = False
                self.status_var.set(
                    f"GPU-Miner {device} ist offline – Neustartversuch {attempts}."
                )
                self._spawn_gpu_miner_process(device, visible=visible)

            self.update_process_state()
        finally:
            self.after(2000, self._gpu_miner_supervisor_loop)

    def start_all_gpu_miners(self) -> None:
        self.ensure_multi_gpu_state()
        gpus = self.detect_nvidia_gpus()

        if not gpus:
            answer = messagebox.askyesno(
                "Multi-GPU",
                "NVIDIA NVML konnte gerade keine GPU lesen.\n\n"
                "Soll ich trotzdem GPU 0 und GPU 1 starten?\n"
                "Das passt für deinen Gaming-PC mit RTX 3060 + RTX 2080 SUPER."
            )
            if not answer:
                return
            devices = [0, 1]
        else:
            devices = [int(gpu.get("index", 0)) for gpu in gpus]

        for device in devices:
            self.start_gpu_miner_device(device, visible=False)

        self.status_var.set(f"{len(devices)} GPU-Miner gestartet.")

    def stop_gpu_miners(self) -> None:
        self.ensure_multi_gpu_state()
        self.gpu_miner_desired_devices.clear()
        self.gpu_miner_visible_by_device.clear()
        self.gpu_miner_next_restart.clear()
        self.gpu_miner_restart_attempts.clear()

        stopped = 0
        for device, proc in list(self.gpu_miner_processes.items()):
            try:
                if proc is not None and proc.poll() is None:
                    proc.terminate()
                    try:
                        proc.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                    stopped += 1
            except Exception:
                pass

        for device, handle in list(self.gpu_miner_log_handles.items()):
            try:
                handle.close()
            except Exception:
                pass

        self.gpu_miner_processes.clear()
        self.gpu_miner_log_handles.clear()
        self.gpu_miner_started_at.clear()
        self.status_var.set(f"{stopped} GPU-Miner gestoppt.")

    def _build_multi_gpu_tab(self, tab: ttk.Frame) -> None:
        self.ensure_multi_gpu_state()
        top = ttk.Frame(tab)
        top.pack(fill="x", padx=8, pady=8)

        ttk.Button(top, text="GPU 0 starten", command=lambda: self.start_gpu_miner_device(0, visible=False)).pack(side="left", padx=5)
        ttk.Button(top, text="GPU 1 starten", command=lambda: self.start_gpu_miner_device(1, visible=False)).pack(side="left", padx=5)
        ttk.Button(top, text="Alle GPUs starten", command=self.start_all_gpu_miners).pack(side="left", padx=5)
        ttk.Button(top, text="Alle GPU-Miner stoppen", command=self.stop_gpu_miners).pack(side="left", padx=5)
        ttk.Button(top, text="Aktualisieren", command=self.refresh_multi_gpu_stats).pack(side="left", padx=5)

        self.multi_gpu_text = tk.Text(tab, height=30, bg="#22324D", fg="#F5F8FF", relief="flat")
        self.multi_gpu_text.pack(fill="both", expand=True, padx=8, pady=8)

        self.refresh_multi_gpu_stats()

    def refresh_multi_gpu_stats(self) -> None:
        self.ensure_multi_gpu_state()
        if not hasattr(self, "multi_gpu_text"):
            return

        gpu_live = self.detect_nvidia_gpus()
        stats_files = self.read_gpu_stats_files()
        stats_by_device = {}
        for s in stats_files:
            try:
                stats_by_device[int(s.get("device", -1))] = s
            except Exception:
                pass

        lines = [
            "LOGIC Multi-GPU Dashboard v0.12.15.3",
            "=" * 72,
            "",
        ]

        total_hs = 0.0
        total_raw_hs = 0.0
        total_accepted_per_minute = 0.0
        blocked_measurements = 0
        total_accepted = 0
        total_invalid = 0
        total_stale = 0
        total_stale_avoided = 0

        if not gpu_live:
            lines.append("Keine NVIDIA-GPUs über NVML erkannt.")
            lines.append("Tipp: NVIDIA-Treiber prüfen und die App neu starten.")
        else:
            for gpu in gpu_live:
                idx = int(gpu.get("index", 0))
                s = stats_by_device.get(idx, {})
                hs = float(s.get("current_hashrate_hs") or 0)
                total_hs += hs
                total_accepted += int(s.get("accepted") or 0)
                total_invalid += int(s.get("invalid", s.get("rejected", 0)) or 0)
                total_stale += int(s.get("stale") or 0)
                total_stale_avoided += int(s.get("stale_avoided") or 0)

                self.ensure_multi_gpu_state()
                proc = self.gpu_miner_processes.get(idx)
                running = proc is not None and proc.poll() is None
                status_icon = "läuft" if running else "aus/extern"

                lines.append(
                    f"GPU {idx} | {str(gpu.get('name','-')):<28} | {status_icon}"
                )
                lines.append(
                    f"  Hashrate: {self.format_hs_ui(hs):<12} "
                    f"Avg1m: {self.format_hs_ui(s.get('avg_1m_hs')):<12} "
                    f"Avg30m: {self.format_hs_ui(s.get('avg_30m_hs')):<12}"
                )
                lines.append(
                    f"  Temp: {gpu.get('temperature_c','-')} °C | "
                    f"Power: {gpu.get('power_w','-')} W | "
                    f"Load: {gpu.get('utilization_percent','-')} % | "
                    f"VRAM: {gpu.get('memory_used_mb','-')}/{gpu.get('memory_total_mb','-')} MB"
                )
                lines.append(
                    f"  Accepted: {s.get('accepted', 0)} | "
                    f"Stale: {s.get('stale', 0)} | "
                    f"vermieden: {s.get('stale_avoided', 0)} | "
                    f"Invalid: {s.get('invalid', s.get('rejected', 0))} | "
                    f"Diff: {s.get('last_diff', '-')}"
                )
                lines.append(
                    f"  Status: {s.get('last_status', 'noch keine Stats')}"
                )
                lines.append("")

        lines.extend([
            "-" * 72,
            f"Gesamt-Hashrate: {self.format_hs_ui(total_hs)}",
            f"Accepted gesamt: {total_accepted} | Stale gesamt: {total_stale} | "
            f"vermieden: {total_stale_avoided} | Invalid gesamt: {total_invalid}",
            "",
            "Hinweis:",
            "- Wenn du Miner manuell außerhalb der App startest, steht Status eventuell 'aus/extern'.",
            "- Die Stats kommen trotzdem aus logicoin_gpu_miner_stats_gpuX.json.",
            "- Ein paar Stale-Blöcke sind bei Multi-GPU normal.",
        ])

        self._set_text(self.multi_gpu_text, "\n".join(lines))


    def _build_gpu_stats_tab(self, tab: ttk.Frame) -> None:
        top = ttk.Frame(tab)
        top.pack(fill="x", padx=8, pady=8)

        ttk.Button(top, text="GPU-Stats aktualisieren", command=self.refresh_gpu_miner_stats).pack(side="left", padx=5)
        ttk.Button(top, text="Stats-Datei öffnen/Ordner", command=self.open_logicoin_folder).pack(side="left", padx=5)

        self.gpu_stats_text = tk.Text(tab, height=28, bg="#22324D", fg="#F5F8FF", relief="flat")
        self.gpu_stats_text.pack(fill="both", expand=True, padx=8, pady=8)

        self.refresh_gpu_miner_stats()

    def refresh_gpu_miner_stats(self) -> None:
        if not hasattr(self, "gpu_stats_text"):
            return

        if not GPU_MINER_STATS_FILE.exists():
            self._set_text(
                self.gpu_stats_text,
                "Noch keine GPU-Miner-Stats vorhanden.\n\n"
                "Starte den LOGIC GPU-Miner. Danach wird hier logicoin_gpu_miner_stats.json angezeigt."
            )
            return

        try:
            data = json.loads(GPU_MINER_STATS_FILE.read_text(encoding="utf-8"))
        except Exception as e:
            self._set_text(self.gpu_stats_text, f"Stats-Datei konnte nicht gelesen werden:\n{e}")
            return

        def fmt_h(v):
            try:
                v = float(v)
            except Exception:
                return "--"
            if v >= 1_000_000_000_000:
                return f"{v/1_000_000_000_000:.2f} TH/s"
            if v >= 1_000_000_000:
                return f"{v/1_000_000_000:.2f} GH/s"
            if v >= 1_000_000:
                return f"{v/1_000_000:.2f} MH/s"
            if v >= 1_000:
                return f"{v/1_000:.2f} KH/s"
            return f"{v:.2f} H/s"

        gpu = data.get("gpu", {}) or {}
        lines = [
            "LOGIC GPU Miner Live Stats",
            "=" * 54,
            f"Zeit:              {data.get('time', '-')}",
            f"Uptime:            {data.get('uptime_seconds', 0)} s",
            f"Status:            {data.get('last_status', '-')}",
            "",
            "Hashrate",
            "-" * 54,
            f"Aktuell:           {fmt_h(data.get('current_hashrate_hs'))}",
            f"Session Avg:       {fmt_h(data.get('session_avg_hs'))}",
            f"Avg 1 min:         {fmt_h(data.get('avg_1m_hs'))}",
            f"Avg 30 min:        {fmt_h(data.get('avg_30m_hs'))}",
            f"Avg 1 h:           {fmt_h(data.get('avg_1h_hs'))}",
            f"Avg 6 h:           {fmt_h(data.get('avg_6h_hs'))}",
            f"Avg 12 h:          {fmt_h(data.get('avg_12h_hs'))}",
            "",
            "GPU",
            "-" * 54,
            f"Device:            {data.get('device', '-')}",
            f"Name:              {gpu.get('name', '-')}",
            f"Temperatur:        {gpu.get('temperature_c', '-')} °C",
            f"Power:             {gpu.get('power_w', '-')} W",
            f"Auslastung:        {gpu.get('utilization_percent', '-')} %",
            f"VRAM:              {gpu.get('memory_used_mb', '-')} / {gpu.get('memory_total_mb', '-')} MB",
            "",
            "Network / Mining",
            "-" * 54,
            f"Height:            #{data.get('last_height', '-')}",
            f"Difficulty:        {data.get('last_diff', '-')}",
            f"TXs:               {data.get('last_txs', '-')}",
            f"Accepted:          {data.get('accepted', 0)}",
            f"Invalid:           {data.get('invalid', data.get('rejected', 0))}",
            f"Stale:             {data.get('stale', 0)}",
            f"Jobs:              {data.get('jobs', 0)}",
            f"Last accepted:     #{data.get('last_accepted_height', '-')}",
            f"Last nonce:        {data.get('last_nonce', '-')}",
            f"Last hash:         {data.get('last_hash', '-')}",
        ]
        self._set_text(self.gpu_stats_text, "\n".join(lines))


    def _build_gpu_optimization_tab(self, tab: ttk.Frame) -> None:
        top = ttk.Frame(tab)
        top.pack(fill="x", pady=8)

        ttk.Label(top, text="Mining-Optimierungsmodus:").grid(row=0, column=0, sticky="w", padx=5, pady=5)
        mode_box = ttk.Combobox(
            top,
            textvariable=self.optimization_mode_var,
            width=34,
            state="readonly",
            values=[
                "manual_afterburner",
                "auto_safe",
                "legacy_gpu",
                "custom",
            ],
        )
        mode_box.grid(row=0, column=1, sticky="w", padx=5, pady=5)
        ttk.Button(top, text="Modus speichern", command=self.save_current_settings).grid(row=0, column=2, padx=5, pady=5)

        options = ttk.LabelFrame(tab, text="GPU-Mining Optionen")
        options.pack(fill="x", padx=4, pady=8)

        ttk.Checkbutton(
            options,
            text="Auto-Intensity erlauben (nur Software-Intensity, kein OC/UV)",
            variable=self.gpu_auto_intensity_var,
            command=self.save_current_settings,
        ).grid(row=0, column=0, columnspan=3, sticky="w", padx=8, pady=5)

        ttk.Checkbutton(
            options,
            text="Temperatur-Sicherheitslimit aktiv",
            variable=self.gpu_temp_safety_var,
            command=self.save_current_settings,
        ).grid(row=1, column=0, sticky="w", padx=8, pady=5)

        ttk.Label(options, text="Temp-Limit °C:").grid(row=1, column=1, sticky="e", padx=5, pady=5)
        ttk.Spinbox(options, from_=50, to=95, textvariable=self.gpu_temp_limit_var, width=8, command=self.save_current_settings).grid(row=1, column=2, sticky="w", padx=5, pady=5)

        ttk.Label(options, text="Custom Intensity:").grid(row=2, column=0, sticky="w", padx=8, pady=5)
        ttk.Spinbox(options, from_=1, to=100, textvariable=self.gpu_custom_intensity_var, width=8, command=self.save_current_settings).grid(row=2, column=1, sticky="w", padx=5, pady=5)

        ttk.Label(options, text="Custom Worksize:").grid(row=3, column=0, sticky="w", padx=8, pady=5)
        ttk.Spinbox(options, from_=32, to=1024, increment=32, textvariable=self.gpu_custom_worksize_var, width=8, command=self.save_current_settings).grid(row=3, column=1, sticky="w", padx=5, pady=5)

        dangerous = ttk.LabelFrame(tab, text="OC/UV-Steuerung")
        dangerous.pack(fill="x", padx=4, pady=8)

        ttk.Checkbutton(
            dangerous,
            text="GPU Power-Limit durch LOGIC-App erlauben (standardmäßig AUS)",
            variable=self.gpu_power_limit_control_var,
            command=self.save_current_settings,
        ).pack(anchor="w", padx=8, pady=5)

        ttk.Checkbutton(
            dangerous,
            text="GPU Clock/Memory durch LOGIC-App erlauben (standardmäßig AUS)",
            variable=self.gpu_clock_control_var,
            command=self.save_current_settings,
        ).pack(anchor="w", padx=8, pady=5)

        info = tk.Text(tab, height=18, bg="#22324D", fg="#F5F8FF", relief="flat")
        info.pack(fill="both", expand=True, padx=4, pady=8)
        info.insert("1.0",
            "Modi:\n\n"
            "manual_afterburner:\n"
            "- empfohlen, wenn du selbst mit MSI Afterburner OC/UV machst\n"
            "- LOGIC-App verändert keine Taktraten, keine Spannung, kein Power-Limit\n"
            "- App startet/stoppt Miner und überwacht nur Temperatur/Power/Hashrate\n\n"
            "auto_safe:\n"
            "- App/Miner darf nur sichere Software-Werte wie Intensity/Worksize automatisch wählen\n"
            "- kein OC/UV, keine Spannung, kein Power-Limit\n\n"
            "legacy_gpu:\n"
            "- konservativ für ältere Karten wie GTX 1050 Ti\n"
            "- niedrige Intensity, niedriger VRAM-Bedarf, stabil vor maximal aggressiv\n\n"
            "custom:\n"
            "- du setzt Intensity/Worksize selbst\n"
            "- nützlich zum Testen verschiedener Karten\n\n"
            "Wichtig:\n"
            "OC/UV mit Afterburner bleibt komplett deine Sache. Standardmäßig fasst LOGIC keine GPU-Takte an.\n"
            "Das ist absichtlich so, damit alte Karten und verschiedene Hersteller sicher laufen.\n"
        )
        info.configure(state="disabled")

    def _build_network_tab(self, tab: ttk.Frame) -> None:
        header = ttk.Frame(tab)
        header.pack(fill="x", padx=8, pady=8)

        ttk.Label(header, text="Eigene LAN-URL:").pack(side="left")
        ttk.Entry(header, textvariable=self.local_lan_url_var, width=34, state="readonly").pack(side="left", padx=6)
        ttk.Button(header, text="Kopieren", command=self.copy_lan_url).pack(side="left", padx=4)
        ttk.Button(header, text="Aktualisieren", command=self.refresh_network_async).pack(side="left", padx=4)
        ttk.Button(header, text="Jetzt synchronisieren", command=self.sync_network_now_async).pack(side="left", padx=4)

        peer_row = ttk.Frame(tab)
        peer_row.pack(fill="x", padx=8, pady=(0, 8))

        ttk.Label(peer_row, text="Peer-URL:").pack(side="left")
        ttk.Entry(peer_row, textvariable=self.peer_url_var, width=44).pack(side="left", padx=6)
        ttk.Button(peer_row, text="Peer hinzufügen", command=self.add_peer_from_ui).pack(side="left", padx=4)
        ttk.Button(peer_row, text="Peer testen", command=self.test_peer_from_ui).pack(side="left", padx=4)
        ttk.Button(peer_row, text="Ausgewählten Peer entfernen", command=self.remove_selected_peer).pack(side="left", padx=4)

        ttk.Label(tab, textvariable=self.network_summary_var, style="Small.TLabel").pack(fill="x", padx=8, pady=(0, 6))

        columns = ("url", "online", "height", "latency", "version", "last_sync", "error")
        self.peer_tree = ttk.Treeview(tab, columns=columns, show="headings", height=12)
        headings = {
            "url": "Peer",
            "online": "Status",
            "height": "Höhe",
            "latency": "Latenz",
            "version": "Version",
            "last_sync": "Letzter Sync",
            "error": "Fehler",
        }
        widths = {
            "url": 250,
            "online": 80,
            "height": 70,
            "latency": 85,
            "version": 80,
            "last_sync": 140,
            "error": 330,
        }
        for column in columns:
            self.peer_tree.heading(column, text=headings[column])
            self.peer_tree.column(column, width=widths[column], anchor="w")
        self.peer_tree.pack(fill="both", expand=True, padx=8, pady=8)

        self.network_text = tk.Text(tab, height=9, bg="#22324D", fg="#F5F8FF", relief="flat")
        self.network_text.pack(fill="x", padx=8, pady=(0, 8))
        self._set_text(
            self.network_text,
            "LAN-Testnet v0.12.15.3\n\n"
            "PC 1 und PC 2 müssen denselben LOGIC-Build und dieselbe Config verwenden.\n"
            "Füge auf einem PC die LAN-URL des anderen PCs als Peer hinzu.\n"
            "Die Gegenrichtung wird automatisch registriert, sofern Port 8080 erreichbar ist."
        )

    def refresh_network_async(self) -> None:
        if not hasattr(self, "peer_tree"):
            return
        threading.Thread(target=self._network_worker, daemon=True).start()

    def _network_worker(self) -> None:
        try:
            network = get_json(self.node_url() + "/network")
            peers = get_json(self.node_url() + "/peers")
            self.after(0, lambda: self._apply_network_data(network, peers))
        except Exception as exc:
            self.after(0, lambda error=str(exc): self.network_summary_var.set(f"Netzwerkdaten nicht erreichbar: {error}"))

    def _apply_network_data(self, network: Dict[str, Any], peers_data: Dict[str, Any]) -> None:
        self.local_lan_url_var.set(str(network.get("lan_url", "-")))
        self.network_summary_var.set(
            f"Netzwerk: {network.get('network_id')} | "
            f"Node: {network.get('node_name')} | "
            f"Höhe #{network.get('height')} | "
            f"Peers {network.get('peers_online', 0)}/{network.get('peers_total', 0)} | "
            f"Sync alle {network.get('peer_sync_interval_seconds', '-')}s"
        )

        for item in self.peer_tree.get_children():
            self.peer_tree.delete(item)

        for peer in peers_data.get("peers", []):
            last_sync = peer.get("last_sync")
            if last_sync:
                try:
                    last_sync_text = time.strftime("%d.%m. %H:%M:%S", time.localtime(float(last_sync)))
                except Exception:
                    last_sync_text = "-"
            else:
                last_sync_text = "-"

            latency = peer.get("latency_ms")
            latency_text = f"{float(latency):.0f} ms" if latency is not None else "-"

            self.peer_tree.insert("", "end", values=(
                peer.get("url", "-"),
                "online" if peer.get("online") else "offline",
                peer.get("height", "-"),
                latency_text,
                peer.get("version", "-"),
                last_sync_text,
                peer.get("error", ""),
            ))

        last_sync = network.get("last_sync", {}) or {}
        peer_storage = network.get("peer_storage", {}) or {}
        self._set_text(self.network_text, "\n".join([
            "LOGIC LAN-Testnet v0.12.15.3",
            "=" * 58,
            f"LAN-IP:              {network.get('lan_ip', '-')}",
            f"LAN-URL:             {network.get('lan_url', '-')}",
            f"Netzwerk-ID:         {network.get('network_id', '-')}",
            f"Fingerprint:         {network.get('network_fingerprint', '-')}",
            f"Node-Uptime:         {network.get('uptime_seconds', 0)} Sekunden",
            f"Peer-Sync aktiv:     {network.get('peer_sync_enabled', False)}",
            f"Peers geprüft:       {last_sync.get('peers_checked', 0)}",
            f"Peers online:        {last_sync.get('peers_online', 0)}",
            f"Chains übernommen:   {last_sync.get('chains_adopted', 0)}",
            f"Sync-Fehler:         {last_sync.get('errors', 0)}",
            f"Status-Datei:        {'OK' if peer_storage.get('ok', True) else 'temporär blockiert'}",
            f"Storage-Warnung:     {peer_storage.get('last_error', '')}",
            "",
            "Regel: Die gültige Chain mit der höchsten kumulativen Arbeit gewinnt.",
        ]))

    def test_peer_from_ui(self) -> None:
        peer = self.peer_url_var.get().strip()

        if not peer:
            selected = self.peer_tree.selection()
            if selected:
                values = self.peer_tree.item(selected[0], "values")
                peer = str(values[0]) if values else ""

        if not peer:
            messagebox.showinfo(
                "Peer testen",
                "Bitte eine Peer-URL eingeben oder einen Peer in der Tabelle auswählen."
            )
            return

        self.status_var.set(f"Teste Peer {peer} ...")

        def worker() -> None:
            try:
                result = post_json(self.node_url() + "/peers/test", {"url": peer})

                lines = [
                    f"Peer: {result.get('peer', peer)}",
                    "",
                    f"TCP-Verbindung: {'OK' if result.get('tcp_ok') else 'FEHLER'}",
                    f"HTTP /info:     {'OK' if result.get('http_ok') else 'FEHLER'}",
                    f"LOGIC-Netzwerk: {'OK' if result.get('logicoin_ok') else 'NICHT BESTÄTIGT'}",
                ]

                if result.get("latency_ms") is not None:
                    lines.append(f"Latenz:          {result.get('latency_ms')} ms")

                info = result.get("info") or {}
                if info:
                    lines.extend([
                        "",
                        f"Node:            {info.get('node_name', '-')}",
                        f"Version:         {info.get('version', '-')}",
                        f"Höhe:            #{info.get('height', '-')}",
                        f"Netzwerk-ID:     {info.get('network_id', '-')}",
                    ])

                if result.get("error"):
                    lines.extend(["", f"Fehler: {result.get('error')}"])

                title = "Peer-Test erfolgreich" if result.get("ok") else "Peer-Test fehlgeschlagen"
                self.after(0, lambda: messagebox.showinfo(title, "\n".join(lines)))
                self.after(0, lambda: self.status_var.set(title))
                self.after(0, self.refresh_network_async)

            except Exception as exc:
                self.after(
                    0,
                    lambda error=str(exc): messagebox.showerror("Peer testen", error)
                )

        threading.Thread(target=worker, daemon=True).start()

    def add_peer_from_ui(self) -> None:
        peer = self.peer_url_var.get().strip()
        if not peer:
            messagebox.showinfo("Peer hinzufügen", "Bitte eine Peer-URL eingeben, z. B. http://192.168.178.50:8080")
            return

        try:
            response = post_json(self.node_url() + "/peers/add", {
                "url": peer,
                "bidirectional": True,
                "callback_url": self.local_lan_url_var.get(),
            })
            self.settings["last_peer_url"] = peer
            save_json_file(SETTINGS_FILE, self.settings)
            self.status_var.set(str(response.get("message", "Peer verarbeitet.")))
            self.refresh_network_async()
        except Exception as exc:
            messagebox.showerror("Peer hinzufügen", str(exc))

    def remove_selected_peer(self) -> None:
        selected = self.peer_tree.selection()
        if not selected:
            messagebox.showinfo("Peer entfernen", "Bitte zuerst einen Peer in der Tabelle auswählen.")
            return

        values = self.peer_tree.item(selected[0], "values")
        peer = str(values[0]) if values else ""
        if not peer:
            return

        if not messagebox.askyesno("Peer entfernen", f"Peer entfernen?\n\n{peer}"):
            return

        try:
            post_json(self.node_url() + "/peers/remove", {"url": peer})
            self.refresh_network_async()
        except Exception as exc:
            messagebox.showerror("Peer entfernen", str(exc))

    def sync_network_now_async(self) -> None:
        self.status_var.set("Peer-Synchronisierung läuft ...")

        def worker() -> None:
            try:
                result = post_json(self.node_url() + "/sync", {})
                summary = result.get("summary", result)
                self.after(0, lambda: self.status_var.set(
                    f"Sync fertig: {summary.get('peers_online', 0)} online, "
                    f"{summary.get('chains_adopted', 0)} Chain(s) übernommen."
                ))
                self.after(0, self.refresh_network_async)
                self.after(0, self.refresh_all_async)
            except Exception as exc:
                self.after(0, lambda error=str(exc): messagebox.showerror("Peer-Sync", error))

        threading.Thread(target=worker, daemon=True).start()

    def copy_lan_url(self) -> None:
        value = self.local_lan_url_var.get().strip()
        if not value or value == "-":
            return
        self.clipboard_clear()
        self.clipboard_append(value)
        self.status_var.set("LAN-URL kopiert.")

    def _build_hardware_tab(self, tab: ttk.Frame) -> None:
        top = ttk.Frame(tab)
        top.pack(fill="x", pady=8)
        ttk.Button(top, text="Hardware aktualisieren", command=self.refresh_hardware_async).pack(side="left", padx=5)
        self.cpu_info_var = tk.StringVar(value="CPU: -")
        ttk.Label(top, textvariable=self.cpu_info_var).pack(side="left", padx=14)
        ttk.Label(
            top,
            text="Miner aktiv = 5s/60s · NVML = Ø5s/Maximum",
            style="Small.TLabel",
        ).pack(side="left", padx=14)

        columns = (
            "id",
            "name",
            "profile",
            "miner",
            "util",
            "temp",
            "power",
            "mem",
        )
        self.gpu_tree = ttk.Treeview(
            tab,
            columns=columns,
            show="headings",
        )
        headers = {
            "id": "GPU",
            "name": "Name",
            "profile": "LOGIC-Profil",
            "miner": "Miner aktiv",
            "util": "NVML Ø5s / Max",
            "temp": "Temperatur",
            "power": "Power Ø5s / Jetzt",
            "mem": "VRAM",
        }
        widths = {
            "id": 55,
            "name": 260,
            "profile": 130,
            "miner": 105,
            "util": 130,
            "temp": 105,
            "power": 145,
            "mem": 145,
        }
        for col in columns:
            self.gpu_tree.heading(col, text=headers[col])
            self.gpu_tree.column(col, width=widths[col], anchor="w")
        self.gpu_tree.pack(fill="both", expand=True)

    def _build_benchmark_tab(self, tab: ttk.Frame) -> None:
        cpu_box = ttk.LabelFrame(tab, text="CPU")
        cpu_box.pack(fill="x", padx=6, pady=(6, 4))
        ttk.Button(cpu_box, text="CPU-Benchmark 10 s", command=self.start_cpu_benchmark_async).pack(side="left", padx=7, pady=7)
        ttk.Label(cpu_box, textvariable=self.benchmark_var).pack(side="left", padx=8)

        gpu_box = ttk.LabelFrame(tab, text="LOGIC CUDA GPU-Benchmark")
        gpu_box.pack(fill="x", padx=6, pady=4)
        ttk.Label(gpu_box, text="GPU:").pack(side="left", padx=(7, 3), pady=7)
        self.gpu_benchmark_device_combo = ttk.Combobox(gpu_box, textvariable=self.gpu_benchmark_device_var, width=31, state="readonly", values=["0"])
        self.gpu_benchmark_device_combo.pack(side="left", padx=3, pady=7)
        ttk.Label(gpu_box, text="Dauer:").pack(side="left", padx=(8, 3))
        ttk.Combobox(gpu_box, textvariable=self.gpu_benchmark_duration_var, width=6, state="readonly", values=[10, 20, 30, 60]).pack(side="left", padx=3)
        ttk.Button(gpu_box, text="Ausgewählte GPU testen", command=lambda: self.start_gpu_benchmark_async(False)).pack(side="left", padx=5)
        ttk.Button(gpu_box, text="Alle GPUs vergleichen", command=lambda: self.start_gpu_benchmark_async(True), style="Accent.TButton").pack(side="left", padx=5)

        ttk.Label(tab, textvariable=self.gpu_benchmark_status_var, style="Small.TLabel").pack(fill="x", padx=8, pady=(3, 5))
        columns = ("device", "gpu", "hashrate", "temp", "power", "efficiency", "util", "duration", "status")
        self.gpu_benchmark_tree = ttk.Treeview(tab, columns=columns, show="headings", height=10)
        headers = {"device":"GPU","gpu":"Modell","hashrate":"Hashrate","temp":"Temp Ø/Max","power":"Watt Ø/Max","efficiency":"Effizienz","util":"Load Ø","duration":"Dauer","status":"Status"}
        widths = {"device":55,"gpu":250,"hashrate":115,"temp":100,"power":105,"efficiency":115,"util":75,"duration":70,"status":180}
        for column in columns:
            self.gpu_benchmark_tree.heading(column, text=headers[column])
            self.gpu_benchmark_tree.column(column, width=widths[column], anchor="w")
        self.gpu_benchmark_tree.pack(fill="both", expand=True, padx=6, pady=5)
        note = tk.Text(tab, height=5, bg="#22324D", fg="#F5F8FF", relief="flat")
        note.pack(fill="x", padx=6, pady=(2, 6))
        note.insert(
            "1.0",
            "Der Benchmark nutzt LogicHash-v2-CUDA-Mix ohne Node und ohne Blockfund-Zufall.\n"
            "Er zeigt Hashrate, Temperatur, Leistungsaufnahme und Hashrate pro Watt.\n"
            "Für den präzisen Benchmark den CUDA-Worker aus v0.12.15.3 neu bauen.\n"
            "Ergebnisse: logicoin_gpu_benchmark_results.json",
        )
        note.configure(state="disabled")

    def _build_overview_tab(self, tab: ttk.Frame) -> None:
        row1 = ttk.Frame(tab)
        row1.pack(fill="x", pady=4)
        self.height_var = self.metric(row1, "Blockhöhe")
        self.valid_var = self.metric(row1, "Chain")
        self.diff_var = self.metric(row1, "Nächste Difficulty")
        self.mempool_count_var = self.metric(row1, "Mempool")

        row2 = ttk.Frame(tab)
        row2.pack(fill="x", pady=4)
        self.reward_var = self.metric(row2, "Blockreward")
        self.tx_count_var = self.metric(row2, "Bestätigte TXs")
        self.avg_time_var = self.metric(row2, "Ø Blockzeit")
        self.avg_hashrate_var = self.metric(row2, "Ø Hashrate")

        self.info_text = tk.Text(tab, height=14, bg="#22324D", fg="#F5F8FF", relief="flat")
        self.info_text.pack(fill="both", expand=True, pady=12)

    def _build_blocks_tab(self, tab: ttk.Frame) -> None:
        columns = ("height", "hash", "miner", "reward", "diff", "txs", "time", "hps")
        self.blocks_tree = ttk.Treeview(tab, columns=columns, show="headings")
        headers = {"height":"Höhe","hash":"Hash","miner":"Miner","reward":"Reward","diff":"Diff","txs":"TXs","time":"Zeit","hps":"H/s"}
        widths = {"height":70,"hash":240,"miner":260,"reward":110,"diff":70,"txs":70,"time":90,"hps":120}
        for col in columns:
            self.blocks_tree.heading(col, text=headers[col])
            self.blocks_tree.column(col, width=widths[col], anchor="w")
        self.blocks_tree.pack(fill="both", expand=True)

    def _build_mempool_tab(self, tab: ttk.Frame) -> None:
        columns = ("txid", "from", "to", "amount", "fee", "nonce", "memo")
        self.mempool_tree = ttk.Treeview(tab, columns=columns, show="headings")
        headers = {"txid":"TXID","from":"Von","to":"An","amount":"Amount","fee":"Fee","nonce":"Nonce","memo":"Memo"}
        widths = {"txid":210,"from":230,"to":230,"amount":100,"fee":90,"nonce":70,"memo":220}
        for col in columns:
            self.mempool_tree.heading(col, text=headers[col])
            self.mempool_tree.column(col, width=widths[col], anchor="w")
        self.mempool_tree.pack(fill="both", expand=True)

    def _build_balances_tab(self, tab: ttk.Frame) -> None:
        top = ttk.Frame(tab)
        top.pack(fill="x", pady=6)
        ttk.Label(top, text="Adresse suchen:").pack(side="left")
        self.address_search_var = tk.StringVar()
        ttk.Entry(top, textvariable=self.address_search_var, width=52).pack(side="left", padx=8)
        ttk.Button(top, text="Adresse prüfen", command=self.lookup_address_async).pack(side="left")

        self.address_info_text = tk.Text(tab, height=7, bg="#22324D", fg="#F5F8FF", relief="flat")
        self.address_info_text.pack(fill="x", pady=(0, 8))

        self.balances_tree = ttk.Treeview(tab, columns=("address","balance"), show="headings")
        self.balances_tree.heading("address", text="Adresse")
        self.balances_tree.heading("balance", text="Balance")
        self.balances_tree.column("address", width=650)
        self.balances_tree.column("balance", width=180)
        self.balances_tree.pack(fill="both", expand=True)

    def _build_logs_tab(self, tab: ttk.Frame) -> None:
        buttons = ttk.Frame(tab)
        buttons.pack(fill="x", pady=6)
        ttk.Button(buttons, text="Logs aktualisieren", command=self.refresh_logs).pack(side="left", padx=5)
        ttk.Button(buttons, text="Logs leeren", command=self.clear_logs).pack(side="left", padx=5)
        self.logs_text = tk.Text(tab, bg="#22324D", fg="#F5F8FF", relief="flat")
        self.logs_text.pack(fill="both", expand=True)

    # Settings/processes

    def node_url(self) -> str:
        return normalize_node_url(self.node_url_var.get())

    def save_current_settings(self) -> None:
        self.settings.update({
            "node_url": self.node_url(),
            "miner_address": self.miner_address_var.get().strip() or DEFAULT_MINER_ADDRESS,
            "coin_algorithm": self.coin_algo_var.get(),
            "external_miner_path": self.external_path_var.get(),
            "external_miner_args": self.external_args_var.get(),
            "auto_start_node_with_app": self.auto_start_node_var.get(),
            "mining_optimization_mode": self.optimization_mode_var.get(),
            "gpu_auto_intensity": self.gpu_auto_intensity_var.get(),
            "gpu_custom_intensity": int(self.gpu_custom_intensity_var.get()),
            "gpu_custom_worksize": int(self.gpu_custom_worksize_var.get()),
            "gpu_temp_safety_enabled": self.gpu_temp_safety_var.get(),
            "gpu_temp_limit_c": int(self.gpu_temp_limit_var.get()),
            "gpu_power_limit_control": self.gpu_power_limit_control_var.get(),
            "gpu_clock_control": self.gpu_clock_control_var.get(),
            "mining_power_profile": self.mining_power_profile_var.get(),
            "cpu_usage_percent": int(float(self.cpu_usage_percent_var.get())),
            "gpu_usage_percent": int(float(self.gpu_usage_percent_var.get())),
            "manual_miner_visible_console": self.manual_miner_console_var.get(),
            "auto_apply_power_profile": self.auto_apply_power_profile_var.get(),
            "internal_cpu_miner_mode": self.internal_cpu_miner_mode_var.get(),
            "last_peer_url": self.peer_url_var.get().strip(),
            "gpu_benchmark_duration_seconds": int(self.gpu_benchmark_duration_var.get()),
            "easy_mining_mode": self.easy_mining_mode_var.get(),
            "easy_miner_visible": bool(self.easy_miner_visible_var.get()),
            "logic_test_rate_eur": float(self.logic_test_rate_eur_var.get()),
            "logic_test_rate_usd": float(self.logic_test_rate_usd_var.get()),
        })
        save_json_file(SETTINGS_FILE, self.settings)
        self.status_var.set("Einstellungen gespeichert.")

    def update_process_state(self) -> None:
        node_internal = self.node_process is not None and self.node_process.poll() is None
        cpu_running = self.cpu_miner_process is not None and self.cpu_miner_process.poll() is None
        ext_running = self.external_miner_process is not None and self.external_miner_process.poll() is None
        gpu_running_devices = [
            device for device, process in self.gpu_miner_processes.items()
            if process is not None and process.poll() is None
        ]

        if node_internal:
            node_text = f"v{APP_VERSION} intern"
        elif self.connected_node_version:
            node_text = f"v{self.connected_node_version} extern"
        else:
            node_text = "aus"

        cpu_stats = self.read_cpu_miner_stats()
        cpu_hs = float(cpu_stats.get("current_hashrate_hs") or 0.0)
        cpu_target = int(cpu_stats.get("target_percent") or self.cpu_usage_percent_var.get())
        cpu_text = (
            f"AKTIV • {cpu_target}% • {self.format_hs_ui(cpu_hs)}"
            if cpu_running else "INAKTIV"
        )

        gpu_count = len(gpu_running_devices)
        desired_count = len(self.gpu_miner_desired_devices)
        if gpu_count and gpu_count == desired_count:
            gpu_text = f"AKTIV • {gpu_count}/{desired_count} • überwacht"
        elif desired_count:
            gpu_text = f"NEUSTART • {gpu_count}/{desired_count} aktiv"
        else:
            gpu_text = "INAKTIV"
        ext_text = "läuft" if ext_running else "aus"

        self.node_state_var.set(node_text)
        self.cpu_miner_state_var.set(cpu_text)
        self.external_miner_state_var.set(ext_text)
        self.dash_node_value_var.set(node_text)
        self.dash_cpu_miner_value_var.set(cpu_text)
        self.dash_gpu_miner_value_var.set(gpu_text)
        self.app_mode_var.set(
            f"{self.easy_mining_mode_var.get()} | "
            f"CPU {self.cpu_usage_percent_var.get()}% | "
            f"GPU {self.gpu_usage_percent_var.get()}%"
        )

    def start_all(self) -> None:
        self.start_easy_mining()

    def stop_all(self) -> None:
        self.stop_easy_mining()
        self.stop_external_miner()
        self.stop_node()
        self.status_var.set("Alles gestoppt.")

    def open_easy_mining_dialog(self) -> None:
        dialog = tk.Toplevel(self)
        dialog.title("LOGIC Mining starten")
        dialog.transient(self)
        dialog.grab_set()
        dialog.resizable(False, False)
        dialog.after(50, lambda: enable_windows_11_rounded_corners(dialog))

        frame = ttk.Frame(dialog)
        frame.pack(fill="both", expand=True, padx=16, pady=14)

        ttk.Label(frame, text="Was soll minen?", font=("Segoe UI", 13, "bold")).pack(anchor="w", pady=(0, 8))
        ttk.Radiobutton(frame, text="Nur GPU – empfohlen", variable=self.easy_mining_mode_var, value="gpu").pack(anchor="w", pady=3)
        ttk.Radiobutton(frame, text="Nur CPU – Testmodus", variable=self.easy_mining_mode_var, value="cpu").pack(anchor="w", pady=3)
        ttk.Radiobutton(frame, text="GPU + CPU – alles zusammen", variable=self.easy_mining_mode_var, value="gpu_cpu").pack(anchor="w", pady=3)

        ttk.Separator(frame).pack(fill="x", pady=10)
        ttk.Label(
            frame,
            text=f"Aktuelle Leistung: CPU {self.cpu_usage_percent_var.get()}% / GPU {self.gpu_usage_percent_var.get()}%",
        ).pack(anchor="w")
        ttk.Label(frame, text="Interne Miner laufen leise im Hintergrund. Logs bleiben verfügbar.", style="Small.TLabel").pack(anchor="w", pady=6)

        buttons = ttk.Frame(frame)
        buttons.pack(fill="x", pady=(10, 0))
        ttk.Button(
            buttons,
            text="▶ Mining starten",
            command=lambda: (dialog.destroy(), self.start_easy_mining()),
            style="Accent.TButton",
        ).pack(side="left", padx=4)
        ttk.Button(buttons, text="Abbrechen", command=dialog.destroy).pack(side="left", padx=4)

    def start_easy_mining(self) -> None:
        self.save_current_settings()
        mode = self.easy_mining_mode_var.get().strip().lower()
        visible = False

        self.status_var.set("Node wird geprüft, danach startet das Mining ...")
        self.ensure_current_node_async(force_start=True)

        threading.Thread(
            target=self._wait_for_node_then_start_mining,
            args=(mode, visible),
            daemon=True,
        ).start()

    def _wait_for_node_then_start_mining(self, mode: str, visible: bool) -> None:
        info: dict[str, Any] | None = None

        for _attempt in range(50):
            try:
                candidate = get_json(self.node_url() + "/info", timeout=1)
                if str(candidate.get("version", "")) == APP_VERSION:
                    info = candidate
                    break
            except Exception:
                pass
            time.sleep(0.25)

        if info is None:
            self.after(
                0,
                lambda: messagebox.showerror(
                    "Mining starten",
                    "Der aktuelle Logicoin-Node war nach mehreren Versuchen "
                    "noch nicht erreichbar. Bitte Node-Log prüfen.",
                ),
            )
            self.after(
                0,
                lambda: self.status_var.set(
                    "Mining wurde nicht gestartet, weil der Node nicht bereit war."
                ),
            )
            return

        self.after(0, lambda: self._start_selected_easy_miners(mode, visible))

    def _start_selected_easy_miners(self, mode: str, visible: bool) -> None:
        if mode not in {"cpu", "gpu_cpu"}:
            self.stop_cpu_miner()

        if mode not in {"gpu", "gpu_cpu"}:
            self.stop_gpu_miners()

        if mode in {"cpu", "gpu_cpu"}:
            if not visible:
                self.internal_cpu_miner_mode_var.set("background")
            self.start_cpu_miner()

        if mode in {"gpu", "gpu_cpu"}:
            gpus = self.detect_nvidia_gpus()
            if not gpus:
                messagebox.showerror(
                    "Mining starten",
                    "Keine NVIDIA-GPU wurde über NVML erkannt.",
                )
                return

            for gpu in gpus:
                self.start_gpu_miner_device(
                    int(gpu.get("index", 0)),
                    visible=visible,
                    supervised=True,
                )

        self.status_var.set(
            f"Mining aktiv: {mode.upper()} | "
            f"CPU {self.cpu_usage_percent_var.get()}% | "
            f"GPU {self.gpu_usage_percent_var.get()}% | "
            "GPU-Überwachung aktiv"
        )
        self.update_process_state()

    def stop_easy_mining(self) -> None:
        self.stop_cpu_miner()
        self.stop_gpu_miners()
        self.status_var.set("CPU- und GPU-Mining gestoppt. Node bleibt online.")
        self.update_process_state()

    def on_power_profile_changed(self, *_args) -> None:
        profile = self.mining_power_profile_var.get().lower().strip()
        if profile in POWER_PROFILE_PRESETS and self.auto_apply_power_profile_var.get():
            self.apply_power_profile(save=False, restart_running=True)
        else:
            self.refresh_profile_preview()

    def on_manual_power_changed(self, *_args) -> None:
        self.mining_power_profile_var.set("custom")
        self.refresh_profile_preview()

    def apply_custom_power_values(self) -> None:
        self.mining_power_profile_var.set("custom")
        self.apply_power_profile(save=True, restart_running=True)

    def apply_power_profile(self, save: bool = True, restart_running: bool = True) -> None:
        profile = self.mining_power_profile_var.get().lower().strip()

        if profile in POWER_PROFILE_PRESETS:
            preset = POWER_PROFILE_PRESETS[profile]
            self.cpu_usage_percent_var.set(int(preset["cpu"]))
            self.gpu_usage_percent_var.set(int(preset["gpu"]))
        else:
            profile = "custom"
            self.mining_power_profile_var.set("custom")
            self.cpu_usage_percent_var.set(max(5, min(100, int(float(self.cpu_usage_percent_var.get())))))
            self.gpu_usage_percent_var.set(max(5, min(100, int(float(self.gpu_usage_percent_var.get())))))

        cpu_running = self.is_cpu_miner_running()
        running_gpus = [
            device for device, process in self.gpu_miner_processes.items()
            if process is not None and process.poll() is None
        ]
        visible = False

        self.refresh_profile_preview()
        if save:
            self.save_current_settings()

        if restart_running and (cpu_running or running_gpus):
            if cpu_running:
                self.stop_cpu_miner()
            if running_gpus:
                self.stop_gpu_miners()

            def restart() -> None:
                if cpu_running:
                    self.start_cpu_miner()
                for device in running_gpus:
                    self.start_gpu_miner_device(device, visible=visible)
                self.status_var.set(
                    f"Leistung angewendet: CPU {self.cpu_usage_percent_var.get()}% / "
                    f"GPU {self.gpu_usage_percent_var.get()}%"
                )

            self.after(800, restart)
        else:
            self.status_var.set(
                f"Leistung gespeichert: CPU {self.cpu_usage_percent_var.get()}% / "
                f"GPU {self.gpu_usage_percent_var.get()}%"
            )

    def open_logicoin_folder(self) -> None:
        try:
            if os.name == "nt":
                os.startfile(str(BASE_DIR))
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(BASE_DIR)])
            else:
                subprocess.Popen(["xdg-open", str(BASE_DIR)])
        except Exception as e:
            messagebox.showerror("Ordner öffnen", str(e))

    def refresh_profile_preview(self) -> None:
        try:
            cpu = max(5, min(100, int(float(self.cpu_usage_percent_var.get()))))
            gpu = max(5, min(100, int(float(self.gpu_usage_percent_var.get()))))
            if hasattr(self, "cpu_percent_label_var"):
                self.cpu_percent_label_var.set(f"{cpu}%")
            if hasattr(self, "gpu_percent_label_var"):
                self.gpu_percent_label_var.set(f"{gpu}%")
        except Exception:
            cpu, gpu = 50, 70

        profile = self.mining_power_profile_var.get().lower().strip()
        description = (
            POWER_PROFILE_PRESETS.get(profile, {}).get("description")
            if profile in POWER_PROFILE_PRESETS
            else "Eigene exakte Prozentwerte."
        )

        text = (
            f"Profil: {profile.upper()}\n"
            f"CPU-Ziel: {cpu}%\n"
            f"GPU-Ziel: {gpu}%\n\n"
            "Die Werte werden als Software-Duty-Cycle umgesetzt.\n"
            "Laufende Miner werden beim Anwenden kontrolliert neu gestartet.\n"
            f"Beschreibung: {description}"
        )
        if hasattr(self, "profile_preview_text"):
            self._set_text(self.profile_preview_text, text)

    def prepare_logicoin_gpu_miner_profile(self) -> None:
        """
        v0.12.15.3:
        GPU-Testminer-Profil nutzt nach EXE-Build automatisch LogicoinGpuMiner.exe.
        Auto-Backend fällt ohne CUDA-Worker sauber auf CPU-Fallback zurück.
        """
        self.coin_algo_var.set("External Miner / Custom")

        # In der EXE-Version existiert normalerweise LogicoinGpuMiner.exe.
        # In der Python-Version existiert logicoin_gpu_miner.py.
        if (BASE_DIR / "LogicoinGpuMiner.exe").exists():
            self.external_path_var.set("LogicoinGpuMiner.exe")
        elif (BASE_DIR / "dist" / "LogicoinGpuMiner.exe").exists():
            self.external_path_var.set(str(BASE_DIR / "dist" / "LogicoinGpuMiner.exe"))
        else:
            self.external_path_var.set("logicoin_gpu_miner.py")

        self.external_args_var.set(
            "--backend auto "
            f"--node-url {self.node_url()} "
            f"--miner-address {self.miner_address_var.get().strip() or DEFAULT_MINER_ADDRESS} "
            "--device 0 --batch-nonces 262144"
        )
        self.mining_power_profile_var.set("high")
        self.cpu_usage_percent_var.set(10)
        self.gpu_usage_percent_var.set(85)
        self.optimization_mode_var.set("auto_safe")
        self.manual_miner_console_var.set(True)
        self.save_current_settings()
        self.refresh_profile_preview()
        self.status_var.set("LOGIC GPU-Testminer vorbereitet: auto = CUDA wenn vorhanden, sonst CPU-Fallback.")

    def open_cpu_miner_console(self) -> None:
        """
        Öffnet nicht nochmal, wenn der interne CPU-Miner schon läuft.
        Stattdessen ist dieser Button jetzt identisch mit "Internen CPU-Miner starten".
        """
        if self.is_cpu_miner_running():
            self.status_var.set("Interner CPU-Miner läuft bereits. Keine zweite Instanz gestartet.")
            messagebox.showinfo(
                "Interner CPU-Miner läuft bereits",
                "Der Interner CPU-Miner läuft bereits.\\n\\n"
                "Es wird KEINE zweite Instanz gestartet.\\n"
                "Wenn du neu starten willst: erst CPU-Miner stoppen, dann wieder starten."
            )
            return

        self.start_cpu_miner()

    def open_external_miner_console(self) -> None:
        self.save_current_settings()
        path = self.external_path_var.get().strip()
        args = self.external_args_var.get().strip()
        if not path:
            messagebox.showerror("Externer Miner", "Miner-Datei nicht angegeben.")
            return

        try:
            env = dict(os.environ)
            env["LOGIC_MINING_POWER_PROFILE"] = self.mining_power_profile_var.get()
            env["LOGIC_CPU_USAGE_PERCENT"] = str(int(float(self.cpu_usage_percent_var.get())))
            env["LOGIC_GPU_USAGE_PERCENT"] = str(int(float(self.gpu_usage_percent_var.get())))
            env["LOGIC_MINING_OPTIMIZATION_MODE"] = self.optimization_mode_var.get()

            cmd, cwd = build_external_miner_command(path, args)

            if os.name == "nt" and self.manual_miner_console_var.get():
                subprocess.Popen(
                    cmd,
                    cwd=str(cwd),
                    env=env,
                    creationflags=creationflags_new_console()
                )
            else:
                subprocess.Popen(cmd, cwd=str(cwd), env=env)

            self.status_var.set("Externer Miner wurde manuell geöffnet.")
        except Exception as e:
            messagebox.showerror(
                "Externen Miner manuell öffnen",
                f"Externer Miner konnte nicht manuell geöffnet werden.\n\n{e}"
            )

    def start_node(self) -> None:
        self.ensure_current_node_async(force_start=True)

    def ensure_current_node_async(self, force_start: bool = True) -> None:
        if self.node_repair_in_progress:
            self.status_var.set("Node-Prüfung läuft bereits ...")
            return
        self.node_repair_in_progress = True
        self.status_var.set("Prüfe Node-Version ...")
        threading.Thread(target=self._ensure_current_node_worker, args=(force_start,), daemon=True).start()

    def _ensure_current_node_worker(self, force_start: bool) -> None:
        url = self.node_url()
        local = is_local_node_url(url)
        try:
            info = get_json(url + "/info", timeout=2)
        except Exception:
            info = None
        if info is not None:
            node_version = str(info.get("version", ""))
            if node_version == APP_VERSION:
                self.after(0, lambda data=info: self._mark_current_node_ready(data))
                return
            if not local:
                self.after(0, lambda v=node_version: self._finish_node_repair_error(f"Der entfernte Node nutzt v{v}, die App v{APP_VERSION}.\nEin entfernter Node wird nicht automatisch beendet."))
                return
            self.after(0, lambda v=node_version: self.status_var.set(f"Alter lokaler Node v{v} erkannt – ersetze durch v{APP_VERSION} ..."))
            stopped, reason = self._terminate_local_logicoin_node()
            if not stopped:
                self.after(0, lambda msg=reason: self._finish_node_repair_error(msg))
                return
            for _ in range(20):
                try:
                    get_json(url + "/info", timeout=0.3)
                except Exception:
                    break
                time.sleep(0.15)
        elif local:
            # Falls ein abgestürzter/hängender alter Node den Port belegt,
            # aber /info nicht mehr beantwortet, räumen wir ihn ebenfalls auf.
            stopped, reason = self._terminate_local_logicoin_node()
            if not stopped:
                self.after(0, lambda msg=reason: self._finish_node_repair_error(msg))
                return

        if force_start:
            self.after(0, self._start_current_node_process)
        else:
            self.after(0, self._finish_node_repair_without_start)

    def _mark_current_node_ready(self, info: Dict[str, Any]) -> None:
        version = str(info.get("version", APP_VERSION))
        self.connected_node_version = version
        self.node_version_badge_var.set(f"App v{APP_VERSION} | Node v{version} ✓")
        self.status_var.set(f"Node ist aktuell: v{version} | Höhe #{info.get('height', '-')}")
        self.node_repair_in_progress = False
        self.update_process_state()
        self.dash_node_value_var.set(f"v{version} online")
        self.refresh_all_async()

    def _finish_node_repair_without_start(self) -> None:
        self.node_repair_in_progress = False
        self.connected_node_version = ""
        self.node_version_badge_var.set(f"App v{APP_VERSION} | Node aus")
        self.status_var.set("Kein Node aktiv.")

    def _finish_node_repair_error(self, message: str) -> None:
        self.node_repair_in_progress = False
        self.node_version_badge_var.set(f"App v{APP_VERSION} | Node-Fehler")
        self.status_var.set("Node konnte nicht automatisch aktualisiert werden.")
        messagebox.showerror("Node-Version", message)

    def _start_current_node_process(self) -> None:
        if self.node_process is not None and self.node_process.poll() is None:
            self.node_repair_in_progress = False
            self.status_var.set("Der aktuelle Node-Prozess läuft bereits.")
            return
        try:
            cmd = app_command_for_role("node")
            self.node_log_handle = open(NODE_LOG_FILE, "a", encoding="utf-8", buffering=1)
            self.node_log_handle.write("\n" + "=" * 70 + "\n" + f"NODE-START v{APP_VERSION} | {time.strftime('%d.%m.%Y %H:%M:%S')}\n" + f"Befehl: {cmd}\n" + "Verwalteter Node stammt aus derselben Control-Center-Version.\n" + "=" * 70 + "\n")
            self.node_log_handle.flush()
            self.node_process = subprocess.Popen(cmd, cwd=str(BASE_DIR), stdout=self.node_log_handle, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, creationflags=creationflags_no_window())
            self.node_verify_attempt = 0
            self.status_var.set(f"Node v{APP_VERSION} wird gestartet ...")
            self.update_process_state()
            self.after(350, self._verify_node_start)
        except Exception as exc:
            if self.node_log_handle:
                try:
                    self.node_log_handle.close()
                except Exception:
                    pass
                self.node_log_handle = None
            self.node_process = None
            self.node_repair_in_progress = False
            messagebox.showerror("Node Start Fehler", str(exc))
            self.update_process_state()

    def _verify_node_start(self) -> None:
        process = self.node_process
        if process is None:
            self.node_repair_in_progress = False
            return
        return_code = process.poll()
        if return_code is not None:
            self.node_process = None
            if self.node_log_handle:
                try:
                    self.node_log_handle.close()
                except Exception:
                    pass
                self.node_log_handle = None
            try:
                log_text = NODE_LOG_FILE.read_text(encoding="utf-8", errors="replace")
                log_tail = log_text[-3500:]
            except Exception as exc:
                log_tail = f"Node-Log konnte nicht gelesen werden: {exc}"
            self.node_repair_in_progress = False
            self.status_var.set(f"Node-Start fehlgeschlagen, Fehlercode {return_code}.")
            self.update_process_state()
            messagebox.showerror("Node Start Fehler", "Der Node-Prozess wurde direkt nach dem Start beendet.\n\n" + f"Fehlercode: {return_code}\n\nLetzte Log-Ausgabe:\n{log_tail}")
            return
        try:
            info = get_json(self.node_url() + "/info", timeout=1)
            node_version = str(info.get("version", ""))
            if node_version != APP_VERSION:
                try:
                    process.terminate()
                except Exception:
                    pass
                self.node_repair_in_progress = False
                self.node_version_badge_var.set(f"App v{APP_VERSION} | falscher Node v{node_version}")
                messagebox.showerror("Node-Version", f"Gestarteter Node ist v{node_version}, erwartet wird v{APP_VERSION}.")
                return
            self.connected_node_version = node_version
            self.node_repair_in_progress = False
            self.node_version_badge_var.set(f"App v{APP_VERSION} | Node v{node_version} ✓")
            self.status_var.set(f"Node v{node_version} läuft.")
            self.update_process_state()
            self.refresh_all_async()
            return
        except Exception:
            self.node_verify_attempt += 1
            if self.node_verify_attempt < 18:
                self.after(300, self._verify_node_start)
                return
        self.node_repair_in_progress = False
        self.status_var.set("Node-Prozess läuft, aber /info antwortet nicht.")

        try:
            log_text = NODE_LOG_FILE.read_text(
                encoding="utf-8",
                errors="replace",
            )
            log_tail = log_text[-3500:]
        except Exception as exc:
            log_tail = (
                "Node-Log konnte nicht gelesen werden: "
                f"{exc}"
            )

        messagebox.showerror(
            "Node Start Fehler",
            (
                "Der Node-Prozess läuft, aber der lokale /info-Endpunkt "
                "antwortet nach mehreren Versuchen nicht.\n\n"
                "Letzte Node-Log-Ausgabe:\n"
                f"{log_tail}"
            ),
        )

    def _terminate_local_logicoin_node(self) -> tuple[bool, str]:
        pids: set[int] = set()
        try:
            if NODE_IDENTITY_FILE.exists():
                identity = json.loads(
                    NODE_IDENTITY_FILE.read_text(
                        encoding="utf-8"
                    )
                )
                pid = int(identity.get("pid", 0))

                if pid > 0 and pid != os.getpid():
                    process_exists = True

                    if os.name == "nt":
                        check = subprocess.run(
                            [
                                "powershell",
                                "-NoProfile",
                                "-Command",
                                (
                                    f"if (Get-Process -Id {pid} "
                                    "-ErrorAction SilentlyContinue) "
                                    "{ exit 0 } else { exit 1 }"
                                ),
                            ],
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                            creationflags=creationflags_no_window(),
                            timeout=4,
                        )
                        process_exists = (
                            check.returncode == 0
                        )

                    if process_exists:
                        pids.add(pid)
                    else:
                        # Veraltete Identity-Datei darf den freien
                        # Node-Port nicht mehr blockieren.
                        NODE_IDENTITY_FILE.unlink(
                            missing_ok=True
                        )
        except Exception:
            pass
        if os.name == "nt":
            port = node_port_from_url(self.node_url())
            try:
                command = f"(Get-NetTCPConnection -LocalPort {port} -State Listen -ErrorAction SilentlyContinue).OwningProcess"
                output = subprocess.check_output(["powershell", "-NoProfile", "-Command", command], text=True, stderr=subprocess.DEVNULL, creationflags=creationflags_no_window(), timeout=5)
                for token in output.replace("\r", "\n").split():
                    try:
                        pid = int(token)
                        if pid > 0 and pid != os.getpid():
                            pids.add(pid)
                    except Exception:
                        pass
            except Exception:
                pass
        if not pids:
            return True, "Kein alter lokaler Node-Prozess gefunden."
        killed = 0
        blocked: list[str] = []
        for pid in sorted(pids):
            image = ""
            command_line = ""
            if os.name == "nt":
                try:
                    raw = subprocess.check_output(
                        [
                            "tasklist",
                            "/FI",
                            f"PID eq {pid}",
                            "/FO",
                            "CSV",
                            "/NH",
                        ],
                        text=True,
                        stderr=subprocess.DEVNULL,
                        creationflags=creationflags_no_window(),
                        timeout=4,
                    ).strip()
                    upper_raw = raw.upper()

                    if (
                        raw
                        and not upper_raw.startswith("INFO:")
                        and not upper_raw.startswith("INFORMATION:")
                        and raw.startswith('"')
                    ):
                        row = next(
                            csv.reader(
                                io.StringIO(raw)
                            )
                        )
                        image = row[0] if row else ""
                except Exception:
                    pass
                if image.lower() in {"python.exe", "pythonw.exe"}:
                    try:
                        command_line = subprocess.check_output(["powershell", "-NoProfile", "-Command", f"(Get-CimInstance Win32_Process -Filter \"ProcessId={pid}\").CommandLine"], text=True, stderr=subprocess.DEVNULL, creationflags=creationflags_no_window(), timeout=4)
                    except Exception:
                        command_line = ""
                allowed = image.lower() in {"logicoinnode.exe", "logicoincontrolcenter.exe"} or (image.lower() in {"python.exe", "pythonw.exe"} and "logicoin_node" in command_line.lower())
                if not allowed:
                    blocked.append(f"PID {pid} ({image or 'unbekannt'}) belegt den Node-Port.")
                    continue
                result = subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)], capture_output=True, text=True, creationflags=creationflags_no_window(), timeout=6)
                if result.returncode == 0:
                    killed += 1
                else:
                    blocked.append((result.stderr or result.stdout or f"PID {pid} konnte nicht beendet werden.").strip())
            else:
                try:
                    os.kill(pid, 15)
                    killed += 1
                except Exception as exc:
                    blocked.append(str(exc))
        if blocked and killed == 0:
            return False, "Der alte Node konnte nicht automatisch ersetzt werden.\n\n" + "\n".join(blocked)
        return True, f"{killed} alter Node-Prozess beendet."

    def stop_node(self) -> None:
        stopped = False
        if self.node_process is not None and self.node_process.poll() is None:
            try:
                self.node_process.terminate()
                self.node_process.wait(timeout=3)
            except Exception:
                try:
                    self.node_process.kill()
                except Exception:
                    pass
            stopped = True
        self.node_process = None
        if not stopped and is_local_node_url(self.node_url()):
            stopped, _reason = self._terminate_local_logicoin_node()
        if self.node_log_handle:
            try:
                self.node_log_handle.close()
            except Exception:
                pass
            self.node_log_handle = None
        self.node_repair_in_progress = False
        self.node_version_badge_var.set(f"App v{APP_VERSION} | Node aus")
        self.status_var.set("Node beendet." if stopped else "Kein Node-Prozess gefunden.")
        self.update_process_state()

    def is_cpu_miner_running(self) -> bool:
        return self.cpu_miner_process is not None and self.cpu_miner_process.poll() is None

    def start_cpu_miner(self) -> None:
        """
        v0.12.15.3:
        CPU-Mining kann sichtbar oder im kontrollierten Hintergrund laufen.
        """
        if not self.ensure_public_wallet_for_mining():
            return

        self.save_current_settings()

        if self.coin_algo_var.get() != "LOGIC / LogicHash CPU":
            messagebox.showinfo(
                "Hinweis",
                "Der integrierte Miner unterstützt aktuell LOGIC / LogicHash CPU. "
                "Für GPU/andere Algorithmen nutze den externen Miner-Bereich."
            )
            return

        if self.is_cpu_miner_running():
            self.status_var.set("Interner CPU-Miner läuft bereits. Keine zweite Instanz gestartet.")
            messagebox.showinfo(
                "Interner CPU-Miner läuft bereits",
                "Der interne CPU-Miner läuft bereits.\n\n"
                "Es wird KEINE zweite Miner-Instanz gestartet.\n\n"
                "Wenn du den Modus wechseln willst: erst CPU-Miner stoppen, "
                "dann Sichtbar/Hintergrund wählen und erneut starten."
            )
            return

        try:
            args = [
                "--node-url", self.node_url(),
                "--miner-address", self.miner_address_var.get().strip() or DEFAULT_MINER_ADDRESS,
                "--max-stale-retries", "3",
                "--cpu-power-profile", self.mining_power_profile_var.get(),
                "--cpu-percent", str(int(float(self.cpu_usage_percent_var.get()))),
            ]

            mode = self.internal_cpu_miner_mode_var.get().strip().lower()

            if mode == "background":
                self.cpu_miner_log_handle = open(CPU_MINER_LOG_FILE, "a", encoding="utf-8", buffering=1)
                cmd = app_command_for_role("cpu-miner", args)
                self.cpu_miner_process = subprocess.Popen(
                    cmd,
                    cwd=str(BASE_DIR),
                    stdout=self.cpu_miner_log_handle,
                    stderr=subprocess.STDOUT,
                    stdin=subprocess.DEVNULL,
                    creationflags=creationflags_no_window()
                )
                self.status_var.set("Interner LOGIC CPU-Miner läuft im Hintergrund. Logs sind in der App sichtbar.")
            else:
                cmd = cpu_miner_console_command(args)
                self.cpu_miner_process = run_command_in_visible_console(cmd, "Logicoin Internal CPU Miner v0.12.15.3")
                self.status_var.set("Interner LOGIC CPU-Miner wurde als sichtbares externes Programm gestartet.")

            self.update_process_state()
        except Exception as e:
            messagebox.showerror(
                "Interner CPU-Miner Start Fehler",
                f"Interner CPU-Miner konnte nicht gestartet werden.\n\n{e}"
            )

    def stop_cpu_miner(self) -> None:
        if self.cpu_miner_process is not None and self.cpu_miner_process.poll() is None:
            try:
                self.cpu_miner_process.terminate()
                self.status_var.set("CPU-Miner wird beendet.")
                time.sleep(0.3)
            except Exception:
                pass

        self.cpu_miner_process = None

        if self.cpu_miner_log_handle:
            try:
                self.cpu_miner_log_handle.close()
            except Exception:
                pass
            self.cpu_miner_log_handle = None

        self.update_process_state()

    def start_config_editor(self) -> None:
        self._start_script("logicoin_config_editor.py")

    def _start_script(self, script_name: str) -> None:
        try:
            if script_name == "logicoin_config_editor.py":
                cmd = app_command_for_role("config")
            else:
                script = BASE_DIR / script_name
                if not script.exists():
                    messagebox.showerror("Fehler", f"{script_name} nicht gefunden.")
                    return
                cmd = [sys.executable, str(script)]

            subprocess.Popen(cmd, cwd=str(BASE_DIR), creationflags=creationflags_no_window())
        except Exception as e:
            messagebox.showerror("Start Fehler", str(e))

    def browse_external_miner(self) -> None:
        path = filedialog.askopenfilename(title="Miner .exe auswählen", filetypes=[("EXE Dateien", "*.exe"), ("Alle Dateien", "*.*")])
        if path:
            self.external_path_var.set(path)
            self.save_current_settings()

    def start_external_miner(self) -> None:
        self.save_current_settings()
        if self.external_miner_process is not None and self.external_miner_process.poll() is None:
            self.status_var.set("Externer Miner läuft bereits.")
            return
        path = self.external_path_var.get().strip()
        args = self.external_args_var.get().strip()
        if not path:
            messagebox.showerror("Externer Miner", "Miner-Datei nicht angegeben.")
            return
        try:
            mode = self.optimization_mode_var.get()
            if mode == "manual_afterburner":
                if not messagebox.askyesno(
                    "Manual / Afterburner Modus",
                    "Manual-Modus ist aktiv.\n\n"
                    "Die LOGIC-App verändert keine GPU-Takte, Spannung oder Power-Limits.\n"
                    "OC/UV machst du selbst mit MSI Afterburner.\n\n"
                    "Externen Miner jetzt starten?"
                ):
                    return

            cmd, cwd = build_external_miner_command(path, args)
            env = dict(os.environ)
            env["LOGIC_MINING_POWER_PROFILE"] = self.mining_power_profile_var.get()
            env["LOGIC_CPU_USAGE_PERCENT"] = str(int(float(self.cpu_usage_percent_var.get())))
            env["LOGIC_GPU_USAGE_PERCENT"] = str(int(float(self.gpu_usage_percent_var.get())))
            env["LOGIC_MINING_OPTIMIZATION_MODE"] = mode
            env["LOGIC_GPU_AUTO_INTENSITY"] = "1" if self.gpu_auto_intensity_var.get() else "0"
            env["LOGIC_GPU_CUSTOM_INTENSITY"] = str(int(self.gpu_custom_intensity_var.get()))
            env["LOGIC_GPU_CUSTOM_WORKSIZE"] = str(int(self.gpu_custom_worksize_var.get()))
            env["LOGIC_GPU_TEMP_SAFETY"] = "1" if self.gpu_temp_safety_var.get() else "0"
            env["LOGIC_GPU_TEMP_LIMIT_C"] = str(int(self.gpu_temp_limit_var.get()))
            env["LOGIC_GPU_ALLOW_POWER_LIMIT_CONTROL"] = "1" if self.gpu_power_limit_control_var.get() else "0"
            env["LOGIC_GPU_ALLOW_CLOCK_CONTROL"] = "1" if self.gpu_clock_control_var.get() else "0"

            self.external_miner_log_handle = open(EXTERNAL_MINER_LOG_FILE, "a", encoding="utf-8", buffering=1)
            self.external_miner_process = subprocess.Popen(cmd, cwd=str(cwd),
                stdout=self.external_miner_log_handle, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                env=env,
                creationflags=creationflags_no_window())
            self.status_var.set("Externer Miner wurde gestartet.")
            self.update_process_state()
        except Exception as e:
            messagebox.showerror("Externer Miner Fehler", str(e))

    def stop_external_miner(self) -> None:
        if self.external_miner_process is not None and self.external_miner_process.poll() is None:
            self.external_miner_process.terminate()
            self.status_var.set("Externer Miner wird beendet.")
            time.sleep(0.3)
        self.external_miner_process = None
        if self.external_miner_log_handle:
            try: self.external_miner_log_handle.close()
            except Exception: pass
            self.external_miner_log_handle = None
        self.update_process_state()

    def show_public_testnet_first_run_notice(self) -> None:
        if self.first_run_wallet_created and self.wallet:
            messagebox.showinfo(
                "Public Testnet RC1",
                (
                    f"Eine neue signierte Wallet wurde erstellt:\n"
                    f"{self.wallet.get('address')}\n\n"
                    "Bitte im Wallet-Tab sofort sichern.\n\n"
                    "LOGIC-Testcoins besitzen keinen garantierten Wert."
                ),
            )
            return

        if self.incompatible_wallet_present:
            messagebox.showwarning(
                "Inkompatible Wallet",
                (
                    "Im Ordner liegt eine Wallet aus einem anderen "
                    "Logicoin-Netzwerk. Sie wurde nicht überschrieben.\n\n"
                    "Bitte die alte Datei sichern oder verschieben und "
                    "für Public Testnet RC1 eine neue Wallet erstellen."
                ),
            )

    def ensure_public_wallet_for_mining(self) -> bool:
        self.wallet = load_wallet()

        if not self.wallet:
            messagebox.showerror(
                "Wallet erforderlich",
                (
                    "Mining ist im Public Testnet nur mit einer gültigen "
                    "signierten Wallet für dieses Netzwerk erlaubt.\n\n"
                    "Erstelle oder stelle zuerst eine Wallet im Wallet-Tab her."
                ),
            )
            return False

        address = str(self.wallet.get("address", "")).strip()
        if not address:
            messagebox.showerror(
                "Wallet ungültig",
                "Die Wallet enthält keine gültige Adresse.",
            )
            return False

        self.wallet_address_var.set(address)
        self.miner_address_var.set(address)
        self.settings["miner_address"] = address
        save_json_file(SETTINGS_FILE, self.settings)
        return True

    # Public-Testnet Release / Backup / Diagnose

    def backup_wallet_ui(self) -> None:
        try:
            result = backup_wallet()
            messagebox.showinfo(
                "Wallet gesichert",
                (
                    "Wallet-Backup erstellt:\n"
                    f"{result}\n\n"
                    "Die Backup-Datei enthält den Private Key "
                    "und muss geheim bleiben."
                ),
            )
        except Exception as exc:
            messagebox.showerror(
                "Wallet-Backup",
                str(exc),
            )

    def restore_wallet_ui(self) -> None:
        selected = filedialog.askopenfilename(
            parent=self,
            title="LOGIC Wallet-Backup auswählen",
            filetypes=[
                ("JSON Wallet", "*.json"),
                ("Alle Dateien", "*.*"),
            ],
        )
        if not selected:
            return

        if not messagebox.askyesno(
            "Wallet wiederherstellen",
            (
                "Die aktuelle Wallet wird vorher automatisch gesichert.\n"
                "Ausgewähltes Backup wiederherstellen?"
            ),
        ):
            return

        try:
            result = restore_wallet(
                Path(selected).resolve()
            )
            self.refresh_wallet()
            messagebox.showinfo(
                "Wallet wiederhergestellt",
                f"Wallet geladen:\n{result}",
            )
        except Exception as exc:
            messagebox.showerror(
                "Wallet-Wiederherstellung",
                str(exc),
            )

    def export_diagnostics_ui(self) -> None:
        try:
            result = export_diagnostics()
            messagebox.showinfo(
                "Diagnoseexport",
                (
                    "Diagnosepaket erstellt:\n"
                    f"{result}\n\n"
                    "Private Wallet-Schlüssel werden nicht exportiert."
                ),
            )
        except Exception as exc:
            messagebox.showerror(
                "Diagnoseexport",
                str(exc),
            )

    def run_release_readiness_ui(self) -> None:
        try:
            report = readiness_report()
            lines = [
                f"{NETWORK_NAME}",
                f"Version: {APP_VERSION}",
                "",
            ]

            for item in report.get("checks", []):
                marker = "OK" if item.get("ok") else "WARN/FEHLER"
                lines.append(
                    f"[{marker}] {item.get('name')}: "
                    f"{item.get('detail')}"
                )

            lines.extend([
                "",
                (
                    "Kritische Prüfungen bestanden."
                    if report.get("ok")
                    else "Kritische Prüfungen nicht bestanden."
                ),
            ])

            text = "\n".join(lines)
            if hasattr(self, "release_tools_text"):
                self._set_text(
                    self.release_tools_text,
                    text,
                )

            if report.get("ok"):
                messagebox.showinfo(
                    "Release-Readiness",
                    "Kritische Prüfungen bestanden.",
                )
            else:
                messagebox.showwarning(
                    "Release-Readiness",
                    "Mindestens eine kritische Prüfung ist fehlgeschlagen.",
                )
        except Exception as exc:
            messagebox.showerror(
                "Release-Readiness",
                str(exc),
            )

    def open_public_network_json_ui(self) -> None:
        path = BASE_DIR / "logicoin_public_network.json"

        if not path.exists():
            messagebox.showerror(
                "Public-Network-JSON",
                f"Datei fehlt:\n{path}",
            )
            return

        try:
            os.startfile(str(path))
        except AttributeError:
            subprocess.Popen(
                ["xdg-open", str(path)]
            )
        except Exception as exc:
            messagebox.showerror(
                "Public-Network-JSON",
                str(exc),
            )

    def open_release_folder_ui(self) -> None:
        release_dir = BASE_DIR / "release"
        release_dir.mkdir(
            parents=True,
            exist_ok=True,
        )

        try:
            os.startfile(str(release_dir))
        except AttributeError:
            subprocess.Popen(
                ["xdg-open", str(release_dir)]
            )
        except Exception as exc:
            messagebox.showerror(
                "Release-Ordner",
                str(exc),
            )

    # Wallet

    def create_new_wallet(self) -> None:
        self.wallet = generate_wallet()
        save_wallet(self.wallet)
        self.wallet_address_var.set(self.wallet["address"])
        self.miner_address_var.set(self.wallet["address"])
        self.refresh_wallet()
        messagebox.showinfo(
            "Wallet",
            (
                f"Neue Public-Testnet-Wallet erstellt:\n"
                f"{self.wallet['address']}\n\n"
                "Bitte jetzt sofort über 'Wallet sichern' sichern."
            ),
        )

    def use_mining_wallet(self) -> None:
        self.wallet = mining_test_wallet()
        save_wallet(self.wallet)
        self.wallet_address_var.set(self.wallet["address"])
        self.miner_address_var.set(self.wallet["address"])
        self.refresh_wallet()
        messagebox.showinfo(
            "Wallet",
            (
                f"Neue signierte Mining-Wallet aktiviert:\n"
                f"{self.wallet['address']}\n\n"
                "Bitte jetzt sofort sichern."
            ),
        )

    def refresh_wallet(self) -> None:
        self.wallet = load_wallet()
        if self.wallet:
            self.wallet_address_var.set(self.wallet.get("address", "-"))
            self.refresh_wallet_balance()
        else:
            self._set_text(self.wallet_info_text, "Keine Wallet vorhanden. Erstelle eine neue Wallet oder nutze die Mining-Testwallet.")

    def use_wallet_as_miner_address(self) -> None:
        if not self.wallet:
            self.refresh_wallet()
        if self.wallet:
            self.miner_address_var.set(self.wallet["address"])
            self.save_current_settings()

    def refresh_wallet_balance(self) -> None:
        if not self.wallet:
            self.dash_wallet_balance_var.set("Keine Wallet")
            self.dash_wallet_value_var.set("0,00 € | 0,00 $")
            self.dash_mined_value_var.set("0 LOGIC gemint")
            if hasattr(self, "wallet_info_text"):
                self._set_text(self.wallet_info_text, "Keine Wallet vorhanden.")
            return

        try:
            address = self.wallet["address"]
            encoded = urllib.parse.quote(address)
            data = get_json(self.node_url() + f"/address?address={encoded}")
            info = data.get("address_info", {})

            confirmed = float(info.get("confirmed_balance") or 0.0)
            spendable = float(info.get("spendable_balance") or 0.0)
            mined_total = float(info.get("mined_total") or 0.0)
            mined_rewards = float(info.get("mined_rewards") or 0.0)
            mined_fees = float(info.get("mined_fees") or 0.0)
            mined_blocks_count = int(info.get("mined_blocks") or 0)

            eur_rate = max(0.0, float(self.logic_test_rate_eur_var.get()))
            usd_rate = max(0.0, float(self.logic_test_rate_usd_var.get()))
            confirmed_eur = confirmed * eur_rate
            confirmed_usd = confirmed * usd_rate
            mined_eur = mined_total * eur_rate
            mined_usd = mined_total * usd_rate

            self.dash_wallet_balance_var.set(f"{fmt_amount(confirmed)} LOGIC")
            self.dash_wallet_value_var.set(f"{confirmed_eur:,.2f} € | {confirmed_usd:,.2f} $")
            self.dash_mined_value_var.set(
                f"{fmt_amount(mined_total)} LOGIC | {mined_eur:,.2f} € / {mined_usd:,.2f} $"
            )

            lines = [
                f"Wallet-Datei: {WALLET_FILE}",
                f"Adresse: {address}",
                f"Bestätigt: {fmt_amount(confirmed)} {TICKER}",
                f"Spendable: {fmt_amount(spendable)} {TICKER}",
                f"Pending rein: {fmt_amount(info.get('pending_in'))} {TICKER}",
                f"Pending raus: {fmt_amount(info.get('pending_out'))} {TICKER}",
                f"Pending Fees: {fmt_amount(info.get('pending_fees'))} {TICKER}",
                "",
                f"Geminte Blöcke: {mined_blocks_count}",
                f"Mining-Rewards: {fmt_amount(mined_rewards)} {TICKER}",
                f"Mining-Fees: {fmt_amount(mined_fees)} {TICKER}",
                f"Gemint gesamt: {fmt_amount(mined_total)} {TICKER}",
                "",
                f"Manueller Testkurs: 1 LOGIC = {eur_rate:.8f} EUR / {usd_rate:.8f} USD",
                f"Wallet-Testwert: {confirmed_eur:,.2f} EUR / {confirmed_usd:,.2f} USD",
                f"Mining-Testwert: {mined_eur:,.2f} EUR / {mined_usd:,.2f} USD",
                "Hinweis: Noch kein echter Marktpreis.",
                "",
                f"Nächster Nonce: {info.get('next_nonce')}",
                f"Netzwerk: {self.wallet.get('network_id', '-')}",
                f"Signatur: {'aktiv' if self.wallet.get('private_key') and self.wallet.get('public_key') else 'nicht verfügbar'}",
                f"Public Key: {self.wallet.get('public_key', '-')}",
            ]
            if hasattr(self, "wallet_info_text"):
                self._set_text(self.wallet_info_text, "\n".join(lines))
        except Exception as exc:
            if hasattr(self, "wallet_info_text"):
                self._set_text(self.wallet_info_text, f"Balance konnte nicht geladen werden:\n{exc}")

    def send_logic_transaction(self) -> None:
        if not self.wallet:
            messagebox.showerror("Wallet", "Keine Wallet vorhanden.")
            return
        try:
            from_addr = self.wallet["address"]
            encoded = urllib.parse.quote(from_addr)
            data = get_json(self.node_url() + f"/address?address={encoded}")
            info = data.get("address_info", {})
            nonce = int(info.get("next_nonce", 0))

            to_addr = self.send_to_var.get().strip()
            amount = float(self.send_amount_var.get().replace(",", "."))
            fee = float(self.send_fee_var.get().replace(",", "."))
            memo = self.send_memo_var.get().strip()

            private_key = self.wallet.get("private_key")
            public_key = self.wallet.get("public_key", "")

            if not private_key and from_addr != DEFAULT_MINER_ADDRESS:
                messagebox.showerror(
                    "Wallet nicht signierfähig",
                    "Diese Wallet hat keinen Private Key.\n"
                    "Erstelle in v0.10 eine neue signierte Wallet."
                )
                return

            tx = create_transfer_transaction(
                from_addr,
                to_addr,
                amount,
                fee,
                nonce,
                memo,
                public_key=public_key,
                private_key=private_key,
            )

            sign_status = "signiert" if tx.get("signature") else "Legacy ohne Signatur"
            if not messagebox.askyesno("Transaktion senden", f"{amount} {TICKER} an:\n{to_addr}\n\nFee: {fee} {TICKER}\nTXID: {tx['txid']}\nStatus: {sign_status}\n\nSenden?"):
                return

            response = post_json(self.node_url() + "/submit_tx", tx)
            if response.get("accepted"):
                messagebox.showinfo("Transaktion", "Transaktion wurde in den Mempool aufgenommen.\nMine einen Block, um sie zu bestätigen.")
                self.refresh_all_async()
                self.refresh_wallet_balance()
            else:
                messagebox.showerror("Transaktion abgelehnt", str(response.get("error")))
        except Exception as e:
            messagebox.showerror("Transaktionsfehler", str(e))



    def toggle_earnings_details(self) -> None:
        return

    def _format_probability(self, probability: float) -> str:
        probability = max(0.0, min(1.0, float(probability)))
        if probability >= 0.9999:
            return "99.99%+"
        if probability >= 0.1:
            return f"{probability * 100:.2f}%"
        return f"{probability * 100:.4f}%"

    def _format_blocks_value(self, blocks: float) -> str:
        blocks = max(0.0, float(blocks))
        if blocks >= 100:
            return f"{blocks:,.1f}"
        if blocks >= 1:
            return f"{blocks:,.3f}"
        return f"{blocks:.6f}"

    def _format_earnings_projection(
        self,
        logic_amount: float,
        eur_rate: float,
        usd_rate: float,
        probability: float,
        expected_blocks: float,
    ) -> str:
        amount = max(0.0, float(logic_amount))

        if amount >= 1_000_000:
            logic_text = f"{amount:,.0f} LOGIC"
        elif amount >= 100:
            logic_text = f"{amount:,.2f} LOGIC"
        elif amount >= 1:
            logic_text = f"{amount:,.4f} LOGIC"
        else:
            logic_text = f"{amount:.8f} LOGIC"

        eur = amount * eur_rate
        usd = amount * usd_rate

        return (
            f"{logic_text}\n"
            f"{eur:,.2f} € · {usd:,.2f} $\n"
            f"Chance {self._format_probability(probability)} · "
            f"Blöcke {self._format_blocks_value(expected_blocks)}"
        )

    def update_earnings_estimate(self) -> None:
        params = self.latest_network_params or {}
        difficulty = int(params.get("next_difficulty") or 0)
        difficulty_bits = int(
            params.get(
                "next_difficulty_bits",
                max(0, difficulty) * 4,
            )
        )
        difficulty_rule = str(
            params.get(
                "difficulty_rule",
                "hex-v1",
            )
        )
        reward = float(params.get("block_reward") or 0.0)
        hashrate = max(0.0, float(self.current_total_mining_hashrate_hs))

        eur_rate = max(0.0, float(self.logic_test_rate_eur_var.get()))
        usd_rate = max(0.0, float(self.logic_test_rate_usd_var.get()))
        expected_hashes_per_block = float(
            params.get(
                "expected_hashes_per_block",
                2 ** max(0, difficulty_bits),
            )
        )

        def period(seconds: float) -> tuple[float, float, float]:
            if hashrate <= 0 or reward <= 0 or expected_hashes_per_block <= 0:
                return 0.0, 0.0, 0.0

            expected_blocks = (hashrate * seconds) / expected_hashes_per_block
            probability = (
                1.0
                if expected_blocks >= 700
                else 1.0 - math.exp(-expected_blocks)
            )
            logic_amount = expected_blocks * reward
            return logic_amount, probability, expected_blocks

        h_logic, h_chance, h_blocks = period(3600)
        d_logic, d_chance, d_blocks = period(86400)
        m_logic, m_chance, m_blocks = period(86400 * 30)
        y_logic, y_chance, y_blocks = period(86400 * 365)

        self.earnings_hour_var.set(self._format_earnings_projection(h_logic, eur_rate, usd_rate, h_chance, h_blocks))
        self.earnings_day_var.set(self._format_earnings_projection(d_logic, eur_rate, usd_rate, d_chance, d_blocks))
        self.earnings_month_var.set(self._format_earnings_projection(m_logic, eur_rate, usd_rate, m_chance, m_blocks))
        self.earnings_year_var.set(self._format_earnings_projection(y_logic, eur_rate, usd_rate, y_chance, y_blocks))

        if hashrate <= 0 or reward <= 0:
            self.earnings_summary_var.set("Mining inaktiv – Prognose = 0")
        else:
            expected_seconds_per_block = (
                expected_hashes_per_block / hashrate
                if hashrate > 0
                else 0.0
            )
            risk_text = (
                "hohes Stale-Risiko"
                if expected_seconds_per_block < 5.0
                else "normales Stale-Risiko"
            )
            difficulty_label = (
                f"{difficulty_bits} Bits"
                if difficulty_rule in {"bits-v2", "bits-v3-fast"}
                else f"Diff {difficulty}"
            )
            self.earnings_summary_var.set(
                f"{self.format_hs_ui(hashrate)} | {difficulty_label} | "
                f"Ø {expected_seconds_per_block:.2f}s/Block | {risk_text}"
            )

    def read_cpu_miner_stats(self) -> dict[str, Any]:
        try:
            if not CPU_MINER_STATS_FILE.exists():
                return {}
            return json.loads(CPU_MINER_STATS_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _mining_dashboard_loop(self) -> None:
        try:
            self.refresh_mining_dashboard()
        finally:
            self.after(2500, self._mining_dashboard_loop)

    def stable_cpu_hashrate(
        self,
        stats: dict[str, Any],
    ) -> float:
        for key in (
            "avg_1m_hs",
            "session_avg_hs",
            "current_hashrate_hs",
        ):
            try:
                value = float(
                    stats.get(key) or 0.0
                )
            except Exception:
                value = 0.0

            if value > 0:
                return value

        return 0.0

    def stable_miner_hashrate(
        self,
        stats: dict[str, Any],
    ) -> float:
        """
        Verifizierte End-to-End-Hashrate.

        CUDA-Rohleistung wird absichtlich nicht für Prognosen verwendet.
        Node-Kommunikation, Submit und Jobwechsel sind bereits eingerechnet.
        """
        if str(stats.get("version", "")) != APP_VERSION:
            return 0.0

        if not bool(
            stats.get(
                "proof_search_selftest_ok",
                False,
            )
        ):
            return 0.0

        if (
            str(
                stats.get(
                    "hashrate_measurement",
                    "",
                )
            )
            != "exact_cuda_end_to_end_counter"
        ):
            return 0.0

        if not bool(
            stats.get(
                "measurement_plausible",
                False,
            )
        ):
            return 0.0

        for key in (
            "effective_avg_1m_hs",
            "effective_session_hs",
            "effective_current_hs",
        ):
            try:
                value = float(
                    stats.get(key) or 0.0
                )
            except Exception:
                value = 0.0

            if value > 0:
                return value

        return 0.0

    def _percent_text(self, value: object) -> str:
        try:
            return f"{float(value):.0f}%"
        except Exception:
            return "-"

    def refresh_mining_dashboard(self) -> None:
        self.update_process_state()
        stats_files = self.read_gpu_stats_files()
        stats_by_device: dict[int, dict[str, Any]] = {}
        total_hs = 0.0
        total_accepted = 0
        total_stale = 0
        total_stale_avoided = 0
        total_invalid = 0

        active_devices = [
            device for device, process in self.gpu_miner_processes.items()
            if process is not None and process.poll() is None
        ]

        for stats in stats_files:
            try:
                device = int(stats.get("device", -1))
            except Exception:
                continue
            stats_by_device[device] = stats
            if device in active_devices:
                stable_hs = self.stable_miner_hashrate(
                    stats
                )
                total_hs += stable_hs

                try:
                    total_raw_hs += float(
                        stats.get(
                            "cuda_raw_avg_1m_hs",
                            stats.get(
                                "cuda_raw_current_hs",
                                0.0,
                            ),
                        )
                        or 0.0
                    )
                except Exception:
                    pass

                try:
                    total_accepted_per_minute += float(
                        stats.get(
                            "accepted_per_minute",
                            0.0,
                        )
                        or 0.0
                    )
                except Exception:
                    pass

                if not bool(
                    stats.get(
                        "measurement_plausible",
                        False,
                    )
                ):
                    blocked_measurements += 1

            total_accepted += int(stats.get("accepted") or 0)
            total_stale += int(stats.get("stale") or 0)
            total_stale_avoided += int(stats.get("stale_avoided") or 0)
            total_invalid += int(stats.get("invalid", stats.get("rejected", 0)) or 0)
        self.dash_gpu_miner_value_var.set(
            f"AKTIV • {len(active_devices)} GPU(s)"
            if active_devices else "INAKTIV"
        )
        self.dash_gpu_total_hashrate_var.set(self.format_hs_ui(total_hs))

        cpu_stats = self.read_cpu_miner_stats()
        cpu_running = self.is_cpu_miner_running()
        cpu_hs = (
            self.stable_cpu_hashrate(cpu_stats)
            if cpu_running
            else 0.0
        )
        self.current_total_mining_hashrate_hs = total_hs + cpu_hs
        self.update_earnings_estimate()

        gpus = self.detect_nvidia_gpus()
        measurement_note = (
            f" | Messung gesperrt: {blocked_measurements}"
            if blocked_measurements
            else ""
        )
        lines = [
            f"GPU effektiv Ø1m: {self.format_hs_ui(total_hs)}"
            f" | CUDA roh Ø1m: {self.format_hs_ui(total_raw_hs)}"
            f" | Accepted/min {total_accepted_per_minute:.2f}",
            f"Accepted {total_accepted} | Stale {total_stale} | "
            f"vermieden {total_stale_avoided} | Invalid {total_invalid}"
            f"{measurement_note}",
            "",
        ]
        for gpu in gpus:
            device = int(gpu.get("index", 0))
            stats = stats_by_device.get(device, {})
            running = device in active_devices
            desired = device in self.gpu_miner_desired_devices
            if running:
                state_text = "AKTIV · überwacht"
            elif desired:
                attempts = self.gpu_miner_restart_attempts.get(device, 0)
                state_text = f"NEUSTART LÄUFT · Versuch {attempts}"
            else:
                state_text = "INAKTIV"

            lines.append(
                f"GPU {device} • {gpu.get('name', '-')}"
                f" • {state_text}"
            )
            miner_activity_5s = stats.get("miner_activity_5s_percent")
            miner_activity_60s = stats.get("miner_activity_60s_percent")
            nvml_avg = gpu.get("utilization_avg_5s_percent")
            nvml_max = gpu.get("utilization_max_5s_percent")

            lines.append(
                f"  Effektiv Ø1m "
                f"{self.format_hs_ui(self.stable_miner_hashrate(stats))}"
                f" | Effektiv 5s "
                f"{self.format_hs_ui(stats.get('effective_current_hs'))}"
                f" | CUDA roh "
                f"{self.format_hs_ui(stats.get('cuda_raw_avg_1m_hs'))}"
                f" | Ziel "
                f"{stats.get('target_percent', self.gpu_usage_percent_var.get())}%"
            )
            lines.append(
                f"  Accepted/min "
                f"{float(stats.get('accepted_per_minute') or 0.0):.2f}"
                f" | Proofs {stats.get('proofs_found', 0)}"
                f" | erwartet "
                f"{float(stats.get('expected_proofs_from_work') or 0.0):.2f}"
                f" | Messung "
                f"{stats.get('measurement_plausibility_text', '-')}"
            )
            lines.append(
                f"  Miner aktiv 5s/60s: "
                f"{self._percent_text(miner_activity_5s)} / "
                f"{self._percent_text(miner_activity_60s)}"
                f" | NVML Ø5s/Max: "
                f"{self._percent_text(nvml_avg)} / "
                f"{self._percent_text(nvml_max)}"
            )
            lines.append(
                f"  Accepted {stats.get('accepted', 0)}"
                f" | Stale {stats.get('stale', 0)}"
                f" | vermieden {stats.get('stale_avoided', 0)}"
                f" | Jobwechsel {stats.get('job_refreshes', 0)}"
                f" | Invalid {stats.get('invalid', stats.get('rejected', 0))}"
                f" | {gpu.get('temperature_c', '-')}°C"
                f" | {gpu.get('power_w', '-')} W"
            )
            lines.append("")

        if hasattr(self, "dashboard_gpu_text"):
            self._set_text(self.dashboard_gpu_text, "\n".join(lines))

    def _wallet_dashboard_loop(self) -> None:
        try:
            self.refresh_wallet_balance()
        finally:
            self.after(10000, self._wallet_dashboard_loop)

    def apply_test_rates(self) -> None:
        try:
            self.logic_test_rate_eur_var.set(max(0.0, float(self.logic_test_rate_eur_var.get())))
            self.logic_test_rate_usd_var.set(max(0.0, float(self.logic_test_rate_usd_var.get())))
            self.save_current_settings()
            self.refresh_wallet_balance()
            self.update_earnings_estimate()
            self.status_var.set("Manueller LOGIC-Testkurs gespeichert.")
        except Exception as exc:
            messagebox.showerror("Testkurs", str(exc))

    # Node data

    def open_explorer(self) -> None:
        webbrowser.open(self.node_url() + "/explorer")

    def refresh_all_async(self) -> None:
        threading.Thread(target=self._refresh_all_worker, daemon=True).start()
        self.refresh_hardware_async()
        self.refresh_logs()
        self.update_process_state()
        self.refresh_network_async()

    def _auto_refresh_loop(self) -> None:
        if self.auto_refresh_var.get():
            self.refresh_all_async()
        self.after(5000, self._auto_refresh_loop)

    def _refresh_all_worker(self) -> None:
        try:
            info = get_json(self.node_url() + "/info")
            chain_data = get_json(self.node_url() + "/chain")
            mempool_data = get_json(self.node_url() + "/mempool")
            self.after(0, lambda: self._apply_node_data(info, chain_data, mempool_data))
        except Exception as e:
            self.after(0, lambda error=str(e): self._mark_node_unreachable(error))

    def _mark_node_unreachable(self, error: str) -> None:
        self.connected_node_version = ""
        self.node_version_badge_var.set(f"App v{APP_VERSION} | Node nicht erreichbar")
        self.status_var.set(f"Node nicht erreichbar: {error}")
        self.update_process_state()

    def _apply_node_data(self, info: Dict[str, Any], chain_data: Dict[str, Any], mempool_data: Dict[str, Any]) -> None:
        params = info.get("network_params", {})
        self.latest_network_params = dict(params or {})
        stats = info.get("chain_stats", {})
        diff_stats = info.get("difficulty_stats", {})
        balances = info.get("balances", {})
        chain = chain_data.get("chain", [])
        mempool = mempool_data.get("transactions", [])

        node_version = str(info.get("version", ""))
        self.connected_node_version = node_version
        self.node_version_badge_var.set(f"App v{APP_VERSION} | Node v{node_version}" + (" ✓" if node_version == APP_VERSION else " ⚠"))
        self.status_var.set(f"Verbunden mit {self.node_url()} | Node v{node_version} | Höhe #{info.get('height')} | {time.strftime('%H:%M:%S')}")
        if node_version != APP_VERSION and is_local_node_url(self.node_url()) and not self.node_repair_in_progress:
            self.after(50, lambda: self.ensure_current_node_async(force_start=True))
        self.height_var.set(f"#{info.get('height')}")
        self.dash_height_value_var.set(f"#{info.get('height')}")
        self.dash_node_value_var.set(f"v{node_version} online" if node_version == APP_VERSION else f"v{node_version} → Update")
        self.valid_var.set("gültig" if info.get("chain_valid") else "ungültig")
        if params.get("difficulty_rule") in {"bits-v2", "bits-v3-fast"}:
            self.diff_var.set(
                f"{params.get('next_difficulty_bits')} Bits"
            )
        else:
            self.diff_var.set(
                str(params.get("next_difficulty"))
            )
        self.mempool_count_var.set(f"{info.get('mempool_count')} TXs")
        self.reward_var.set(f"{fmt_amount(params.get('block_reward'))} LOGIC")
        self.tx_count_var.set(str(stats.get("transfer_transactions")))
        self.avg_time_var.set(f"{float(stats.get('average_block_time_seconds', 0.0)):.2f}s")
        self.avg_hashrate_var.set(f"{float(stats.get('average_hashrate_hs', 0.0)):,.0f} H/s")
        self._set_text(self.info_text, "\n".join([
            f"Projekt: {info.get('project')} / {info.get('ticker')}",
            f"Algorithmus: {info.get('algorithm')}",
            f"Chain gültig: {info.get('chain_valid')} - {info.get('chain_status')}",
            f"Tip-Hash: {info.get('tip_hash')}",
            (
                f"Difficulty: aktuell {diff_stats.get('current_difficulty_bits')} Bits "
                f"| nächste {diff_stats.get('next_difficulty_bits')} Bits"
                if diff_stats.get("difficulty_rule") in {"bits-v2", "bits-v3-fast"}
                else
                f"Difficulty: aktuell {diff_stats.get('current_difficulty')} "
                f"| nächste {diff_stats.get('next_difficulty')}"
            ),
            f"Blockreward: {fmt_amount(params.get('block_reward'))} LOGIC",
            f"Min TX Fee: {fmt_amount(params.get('min_tx_fee'))} LOGIC",
            f"Gesamt Fees: {fmt_amount(stats.get('total_fees'))} LOGIC",
        ]))
        self._fill_blocks(chain)
        self._fill_mempool(mempool)
        self._fill_balances(balances)

    # Hardware

    def refresh_hardware_async(self) -> None:
        threading.Thread(target=self._hardware_worker, daemon=True).start()

    def _hardware_worker(self) -> None:
        cpu = self.query_cpu()
        gpus = self.query_nvidia_gpus()
        self.after(0, lambda: self._apply_hardware(cpu, gpus))

    def query_cpu(self) -> Dict[str, str]:
        name = platform.processor() or platform.machine() or "Unbekannte CPU"
        load = "-"

        if os.name == "nt":
            try:
                command = (
                    "$cpu = Get-CimInstance Win32_Processor | "
                    "Select-Object -First 1 Name,LoadPercentage; "
                    "$cpu | ConvertTo-Json -Compress"
                )
                output = subprocess.check_output(
                    [
                        "powershell",
                        "-NoProfile",
                        "-Command",
                        command,
                    ],
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    stderr=subprocess.DEVNULL,
                    creationflags=creationflags_no_window(),
                    timeout=6,
                ).strip()

                data = json.loads(output)
                if isinstance(data, dict):
                    detected_name = str(data.get("Name") or "").strip()
                    detected_load = data.get("LoadPercentage")

                    if detected_name:
                        name = detected_name

                    if detected_load is not None:
                        load = f"{float(detected_load):.0f}%"
            except Exception:
                pass

        return {
            "name": name,
            "load": load,
        }

    def query_nvidia_gpus(self) -> List[Dict[str, str]]:
        rows: list[dict[str, str]] = []
        stats_by_device: dict[int, dict[str, object]] = {}

        for stats in self.read_gpu_stats_files():
            try:
                device = int(stats.get("device", -1))
            except Exception:
                continue
            stats_by_device[device] = stats

        for gpu in self.detect_nvidia_gpus():
            device = int(gpu.get("index", 0))
            name = str(gpu.get("name", "NVIDIA GPU"))
            lower_name = name.lower()
            stats = stats_by_device.get(device, {})

            if "1050" in lower_name or "1060" in lower_name or "gtx" in lower_name:
                profile = "Legacy / Low VRAM"
            elif "rtx" in lower_name:
                profile = "CUDA High"
            else:
                profile = "Auto"

            def number(
                source: dict[str, object],
                key: str,
            ) -> float | None:
                try:
                    value = source.get(key)
                    if value is None:
                        return None
                    return float(value)
                except Exception:
                    return None

            def text_number(
                value: float | None,
                digits: int = 0,
                suffix: str = "",
            ) -> str:
                if value is None:
                    return "-"
                return f"{value:.{digits}f}{suffix}"

            miner_5s = number(stats, "miner_activity_5s_percent")
            miner_60s = number(stats, "miner_activity_60s_percent")
            util_avg = number(gpu, "utilization_avg_5s_percent")
            util_max = number(gpu, "utilization_max_5s_percent")
            temp = number(gpu, "temperature_c")
            power_avg = number(gpu, "power_avg_5s_w")
            power_now = number(gpu, "power_instant_w")
            memory_used = number(gpu, "memory_used_mb")
            memory_total = number(gpu, "memory_total_mb")

            miner_text = (
                f"{text_number(miner_5s, 0, '%')} / "
                f"{text_number(miner_60s, 0, '%')}"
                if miner_5s is not None or miner_60s is not None
                else "-"
            )
            util_text = (
                f"{text_number(util_avg, 0, '%')} / "
                f"{text_number(util_max, 0, '%')}"
            )
            power_text = (
                f"{text_number(power_avg, 1)} / "
                f"{text_number(power_now, 1)} W"
            )
            memory_text = (
                f"{text_number(memory_used, 0)} / "
                f"{text_number(memory_total, 0)} MB"
            )

            rows.append({
                "id": str(device),
                "name": name,
                "profile": profile,
                "miner": miner_text,
                "util": util_text,
                "temp": text_number(temp, 0, "°C"),
                "power": power_text,
                "mem": memory_text,
            })

        return rows

    def _apply_hardware(self, cpu: Dict[str, str], gpus: List[Dict[str, str]]) -> None:
        self.cpu_info_var.set(
            f"CPU: {cpu.get('name')} | Auslastung: {cpu.get('load')}"
        )
        self.gpu_tree.delete(*self.gpu_tree.get_children())
        if not gpus:
            self.gpu_tree.insert(
                "",
                "end",
                values=(
                    "-",
                    "Keine NVIDIA-GPUs über NVML gefunden",
                    "-",
                    "-",
                    "-",
                    "-",
                    "-",
                    "-",
                ),
            )
        for gpu in gpus:
            self.gpu_tree.insert(
                "",
                "end",
                values=(
                    gpu["id"],
                    gpu["name"],
                    gpu["profile"],
                    gpu["miner"],
                    gpu["util"],
                    gpu["temp"],
                    gpu["power"],
                    gpu["mem"],
                ),
            )

        if hasattr(self, "gpu_benchmark_device_combo"):
            values = [f"{gpu['id']}: {gpu['name']}" for gpu in gpus]
            self.gpu_benchmark_device_combo.configure(values=values or ["0"])
            if values and self.gpu_benchmark_device_var.get() not in values:
                self.gpu_benchmark_device_var.set(values[0])

    # Benchmark

    def start_cpu_benchmark_async(self) -> None:
        self.benchmark_var.set("Benchmark läuft...")
        threading.Thread(target=self._cpu_benchmark_worker, daemon=True).start()

    def _cpu_benchmark_worker(self) -> None:
        duration = 10.0
        start = time.time()
        count = 0
        sample = {"bench": "logicoin", "time": start, "nonce": 0}
        while time.time() - start < duration:
            sample["nonce"] = count
            logic_hash_v0(sample)
            count += 1
        elapsed = time.time() - start
        hps = count / elapsed if elapsed > 0 else 0.0
        self.after(0, lambda: self.benchmark_var.set(f"{hps:,.2f} H/s über {elapsed:.2f}s"))

    def find_cuda_worker_for_benchmark(self) -> Path | None:
        names = ["logicoin_cuda_worker.exe", "LogicoinCudaWorker.exe", "logicoin_cuda_worker"]
        directories = [BASE_DIR, BASE_DIR / "dist", BASE_DIR.parent, BASE_DIR.parent / "dist", Path.cwd(), Path.cwd() / "dist"]
        seen: set[str] = set()
        for directory in directories:
            for name in names:
                candidate = directory / name
                key = str(candidate).lower()
                if key in seen:
                    continue
                seen.add(key)
                if candidate.exists():
                    return candidate
        return None

    def _selected_benchmark_device(self) -> int:
        match = re.match(r"\s*(\d+)", self.gpu_benchmark_device_var.get().strip())
        return int(match.group(1)) if match else 0

    def start_gpu_benchmark_async(self, all_devices: bool = False) -> None:
        if self.gpu_benchmark_running:
            messagebox.showinfo("GPU-Benchmark", "Ein GPU-Benchmark läuft bereits.")
            return
        gpus = self.detect_nvidia_gpus()
        if not gpus:
            messagebox.showerror("GPU-Benchmark", "Keine NVIDIA-GPUs über NVML erkannt.")
            return
        devices = [int(g.get("index", 0)) for g in gpus] if all_devices else [self._selected_benchmark_device()]
        duration = max(3, min(60, int(self.gpu_benchmark_duration_var.get())))
        self.gpu_benchmark_running = True
        self.gpu_benchmark_status_var.set(f"100%-Benchmark startet: {len(devices)} GPU(s), je {duration} Sekunden.")
        threading.Thread(target=self._gpu_benchmark_worker, args=(devices, duration), daemon=True).start()

    def _gpu_benchmark_worker(self, devices: list[int], duration: int) -> None:
        worker = self.find_cuda_worker_for_benchmark()
        if worker is None:
            self.after(0, lambda: self._finish_gpu_benchmark_with_error("logicoin_cuda_worker.exe wurde nicht gefunden.\nBitte BUILD_CUDA_WORKER_SAFE.bat ausführen."))
            return

        gpu_map = {int(g.get("index", -1)): g for g in self.detect_nvidia_gpus()}
        results: list[dict[str, Any]] = []

        restore_devices = sorted(self.gpu_miner_desired_devices)
        restore_visible = dict(self.gpu_miner_visible_by_device)
        had_running_gpu_miners = any(
            proc is not None and proc.poll() is None
            for proc in self.gpu_miner_processes.values()
        )

        if had_running_gpu_miners:
            self.after(0, lambda: self.gpu_benchmark_status_var.set("Aktive interne GPU-Miner werden für den 100%-Benchmark kurz pausiert ..."))
            self.after(0, self.stop_gpu_miners)
            time.sleep(1.2)

        for position, device in enumerate(devices, start=1):
            gpu_name = str(gpu_map.get(device, {}).get("name", f"GPU {device}"))
            self.after(
                0,
                lambda d=device, n=gpu_name, p=position, total=len(devices):
                    self.gpu_benchmark_status_var.set(
                        f"100%-Benchmark: GPU {d} – {n} ({p}/{total}) ..."
                    )
            )
            result = self._run_single_gpu_benchmark(worker, device, duration, gpu_name)
            results.append(result)
            self.after(0, lambda r=result: self._upsert_gpu_benchmark_row(r))

        self.gpu_benchmark_results = results
        try:
            GPU_BENCHMARK_FILE.write_text(
                json.dumps(
                    {"version": APP_VERSION, "time": time.strftime("%Y-%m-%dT%H:%M:%S"), "results": results},
                    indent=2,
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
        except Exception:
            pass

        if had_running_gpu_miners and restore_devices:
            def _restore() -> None:
                for dev in restore_devices:
                    self.start_gpu_miner_device(int(dev), visible=False, supervised=True)
                self.status_var.set("GPU-Miner nach Benchmark wieder gestartet.")
            self.after(0, _restore)

        successful = [r for r in results if r.get("ok")]
        if successful:
            best = max(successful, key=lambda r: float(r.get("hashrate_hs", 0.0)))
            summary = (
                f"100%-Benchmark fertig: {len(successful)}/{len(results)} GPU(s). "
                f"Beste: GPU {best.get('device')} mit {self.format_hs_ui(best.get('hashrate_hs'))}."
            )
        else:
            summary = "GPU-Benchmark beendet, aber kein Test war erfolgreich."
        self.after(0, lambda: self.gpu_benchmark_status_var.set(summary))
        self.after(0, lambda: setattr(self, "gpu_benchmark_running", False))

    def _run_single_gpu_benchmark(
        self,
        worker: Path,
        device: int,
        duration: int,
        gpu_name: str,
    ) -> dict[str, Any]:
        base_hash = "000102030405060708090a0b0c0d0e0f101112131415161718191a1b1c1d1e1f"
        batch_count = 4_194_304
        cmd = [
            str(worker),
            "--base-hash", base_hash,
            "--difficulty", "256",
            "--start", "0",
            "--count", str(batch_count),
            "--device", str(device),
            "--benchmark-ms", str(duration * 1000),
        ]

        metric_samples: list[dict[str, float]] = []
        started = time.perf_counter()

        try:
            process = subprocess.Popen(
                cmd,
                cwd=str(BASE_DIR),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                **hidden_subprocess_kwargs(),
            )

            # Sensorwerte kommen direkt aus NVML; kein nvidia-smi-Prozess.
            while process.poll() is None:
                sample = self._gpu_metric_sample(device)
                if sample:
                    metric_samples.append(sample)
                time.sleep(0.8)

            stdout, stderr = process.communicate(timeout=5)
            elapsed_wall = time.perf_counter() - started
            parsed = parse_cuda_benchmark_output(stdout)
            mode = "100%-CUDA-Dauertest · Bit-Difficulty kompatibel"

            if parsed is None and process.returncode == 0 and elapsed_wall > 0.01:
                # Alter Worker ohne --benchmark-ms:
                # Er wurde nur EINMAL gestartet. Die Laufzeit dieses einen
                # Batches ergibt eine grobe Vergleichsmessung.
                parsed = {
                    "hashes": float(batch_count),
                    "elapsed_ms": elapsed_wall * 1000.0,
                    "hashrate_hs": batch_count / elapsed_wall,
                }
                mode = "Einzelbatch – CUDA-Worker für Präzision neu bauen"

            if process.returncode != 0 or parsed is None:
                error = (stderr or stdout or "Keine Benchmark-Ausgabe.").strip()
                return {
                    "ok": False,
                    "device": device,
                    "gpu_name": gpu_name,
                    "status": (
                        "CUDA-Worker unterstützt den Benchmark nicht. "
                        "BUILD_CUDA_WORKER_SAFE.bat neu ausführen. "
                        + error[:100]
                    ),
                    "duration_seconds": round(elapsed_wall, 2),
                }

            hps = float(parsed.get("hashrate_hs", 0.0))
            temps = [
                sample["temperature_c"]
                for sample in metric_samples
                if sample.get("temperature_c") is not None
            ]
            powers = [
                sample["power_w"]
                for sample in metric_samples
                if sample.get("power_w") is not None
            ]
            utils = [
                sample["utilization_percent"]
                for sample in metric_samples
                if sample.get("utilization_percent") is not None
            ]

            temp_avg = sum(temps) / len(temps) if temps else None
            temp_max = max(temps) if temps else None
            power_avg = sum(powers) / len(powers) if powers else None
            power_max = max(powers) if powers else None
            util_avg = sum(utils) / len(utils) if utils else None
            efficiency = hps / power_avg if power_avg and power_avg > 0 else None

            return {
                "ok": True,
                "device": device,
                "gpu_name": gpu_name,
                "hashrate_hs": hps,
                "hashes": float(parsed.get("hashes", 0.0)),
                "gpu_elapsed_ms": float(parsed.get("elapsed_ms", 0.0)),
                "duration_seconds": round(elapsed_wall, 2),
                "temperature_avg_c": temp_avg,
                "temperature_max_c": temp_max,
                "power_avg_w": power_avg,
                "power_max_w": power_max,
                "utilization_avg_percent": util_avg,
                "efficiency_hs_per_w": efficiency,
                "status": mode,
            }
        except Exception as exc:
            return {
                "ok": False,
                "device": device,
                "gpu_name": gpu_name,
                "status": str(exc),
                "duration_seconds": round(time.perf_counter() - started, 2),
            }

    def _legacy_gpu_benchmark(
        self,
        worker: Path,
        device: int,
        duration: int,
        base_hash: str,
    ) -> tuple[dict[str, float] | None, list[dict[str, float]], float]:
        """
        Seit v0.12.15.3 absichtlich deaktiviert.

        Frühere Versionen starteten hier viele kurze Worker-Prozesse.
        Das verursachte auf Windows ständig aufblitzende Konsolenfenster.
        """
        return None, [], 0.0

    def _gpu_metric_sample(self, device: int) -> dict[str, float] | None:
        for gpu in self.detect_nvidia_gpus():
            if int(gpu.get("index", -1)) != int(device):
                continue
            def number(key: str) -> float | None:
                try:
                    return float(str(gpu.get(key, "")).strip())
                except Exception:
                    return None
            return {"temperature_c": number("temperature_c"), "power_w": number("power_w"), "utilization_percent": number("utilization_percent")}
        return None

    def _upsert_gpu_benchmark_row(self, result: dict[str, Any]) -> None:
        if not hasattr(self, "gpu_benchmark_tree"):
            return
        device = str(result.get("device", "-"))
        existing = None
        for item in self.gpu_benchmark_tree.get_children():
            values = self.gpu_benchmark_tree.item(item, "values")
            if values and str(values[0]) == device:
                existing = item
                break
        def pair(avg_key: str, max_key: str, suffix: str) -> str:
            avg = result.get(avg_key)
            maximum = result.get(max_key)
            if avg is None and maximum is None:
                return "-"
            return f"{float(avg or 0):.1f}/{float(maximum or 0):.1f}{suffix}"
        efficiency = result.get("efficiency_hs_per_w")
        efficiency_text = f"{self.format_hs_ui(efficiency)}/W" if efficiency is not None else "-"
        values = (device, result.get("gpu_name", "-"), self.format_hs_ui(result.get("hashrate_hs")) if result.get("ok") else "-", pair("temperature_avg_c", "temperature_max_c", "°C"), pair("power_avg_w", "power_max_w", " W"), efficiency_text, f"{float(result.get('utilization_avg_percent') or 0):.1f}%", f"{float(result.get('duration_seconds') or 0):.1f}s", result.get("status", "-"))
        if existing:
            self.gpu_benchmark_tree.item(existing, values=values)
        else:
            self.gpu_benchmark_tree.insert("", "end", values=values)

    def _finish_gpu_benchmark_with_error(self, message: str) -> None:
        self.gpu_benchmark_running = False
        self.gpu_benchmark_status_var.set("GPU-Benchmark fehlgeschlagen.")
        messagebox.showerror("GPU-Benchmark", message)

    # Tables/logs/address

    def lookup_address_async(self) -> None:
        threading.Thread(target=self._lookup_address_worker, daemon=True).start()

    def _lookup_address_worker(self) -> None:
        address = self.address_search_var.get().strip()
        if not address:
            self.after(0, lambda: messagebox.showinfo("Adresse", "Bitte eine Adresse eingeben."))
            return
        try:
            data = get_json(self.node_url() + f"/address?address={urllib.parse.quote(address)}")
            self.after(0, lambda: self._apply_address_info(data))
        except Exception as e:
            self.after(0, lambda: messagebox.showerror("Fehler", str(e)))

    def _apply_address_info(self, data: Dict[str, Any]) -> None:
        info = data.get("address_info", {})
        self._set_text(self.address_info_text, "\n".join([
            f"Adresse: {info.get('address')}",
            f"Bestätigt: {fmt_amount(info.get('confirmed_balance'))} {info.get('ticker', 'LOGIC')}",
            f"Spendable: {fmt_amount(info.get('spendable_balance'))}",
            f"Pending rein: {fmt_amount(info.get('pending_in'))}",
            f"Pending raus: {fmt_amount(info.get('pending_out'))}",
            f"Nächster Nonce: {info.get('next_nonce')}",
        ]))

    def _set_text(self, widget: tk.Text, text: str) -> None:
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        widget.insert("1.0", text)
        widget.configure(state="disabled")

    def _fill_blocks(self, chain: List[Dict[str, Any]]) -> None:
        self.blocks_tree.delete(*self.blocks_tree.get_children())
        for block in reversed(chain):
            tx_count = len([tx for tx in block.get("transactions", []) if isinstance(tx, dict) and tx.get("type") == "transfer"])
            self.blocks_tree.insert("", "end", values=(
                f"#{block.get('index')}", short_hash(block.get("hash")), block.get("miner_address"),
                fmt_amount(block.get("reward")),
                (
                    f"{block.get('difficulty_bits')} Bits"
                    if block.get("difficulty_rule") in {"bits-v2", "bits-v3-fast"}
                    else block.get("difficulty")
                ),
                tx_count,
                f"{float(block.get('mining_time_seconds', 0.0)):.2f}s",
                f"{float(block.get('hashrate_hs', 0.0)):,.0f}",
            ))

    def _fill_mempool(self, mempool: List[Dict[str, Any]]) -> None:
        self.mempool_tree.delete(*self.mempool_tree.get_children())
        for tx in mempool:
            self.mempool_tree.insert("", "end", values=(
                short_hash(tx.get("txid")), tx.get("from"), tx.get("to"),
                fmt_amount(tx.get("amount")), fmt_amount(tx.get("fee")), tx.get("nonce"), tx.get("memo", ""),
            ))

    def _fill_balances(self, balances: Dict[str, Any]) -> None:
        self.balances_tree.delete(*self.balances_tree.get_children())
        for address, balance in sorted(balances.items(), key=lambda item: float(item[1]), reverse=True):
            self.balances_tree.insert("", "end", values=(address, f"{fmt_amount(balance)} LOGIC"))

    def refresh_logs(self) -> None:
        parts = []
        for title, path in [
            ("NODE", NODE_LOG_FILE),
            ("CPU MINER", CPU_MINER_LOG_FILE),
            ("EXTERNER MINER", EXTERNAL_MINER_LOG_FILE),
        ]:
            parts.append("=" * 72)
            parts.append(title)
            parts.append("=" * 72)
            if path.exists():
                try:
                    text = path.read_text(encoding="utf-8", errors="replace")
                    parts.append(text[-6000:])
                except Exception as e:
                    parts.append(str(e))
            else:
                parts.append("Noch kein Log.")
        self._set_text(self.logs_text, "\n".join(parts))

    def clear_logs(self) -> None:
        for path in [NODE_LOG_FILE, CPU_MINER_LOG_FILE, EXTERNAL_MINER_LOG_FILE]:
            try:
                path.write_text("", encoding="utf-8")
            except Exception:
                pass
        self.refresh_logs()

    def on_close(self) -> None:
        if messagebox.askyesno("Beenden", "Control Center schließen?\n\nNode/Miner, die über die App gestartet wurden, werden beendet."):
            try:
                self.nvml_monitor.stop()
            except Exception:
                pass
            self.stop_cpu_miner()
            self.stop_gpu_miners()
            self.stop_external_miner()
            self.stop_node()
            self.destroy()


def run_role_from_args() -> bool:
    configure_utf8_stdio()
    """
    Single-EXE-Kompatibilitätsrollen:
    LogicoinControlCenter.exe --role node
    LogicoinControlCenter.exe --role cpu-miner --node-url ... --miner-address ...
    LogicoinControlCenter.exe --role config
    """
    if "--role" not in sys.argv:
        return False

    role_index = sys.argv.index("--role")
    try:
        role = sys.argv[role_index + 1]
    except IndexError:
        print("Fehler: --role ohne Rolle.", flush=True)
        return True

    remaining_args = sys.argv[:role_index] + sys.argv[role_index + 2:]

    if role == "node":
        import logicoin_node
        sys.argv = remaining_args
        logicoin_node.main()
        return True

    if role == "cpu-miner":
        import logicoin_headless_miner
        sys.argv = remaining_args
        logicoin_headless_miner.main()
        return True

    if role == "gpu-miner":
        import logicoin_gpu_miner
        sys.argv = remaining_args
        logicoin_gpu_miner.main()
        return True

    if role == "config":
        import logicoin_config_editor
        sys.argv = remaining_args
        logicoin_config_editor.main()
        return True

    print(f"Unbekannte Rolle: {role}", flush=True)
    return True


def main() -> None:
    if run_role_from_args():
        return

    app = LogicoinControlCenter()
    app.mainloop()


if __name__ == "__main__":
    main()
