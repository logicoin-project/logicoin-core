# Logicoin (LOGIC)

Logicoin is an experimental proof-of-work blockchain project with its own native coin, wallet, node, block explorer, dynamic difficulty and CUDA mining support.

> **Status:** Public Testnet Release Candidate  
> **Network ID:** `logicoin-public-testnet-rc1`  
> **Version:** `0.12.15.3`  
> **Important:** Testnet coins have no guaranteed monetary value and may be reset before a future mainnet.

## Current features

- Native proof-of-work blockchain
- Signed wallets and transactions
- Dynamic bit difficulty with a target block time of about 30 seconds
- CPU and NVIDIA CUDA mining
- Multi-GPU management
- Peer synchronization and public seed-node support
- Built-in block explorer
- Public asset registry for native LOGIC and a future wrapped token
- Wallet backup and restore
- Diagnostics export without private wallet keys
- Clean public release builder

## Quick start on Windows

1. Download the latest testnet ZIP from **Releases**.
2. Extract it into a new folder.
3. Run `BUILD_LOGICOIN_APP_EXE.bat`.
4. Run `BUILD_CUDA_WORKER_SAFE.bat` when using NVIDIA GPU mining.
5. Run `CHECK_PUBLIC_TESTNET_READINESS.bat`.
6. Start `START_LOGICOIN_APP.bat`.
7. Back up the automatically generated wallet before mining.

The CUDA worker test must report:

```text
STREAMING_CUDA_EXACT_ACCOUNTING_TEST_OK
```

## Public network configuration

Public endpoints and the future wrapped-token configuration are stored in:

```text
logicoin_public_network.json
```

The bridge and wrapped token are intentionally disabled until a contract, reserve model, multisignature control and security review exist.

## Build from source

Requirements:

- Windows 10 or Windows 11
- Python 3.11 or newer
- NVIDIA driver and CUDA toolkit for CUDA worker builds
- Visual Studio C++ Build Tools for the native CUDA executable

Start the Python application with:

```bat
python logicoin_control_center.py
```

## Security

- Never publish `logic_wallet.json`.
- Never send a wallet private key through an issue, log or diagnostics package.
- Do not expose the node directly to the public internet without understanding firewall and rate-limit risks.
- This testnet software has not received an independent professional security audit.

Please read [SECURITY.md](SECURITY.md) before reporting a vulnerability.

## Privacy

The project files use neutral project identities. Personal names, personal email addresses and private wallet files must not be committed. Run the included privacy check before each public release:

```bat
python scripts/privacy_scan.py
```

## License

A final software license has not yet been selected. Until a license is added, no broad permission to copy, modify or redistribute the source is granted beyond what is required to view and test the repository through GitHub.

## Disclaimer

Logicoin is experimental software. It is not an investment product, does not promise profit, and the Public Testnet has no guaranteed monetary value.


## v0.12.15.3 node startup hotfix

The Windows builder now explicitly bundles and copies
`logicoin_public_network.json`. The node also contains a safe embedded
fallback registry so `/info` remains available when the external JSON is
missing.
