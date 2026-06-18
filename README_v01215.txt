Logicoin / LOGIC v0.12.15.3 – Public Testnet RC1
==============================================

Netzwerk
--------
Name:          Logicoin Public Testnet RC1
Netzwerk-ID:   logicoin-public-testnet-rc1
Release-Kanal: public-testnet-rc1
Genesis-Hash:  e5c3eed677420fe6e8e6e2fe6b28db117ea7bb579cc609f18f371213cffa6410

Status
------
Dies ist ein Release Candidate für ein öffentliches Testnetz.

LOGIC-Testcoins besitzen keinen garantierten Geldwert.
Die Testnet-Chain und alle Testguthaben können vor dem Mainnet zurückgesetzt
werden.

Wichtige Änderungen
--------------------
- neue, saubere Public-Testnet-Genesis
- neue feste Netzwerk-ID
- alle Blöcke und Transaktionen sind an diese Netzwerk-ID gebunden
- Wallets enthalten ebenfalls die Netzwerk-ID
- ausschließlich signierte Transaktionen
- alte unsignierte Mining-Testwallets sind deaktiviert
- Wallet-Backup und Wiederherstellung
- Diagnoseexport ohne Private Keys
- Release-Readiness-Prüfung
- Seed-Node-Bootstrap über logicoin_config.json
- Builder für ein bereinigtes öffentliches Tester-ZIP

Wichtig beim Upgrade
--------------------
Die alte Alpha-Chain darf NICHT in v0.12.15.3 kopiert werden.

Wallets aus älteren Versionen ohne Netzwerk-ID sind nicht mit diesem
Public Testnet kompatibel. Für das neue Testnetz eine neue Wallet erstellen.

Public-Release bauen
--------------------
BUILD_PUBLIC_TESTNET_RELEASE.bat

Das Ergebnis liegt anschließend unter:

release\Logicoin_Public_Testnet_RC1_v0.12.15.3.zip

Seed-Node
---------
In logicoin_config.json können öffentliche Seed-Nodes eingetragen werden:

"seed_nodes": [
  "http://DEINE-IP-ODER-DOMAIN:8080"
]

Ohne Seed-Node müssen Tester die Node-Adresse manuell eintragen.

Sicherheit
----------
- Private Key niemals versenden
- Wallet nach Erstellung sofort sichern
- Diagnoseexport enthält keinen Private Key
- öffentliche Server sollten keine Treasury- oder Hauptwallet enthalten
