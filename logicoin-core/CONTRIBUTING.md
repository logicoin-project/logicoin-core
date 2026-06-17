# Contributing

Contributions for testnet reliability, validation, networking, wallet safety, documentation and reproducible testing are welcome.

## Before submitting

1. Create a separate branch.
2. Keep consensus changes isolated and documented.
3. Run `python -m compileall .`.
4. Run `python logicoin_public_network.py`.
5. Run `python scripts/privacy_scan.py`.
6. Never commit wallets, private keys, logs, local chain data or personal information.

Consensus changes must explain compatibility with existing blocks and peers.
