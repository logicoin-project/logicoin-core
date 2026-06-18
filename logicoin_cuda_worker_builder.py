#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
LOG_FILE = BASE_DIR / "logicoin_cuda_build_debug.log"


def log(msg: str) -> None:
    print(msg, flush=True)
    with LOG_FILE.open("a", encoding="utf-8") as f:
        f.write(msg + "\n")


def run(cmd: list[str] | str, shell: bool = False) -> tuple[int, str]:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, shell=shell, cwd=str(BASE_DIR))
        out = (p.stdout or "") + (p.stderr or "")
        return p.returncode, out.strip()
    except Exception as e:
        return 999, str(e)


def find_nvcc() -> Path | None:
    found = shutil.which("nvcc")
    if found:
        return Path(found)

    roots = [
        Path(r"C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA"),
        Path(os.environ.get("CUDA_PATH", "")),
    ]

    candidates: list[Path] = []
    for root in roots:
        if root and root.exists():
            candidates.extend(root.glob(r"v*\bin\nvcc.exe"))
            candidates.extend(root.glob(r"bin\nvcc.exe"))

    candidates = sorted(set(candidates), reverse=True)
    return candidates[0] if candidates else None


def find_vswhere() -> Path | None:
    candidates = [
        Path(r"C:\Program Files (x86)\Microsoft Visual Studio\Installer\vswhere.exe"),
        Path(r"C:\Program Files\Microsoft Visual Studio\Installer\vswhere.exe"),
    ]
    for c in candidates:
        if c.exists():
            return c
    found = shutil.which("vswhere")
    return Path(found) if found else None


def find_vcvars64() -> Path | None:
    vswhere = find_vswhere()
    if vswhere:
        code, out = run([
            str(vswhere),
            "-latest",
            "-prerelease",
            "-products", "*",
            "-property", "installationPath",
        ])
        if code == 0 and out.strip():
            for line in out.splitlines():
                root = Path(line.strip())
                for candidate in [
                    root / r"VC\Auxiliary\Build\vcvars64.bat",
                    root / r"VC\Auxiliary\Build\vcvarsall.bat",
                ]:
                    if candidate.exists():
                        return candidate

    roots = [
        Path(r"C:\Program Files\Microsoft Visual Studio"),
        Path(r"C:\Program Files (x86)\Microsoft Visual Studio"),
    ]

    matches: list[Path] = []
    for root in roots:
        if root.exists():
            matches.extend(root.glob(r"**\VC\Auxiliary\Build\vcvars64.bat"))
            matches.extend(root.glob(r"**\VC\Auxiliary\Build\vcvarsall.bat"))

    matches = sorted(set(matches), reverse=True)
    return matches[0] if matches else None


def find_cl_direct() -> Path | None:
    found = shutil.which("cl")
    if found:
        return Path(found)

    roots = [
        Path(r"C:\Program Files\Microsoft Visual Studio"),
        Path(r"C:\Program Files (x86)\Microsoft Visual Studio"),
    ]

    matches: list[Path] = []
    for root in roots:
        if root.exists():
            matches.extend(root.glob(r"**\VC\Tools\MSVC\*\bin\Hostx64\x64\cl.exe"))
            matches.extend(root.glob(r"**\VC\Tools\MSVC\*\bin\Hostx86\x64\cl.exe"))

    matches = sorted(set(matches), reverse=True)
    return matches[0] if matches else None


def write_temp_build_bat(nvcc: Path, vcvars: Path | None) -> Path:
    temp_bat = BASE_DIR / "_logicoin_cuda_worker_build_generated.bat"

    lines = [
        "@echo off",
        "setlocal EnableExtensions",
        'cd /d "%~dp0"',
        "title Logicoin Generated CUDA Build v0.12.15.3",
        "echo ============================================================",
        "echo Logicoin Generated CUDA Build v0.12.15.3",
        "echo ============================================================",
        "echo.",
    ]

    if vcvars:
        # Important: this line is not inside IF parentheses, so (x86) is safe.
        lines += [
            "echo Lade Visual Studio Build Umgebung:",
            f'echo {vcvars}',
            f'call "{vcvars}"',
            "echo.",
        ]

    lines += [
        "echo Pruefe cl.exe ...",
        "where cl",
        "if errorlevel 1 goto CL_ERROR",
        "echo.",
        "echo Baue logicoin_cuda_worker.exe ...",
        "echo v0.12.15.3: Streaming LogicHash-v2-CUDA-Mix Worker wird gebaut.",
        "echo Aktive Targets: sm_75 RTX 20xx, sm_86 RTX 30xx",
        f'"{nvcc}" -O3 ^',
        "  --cudart static ^",
        '  -Xcompiler "/MT" ^',
        "  -gencode arch=compute_75,code=sm_75 ^",
        "  -gencode arch=compute_86,code=sm_86 ^",
        "  -gencode arch=compute_86,code=compute_86 ^",
        "  logicoin_cuda_worker.cu -o logicoin_cuda_worker.exe",
        "if errorlevel 1 goto BUILD_ERROR",
        "echo.",
        "if exist logicoin_cuda_worker.exe (",
        "  echo logicoin_cuda_worker.exe erstellt.",
        "  echo.",
        "  echo Teste Streaming-CUDA-Worker ...",
        "  python logicoin_cuda_worker_protocol_test.py",
        "  if errorlevel 1 goto PROTOCOL_ERROR",
        "  if exist dist (",
        "    copy /Y logicoin_cuda_worker.exe dist\\logicoin_cuda_worker.exe >nul",
        "    echo Getesteter Worker nach dist\\logicoin_cuda_worker.exe kopiert.",
        "  )",
        "  goto OK",
        ")",
        "goto BUILD_ERROR",
        "",
        ":CL_ERROR",
        "echo.",
        "echo FEHLER: cl.exe wurde nach Laden der VS-Umgebung nicht gefunden.",
        "goto END",
        "",
        ":BUILD_ERROR",
        "echo.",
        "echo FEHLER: CUDA Worker Build fehlgeschlagen.",
        "goto END",
        "",
        ":PROTOCOL_ERROR",
        "echo.",
        "echo FEHLER: Der Streaming-Worker-Protokolltest ist fehlgeschlagen.",
        "echo Der Worker wird nicht fuer Mining freigegeben.",
        "goto END",
        "",
        ":OK",
        "echo.",
        "echo ============================================================",
        "echo FERTIG: Streaming-CUDA-Worker wurde gebaut und getestet.",
        "echo Jetzt start_logicoin_gpu_miner_auto.bat testen.",
        "echo ============================================================",
        "goto END",
        "",
        ":END",
        "echo.",
        "echo Fenster bleibt offen.",
        "pause",
    ]

    temp_bat.write_text("\n".join(lines), encoding="utf-8")
    return temp_bat


def main() -> int:
    LOG_FILE.write_text("", encoding="utf-8")

    log("=" * 70)
    log("Logicoin / LOGIC CUDA Worker Python Builder v0.12.15.3")
    log("=" * 70)
    log(f"Ordner: {BASE_DIR}")
    log("")

    nvcc = find_nvcc()
    if not nvcc:
        log("FEHLER: nvcc nicht gefunden.")
        input("Enter zum Schließen...")
        return 1

    log(f"nvcc gefunden: {nvcc}")

    vcvars = find_vcvars64()
    cl_direct = find_cl_direct()

    if vcvars:
        log(f"vcvars gefunden: {vcvars}")
    else:
        log("WARNUNG: vcvars64.bat nicht gefunden.")

    if cl_direct:
        log(f"cl.exe Datei gefunden: {cl_direct}")
    else:
        log("WARNUNG: cl.exe Datei nicht direkt gefunden.")

    if not vcvars and not shutil.which("cl"):
        log("")
        log("FEHLER: Keine Visual Studio Build Umgebung gefunden.")
        log("Öffne alternativ x64 Native Tools Command Prompt und starte build_logicoin_cuda_worker_core.bat.")
        input("Enter zum Schließen...")
        return 2

    temp_bat = write_temp_build_bat(nvcc, vcvars)
    log("")
    log(f"Temporäre sichere Build-Batch erstellt: {temp_bat}")
    log("Starte Build-Batch in neuem Fenster ...")

    # /k keeps it open no matter what
    subprocess.Popen(["cmd.exe", "/k", str(temp_bat)], cwd=str(BASE_DIR))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
