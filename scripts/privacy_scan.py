#!/usr/bin/env python3
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

TEXT_SUFFIXES = {
    ".py", ".txt", ".md", ".json", ".bat", ".yml",
    ".yaml", ".html", ".css", ".js", ".cu",
}
IGNORED_PARTS = {
    ".git", "__pycache__", "build", "dist", "release",
    "backups", "diagnostics",
}

EMAIL_RE = re.compile(
    r"\b[A-Z0-9._%+-]+@(?!users\.noreply\.github\.com\b)"
    r"[A-Z0-9.-]+\.[A-Z]{2,}\b",
    re.IGNORECASE,
)
WINDOWS_USER_RE = re.compile(
    r"[A-Za-z]:\\Users\\([^\\/\r\n]+)",
    re.IGNORECASE,
)
PRIVATE_KEY_JSON_RE = re.compile(
    r'"private_key"\s*:\s*"(?!<REDACTED>|")',
    re.IGNORECASE,
)
HUMAN_TEST_WALLET_RE = re.compile(
    r"logic1_(?!public_test_wallet\b)[a-z][a-z0-9_]*_test_wallet\b",
    re.IGNORECASE,
)

findings: list[str] = []

for path in ROOT.rglob("*"):
    if not path.is_file():
        continue
    if any(part in IGNORED_PARTS for part in path.parts):
        continue
    if path.suffix.lower() not in TEXT_SUFFIXES:
        continue

    text = path.read_text(encoding="utf-8", errors="ignore")
    relative = path.relative_to(ROOT)

    for label, pattern in (
        ("possible email address", EMAIL_RE),
        ("Windows user path", WINDOWS_USER_RE),
        ("private key in JSON", PRIVATE_KEY_JSON_RE),
        ("human-named test wallet", HUMAN_TEST_WALLET_RE),
    ):
        if pattern.search(text):
            findings.append(f"{relative}: {label}")

for forbidden_name in (
    "logic_wallet.json",
    "logicoin_node_identity.json",
):
    if (ROOT / forbidden_name).exists():
        findings.append(f"{forbidden_name}: private/runtime file must not be committed")

if findings:
    print("PRIVACY_SCAN_FAILED")
    for finding in findings:
        print("-", finding)
    raise SystemExit(1)

print("PRIVACY_SCAN_OK")
