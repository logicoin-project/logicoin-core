#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import queue
import subprocess
import threading
import time
from pathlib import Path

from logicoin_core import (
    hash_meets_difficulty_bits,
    logic_hash_v2_cuda_mix_from_base_hex,
)

BASE_DIR = Path(__file__).resolve().parent
WORKER = BASE_DIR / "logicoin_cuda_worker.exe"


def reader(
    stream,
    output_queue: queue.Queue[str],
) -> None:
    try:
        for line in stream:
            output_queue.put(
                line.rstrip("\r\n")
            )
    finally:
        output_queue.put("__EOF__")


def wait_matching(
    process: subprocess.Popen[str],
    output_queue: queue.Queue[str],
    predicate,
    timeout: float,
) -> str:
    deadline = time.monotonic() + timeout
    seen: list[str] = []

    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(
                "Worker endete mit Exitcode "
                f"{process.poll()}."
            )

        try:
            line = output_queue.get(
                timeout=0.25
            )
        except queue.Empty:
            continue

        if line == "__EOF__":
            raise RuntimeError(
                "Worker-Pipe wurde geschlossen."
            )

        seen.append(line)

        if line.startswith("ERROR "):
            raise RuntimeError(line)

        if predicate(line):
            return line

    raise TimeoutError(
        "Worker antwortete nicht rechtzeitig. "
        + " | ".join(seen[-12:])
    )


def parse_found(
    line: str,
) -> tuple[int, str, int, float]:
    parts = line.split()

    if (
        len(parts) < 5
        or parts[0] != "FOUND"
    ):
        raise RuntimeError(
            f"Ungültige FOUND-Antwort: {line}"
        )

    return (
        int(parts[1]),
        parts[2],
        int(parts[3]),
        float(parts[4]),
    )


def parse_stream_found(
    line: str,
) -> tuple[str, int, str, int, float, float]:
    parts = line.split()

    if (
        len(parts) < 7
        or parts[0] != "STREAM_FOUND"
    ):
        raise RuntimeError(
            "Ungültige STREAM_FOUND-Antwort: "
            f"{line}"
        )

    return (
        parts[1],
        int(parts[2]),
        parts[3],
        int(parts[4]),
        float(parts[5]),
        float(parts[6]),
    )


def verify_target(
    base_hash: str,
    nonce: int,
    worker_hash: str,
    difficulty_bits: int,
) -> None:
    python_hash = (
        logic_hash_v2_cuda_mix_from_base_hex(
            base_hash,
            nonce,
        )
    )

    if worker_hash.lower() != python_hash.lower():
        raise RuntimeError(
            "CUDA-/Python-Hash unterscheiden sich. "
            f"CUDA={worker_hash}, "
            f"Python={python_hash}"
        )

    if not hash_meets_difficulty_bits(
        python_hash,
        difficulty_bits,
    ):
        raise RuntimeError(
            f"Hash erfüllt {difficulty_bits} Bits "
            "nicht."
        )


def main() -> int:
    if not WORKER.exists():
        print(
            "FEHLER: logicoin_cuda_worker.exe "
            "nicht gefunden."
        )
        return 1

    process = subprocess.Popen(
        [
            str(WORKER),
            "--server",
            "--device",
            "0",
        ],
        cwd=str(BASE_DIR),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )

    if (
        process.stdin is None
        or process.stdout is None
    ):
        print(
            "FEHLER: Worker-Pipes konnten "
            "nicht geöffnet werden."
        )
        return 2

    output_queue: queue.Queue[str] = (
        queue.Queue()
    )
    thread = threading.Thread(
        target=reader,
        args=(process.stdout, output_queue),
        daemon=True,
    )
    thread.start()

    try:
        wait_matching(
            process,
            output_queue,
            lambda line: line.startswith(
                "READY 0.12.15.3 "
            ),
            20,
        )

        process.stdin.write("PING\n")
        process.stdin.flush()
        wait_matching(
            process,
            output_queue,
            lambda line: line == "PONG",
            5,
        )

        base_hash = (
            "000102030405060708090a0b0c0d0e0f"
            "101112131415161718191a1b1c1d1e1f"
        )

        # Test 1: exakter Einzelhash und exakte
        # Nonce-Zählung.
        direct_nonce = 123456789
        process.stdin.write(
            f"SCAN {base_hash} 0 "
            f"{direct_nonce} 1\n"
        )
        process.stdin.flush()

        direct_line = wait_matching(
            process,
            output_queue,
            lambda line: line.startswith(
                "FOUND "
            ),
            30,
        )
        (
            nonce,
            found_hash,
            tested,
            active_ms,
        ) = parse_found(direct_line)

        if nonce != direct_nonce:
            raise RuntimeError(
                "Direkttest falsche Nonce: "
                f"{nonce}"
            )

        if tested != 1:
            raise RuntimeError(
                "Direkttest muss exakt eine "
                f"Nonce melden, erhalten {tested}."
            )

        verify_target(
            base_hash,
            nonce,
            found_hash,
            0,
        )

        # Test 2: echte 20-Bit-Suche im
        # normalen SCAN-Protokoll.
        search_count = 2_000_000
        process.stdin.write(
            f"SCAN {base_hash} 20 0 "
            f"{search_count}\n"
        )
        process.stdin.flush()

        proof_line = wait_matching(
            process,
            output_queue,
            lambda line: line.startswith(
                "FOUND "
            ),
            30,
        )
        (
            proof_nonce,
            proof_hash,
            proof_tested,
            proof_active_ms,
        ) = parse_found(proof_line)

        if not (
            0 <= proof_nonce < search_count
        ):
            raise RuntimeError(
                "20-Bit-SCAN Nonce außerhalb "
                f"des Bereichs: {proof_nonce}"
            )

        if not (
            1 <= proof_tested <= search_count
        ):
            raise RuntimeError(
                "20-Bit-SCAN ungültige exakte "
                f"Testanzahl: {proof_tested}"
            )

        verify_target(
            base_hash,
            proof_nonce,
            proof_hash,
            20,
        )

        # Test 3: echte 20-Bit-Suche über die
        # Streaming-Pipeline.
        stream_job = "proof-stream-test"
        stream_chunk = 262_144

        process.stdin.write(
            f"START {stream_job} "
            f"{base_hash} 20 0 "
            f"{stream_chunk} 100 100\n"
        )
        process.stdin.flush()

        wait_matching(
            process,
            output_queue,
            lambda line: (
                line
                == f"STARTED {stream_job}"
            ),
            10,
        )

        stream_line = wait_matching(
            process,
            output_queue,
            lambda line: line.startswith(
                f"STREAM_FOUND {stream_job} "
            ),
            30,
        )
        (
            returned_job,
            stream_nonce,
            stream_hash,
            stream_tested,
            stream_active_ms,
            stream_wall_ms,
        ) = parse_stream_found(stream_line)

        if returned_job != stream_job:
            raise RuntimeError(
                "Falsche Streaming-Job-ID."
            )

        # Der erste bekannte 20-Bit-Treffer
        # liegt vor 2 Mio. Nonces.
        if not (
            0 <= stream_nonce < search_count
        ):
            raise RuntimeError(
                "Streaming-Nonce außerhalb "
                f"des Testbereichs: {stream_nonce}"
            )

        if not (
            1 <= stream_tested <= search_count
        ):
            raise RuntimeError(
                "Streaming meldet ungültige "
                "exakte Testanzahl: "
                f"{stream_tested}"
            )

        verify_target(
            base_hash,
            stream_nonce,
            stream_hash,
            20,
        )

        # Test 4: Langzeitjob liefert Fortschritt
        # und lässt sich sauber stoppen.
        stop_job = "stop-stream-test"
        process.stdin.write(
            f"START {stop_job} "
            f"{base_hash} 256 0 "
            "262144 100 100\n"
        )
        process.stdin.flush()

        wait_matching(
            process,
            output_queue,
            lambda line: (
                line == f"STARTED {stop_job}"
            ),
            10,
        )

        progress = wait_matching(
            process,
            output_queue,
            lambda line: line.startswith(
                f"PROGRESS {stop_job} "
            ),
            20,
        )

        progress_parts = progress.split()
        if len(progress_parts) < 6:
            raise RuntimeError(
                "Ungültige PROGRESS-Antwort: "
                f"{progress}"
            )

        progress_tested = int(
            progress_parts[2]
        )
        if progress_tested <= 0:
            raise RuntimeError(
                "PROGRESS meldet keine "
                "geprüften Nonces."
            )

        process.stdin.write(
            f"STOP {stop_job}\n"
        )
        process.stdin.flush()

        stopped = wait_matching(
            process,
            output_queue,
            lambda line: line.startswith(
                f"STOPPED {stop_job} "
            ),
            10,
        )

        stopped_parts = stopped.split()
        if len(stopped_parts) < 6:
            raise RuntimeError(
                "Ungültige STOPPED-Antwort: "
                f"{stopped}"
            )

        stopped_tested = int(
            stopped_parts[2]
        )
        if stopped_tested < progress_tested:
            raise RuntimeError(
                "Exakter Nonce-Zähler ist "
                "beim Stoppen rückwärts gesprungen."
            )

        wait_matching(
            process,
            output_queue,
            lambda line: (
                line
                == f"STOP_ACK {stop_job}"
            ),
            10,
        )

        process.stdin.write("QUIT\n")
        process.stdin.flush()
        wait_matching(
            process,
            output_queue,
            lambda line: line == "BYE",
            5,
        )

        process.wait(timeout=5)

        print(
            "STREAMING_CUDA_EXACT_ACCOUNTING_TEST_OK"
        )
        print(
            "OK: Einzelhash"
        )
        print(
            "OK: exakte Nonce-Zählung"
        )
        print(
            "OK: echte 20-Bit-SCAN-Suche"
        )
        print(
            "OK: echte 20-Bit-Streaming-Suche"
        )
        print(
            "OK: PROGRESS und STOP"
        )
        return 0
    except Exception as exc:
        print(f"FEHLER: {exc}")
        return 3
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()


if __name__ == "__main__":
    raise SystemExit(main())
