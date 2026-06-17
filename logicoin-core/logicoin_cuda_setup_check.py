#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Logicoin / LOGIC CUDA Setup Check v0.12.15.3

Prüft:
- NVIDIA GPU / nvidia-smi
- CUDA Toolkit / nvcc
- Visual Studio C++ Build Tools / cl.exe
- logicoin_cuda_worker.exe
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

BASE_DIR = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
STATUS_FILE = BASE_DIR / "logicoin_cuda_status.json"


def run_cmd(cmd: list[str], timeout: int = 15) -> tuple[int, str]:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        out = (p.stdout or "") + (p.stderr or "")
        return p.returncode, out.strip()
    except Exception as e:
        return 999, str(e)


def find_nvcc() -> str | None:
    found = shutil.which("nvcc")
    if found:
        return found

    roots = [
        Path(r"C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA"),
        Path(r"C:\Program Files (x86)\NVIDIA GPU Computing Toolkit\CUDA"),
    ]
    candidates: list[Path] = []

    for root in roots:
        if root.exists():
            candidates.extend(sorted(root.glob(r"v*\bin\nvcc.exe"), reverse=True))

    for c in candidates:
        if c.exists():
            return str(c)

    return None


def find_nvidia_smi() -> str | None:
    found = shutil.which("nvidia-smi")
    if found:
        return found

    candidates = [
        Path(r"C:\Windows\System32\nvidia-smi.exe"),
        Path(r"C:\Program Files\NVIDIA Corporation\NVSMI\nvidia-smi.exe"),
    ]

    for c in candidates:
        if c.exists():
            return str(c)

    return None


def find_cl() -> str | None:
    found = shutil.which("cl")
    if found:
        return found

    vswhere_candidates = [
        Path(r"C:\Program Files (x86)\Microsoft Visual Studio\Installer\vswhere.exe"),
        Path(r"C:\Program Files\Microsoft Visual Studio\Installer\vswhere.exe"),
    ]

    for vswhere in vswhere_candidates:
        if vswhere.exists():
            code, out = run_cmd([
                str(vswhere),
                "-latest",
                "-products", "*",
                "-requires", "Microsoft.VisualStudio.Component.VC.Tools.x86.x64",
                "-property", "installationPath",
            ])
            if code == 0 and out:
                root = Path(out.splitlines()[0].strip())
                matches = list(root.glob(r"VC\Tools\MSVC\*\bin\Hostx64\x64\cl.exe"))
                if matches:
                    return str(sorted(matches, reverse=True)[0])

    roots = [
        Path(r"C:\Program Files\Microsoft Visual Studio"),
        Path(r"C:\Program Files (x86)\Microsoft Visual Studio"),
    ]
    for root in roots:
        if root.exists():
            matches = list(root.glob(r"**\VC\Tools\MSVC\*\bin\Hostx64\x64\cl.exe"))
            if matches:
                return str(sorted(matches, reverse=True)[0])

    return None


def find_vcvars64() -> str | None:
    vswhere_candidates = [
        Path(r"C:\Program Files (x86)\Microsoft Visual Studio\Installer\vswhere.exe"),
        Path(r"C:\Program Files\Microsoft Visual Studio\Installer\vswhere.exe"),
    ]

    for vswhere in vswhere_candidates:
        if vswhere.exists():
            code, out = run_cmd([
                str(vswhere),
                "-latest",
                "-products", "*",
                "-requires", "Microsoft.VisualStudio.Component.VC.Tools.x86.x64",
                "-property", "installationPath",
            ])
            if code == 0 and out:
                root = Path(out.splitlines()[0].strip())
                candidates = [
                    root / r"VC\Auxiliary\Build\vcvars64.bat",
                    root / r"VC\Auxiliary\Build\vcvarsall.bat",
                ]
                for c in candidates:
                    if c.exists():
                        return str(c)

    roots = [
        Path(r"C:\Program Files\Microsoft Visual Studio"),
        Path(r"C:\Program Files (x86)\Microsoft Visual Studio"),
    ]
    for root in roots:
        if root.exists():
            matches = list(root.glob(r"**\VC\Auxiliary\Build\vcvars64.bat"))
            if matches:
                return str(sorted(matches, reverse=True)[0])

    return None


def detect_gpus(nvidia_smi: str | None) -> list[dict[str, Any]]:
    if not nvidia_smi:
        return []

    code, out = run_cmd([
        nvidia_smi,
        "--query-gpu=index,name,driver_version",
        "--format=csv,noheader",
    ])
    if code != 0:
        return [{"error": out}]

    gpus = []
    for line in out.splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) >= 3:
            gpus.append({
                "index": parts[0],
                "name": parts[1],
                "driver": parts[2],
            })
        elif line.strip():
            gpus.append({"raw": line.strip()})

    return gpus


def main() -> None:
    print("=" * 70)
    print("Logicoin / LOGIC CUDA Setup Check v0.12.15.3")
    print("=" * 70)
    print()

    nvidia_smi = find_nvidia_smi()
    nvcc = find_nvcc()
    cl = find_cl()
    vcvars64 = find_vcvars64()
    worker = BASE_DIR / "logicoin_cuda_worker.exe"

    gpus = detect_gpus(nvidia_smi)

    status: dict[str, Any] = {
        "nvidia_smi": nvidia_smi,
        "nvcc": nvcc,
        "cl": cl,
        "vcvars64": vcvars64,
        "cuda_worker": str(worker) if worker.exists() else None,
        "gpus": gpus,
        "ready_for_cuda_build": bool(nvcc and (cl or vcvars64)),
        "ready_for_cuda_mining": bool(worker.exists()),
    }

    print("[GPU]")
    if gpus:
        for gpu in gpus:
            if "name" in gpu:
                print(f"✅ GPU {gpu.get('index')}: {gpu.get('name')} | Driver {gpu.get('driver')}")
            else:
                print(f"⚠ {gpu}")
    else:
        print("❌ Keine NVIDIA-GPU per nvidia-smi erkannt.")
    print()

    print("[CUDA Toolkit / nvcc]")
    if nvcc:
        print(f"✅ nvcc gefunden: {nvcc}")
        code, out = run_cmd([nvcc, "--version"])
        if out:
            print(out)
    else:
        print("❌ nvcc nicht gefunden.")
        print("   Installiere das NVIDIA CUDA Toolkit oder füge CUDA\\bin zum PATH hinzu.")
    print()

    print("[Visual Studio C++ Build Tools]")
    if cl:
        print(f"✅ cl.exe gefunden: {cl}")
    elif vcvars64:
        print(f"✅ vcvars64.bat gefunden: {vcvars64}")
    else:
        print("❌ C++ Build Tools nicht gefunden.")
        print("   Installiere Visual Studio Build Tools mit C++ Desktop-Workload.")
    print()

    print("[CUDA 13 Hinweis]")
    print("Hinweis: CUDA 13 kann pre-Turing/Pascal wie GTX 1050 Ti sm_61 nicht mehr kompilieren.")
    print("Dieser Worker wird für RTX 20xx sm_75 und RTX 30xx sm_86 gebaut.")
    print()

    print("[Logicoin CUDA Worker]")
    if worker.exists():
        print(f"✅ logicoin_cuda_worker.exe vorhanden: {worker}")
    else:
        print("❌ logicoin_cuda_worker.exe fehlt.")
        print("   Baue ihn mit: build_logicoin_cuda_worker.bat")
    print()

    if status["ready_for_cuda_mining"]:
        print("STATUS: ✅ Echtes CUDA-GPU-Mining ist bereit.")
    elif status["ready_for_cuda_build"]:
        print("STATUS: ⚠ CUDA kann gebaut werden. Starte build_logicoin_cuda_worker.bat.")
    else:
        print("STATUS: ❌ Noch nicht bereit für echtes CUDA.")
        print("        Bis dahin läuft LOGIC GPU Testminer nur per CPU-Fallback.")

    STATUS_FILE.write_text(json.dumps(status, indent=2, ensure_ascii=False), encoding="utf-8")
    print()
    print(f"Status gespeichert: {STATUS_FILE}")
    print()
    input("Enter drücken zum Schließen...")


if __name__ == "__main__":
    main()
