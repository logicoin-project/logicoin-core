Logicoin / LOGIC v0.12.15.3
Public Testnet RC1 + Public Network JSON
========================================

Neue Datei
----------
logicoin_public_network.json

Sie enthält getrennt von den Konsensregeln:

- öffentliche Seed-Nodes
- native LOGIC-Daten
- Explorer- und RPC-Adressen
- zukünftige Wrapped-Token-Daten
- zukünftige 1:1-Bridge-Daten
- aktuelle Emissionsdaten
- Veröffentlichungsstatus

Seed-Node eintragen
-------------------
Unter native_asset:

"public_seed_nodes": [
  "http://DEINE-DOMAIN-ODER-IP:8080"
]

Die Node übernimmt diese Adressen automatisch zusätzlich zu den Seed-Nodes
aus logicoin_config.json.

Wrapped Token
-------------
Der Bereich wrapped_token ist vorbereitet, aber absichtlich deaktiviert:

"enabled": false

Nach Deployment werden dort Host-Chain, Chain-ID und Contract-Adresse
eingetragen. Empfohlener Name und Ticker:

Wrapped Logicoin / wLOGIC

Bridge
------
Die Bridge ist ebenfalls deaktiviert. Vorgesehen ist:

- native LOGIC werden auf der Logicoin-Chain gesperrt
- exakt dieselbe Menge wLOGIC wird auf der Host-Chain geprägt
- beim Rücktausch wird wLOGIC verbrannt
- native LOGIC werden wieder freigegeben
- Verhältnis 1 LOGIC = 1 wLOGIC

Eine öffentliche Bridge sollte nicht mit einem einzelnen privaten Schlüssel
betrieben werden. Vor Aktivierung sind mindestens Multisig, Pausenfunktion,
Reserveprüfung und ein externer Sicherheitstest erforderlich.

Prüfung
-------
CHECK_PUBLIC_NETWORK_JSON.bat

Bearbeiten
----------
EDIT_PUBLIC_NETWORK_JSON.bat

Node-Endpunkt
-------------
GET /asset_registry

Der Endpunkt liefert die öffentliche Registry inklusive Validierungsstatus.

Emission bei aktuellen Testwerten
---------------------------------
Blockreward: 50 LOGIC
Zielblockzeit: 30 Sekunden
Pro Tag: ungefähr 144.000 LOGIC
Pro Jahr: ungefähr 52.560.000 LOGIC

Da aktuell weder maximale Menge noch Halving festgelegt sind, muss die
Mainnet-Tokenomics vor einer echten Wertveröffentlichung noch eingefroren
werden.
