# Modifications v2 - Séparation des Logs

## Structure des dossiers

```
data/logs/
├── dry/
│   ├── main/           # Logs du bot principal en dry-run
│   └── btc_range/      # Logs du bot BTC en dry-run
└── live/
    ├── main/           # Logs du bot principal en réel
    └── btc_range/      # Logs du bot BTC en réel
```

## Fichiers modifiés

| Fichier | Modification |
|---------|-------------|
| `core/logging.py` | Séparation dry/live + bot type |
| `btc_range_v1/config.py` | Ajout `botType = "btc_range"` |
| `core/config.py` | Ajout `botType = "main"` |
| `dashboard/app.py` | Lecture depuis `live/main/` ou `dry/main/` |
| `dashboard_btc_range/app.py` | Lecture depuis `live/btc_range/` ou `dry/btc_range/` |
| `scripts/clean-dry.sh` | Nettoie uniquement les logs dry |
| `scripts/clean-live.sh` | Nettoie uniquement les logs live |
| `scripts/clean-all-logs.sh` | Nettoie tous les logs |

## Installation

1. **Sauvegarder** les anciens logs :
   ```bash
   mv data/logs data/logs_backup_$(date +%Y%m%d)
   ```

2. **Extraire** le zip dans ton repo

3. **Modifier** `btc_range_v1/config.py` :
   ```python
   botType: str = "btc_range"
   ```

4. **Modifier** `core/config.py` (bot principal) :
   ```python
   botType: str = "main"
   ```

5. **Lancer** le bot en dry-run :
   ```bash
   export BTC_RANGE_DRY_RUN=1
   python -m btc_range_v1.main
   ```

6. **Vérifier** que les logs vont dans `data/logs/dry/btc_range/`

## Dashboards

- **Dashboard principal** : lit automatiquement dans `data/logs/{mode}/main/`
- **Dashboard BTC** : lit automatiquement dans `data/logs/{mode}/btc_range/`

Le mode (dry/live) est détecté via la variable d'environnement `DRY_RUN` ou `BTC_RANGE_DRY_RUN`.

## Nettoyage

```bash
# Nettoyer uniquement les logs dry-run
./scripts/clean-dry.sh

# Nettoyer uniquement les logs live
./scripts/clean-live.sh

# Nettoyer tous les logs
./scripts/clean-all-logs.sh
```
