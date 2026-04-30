# Modifications du Bot BTC Range V1

## Fichiers modifies

Ce zip contient les fichiers modifies pour ameliorer la profitabilite du bot.

### 1. btc_range_v1/logic.py
**Corrections majeures:**
- **Filtre de tendance**: Ne trade plus en tendance baissiere (evite les pieges de range)
- **Stop ATR-based**: Stop dynamique base sur la volatilite reelle
- **Entry zone plus basse**: 15% au lieu de 20% pour meilleur R:R
- **Rebound plus strict**: 5 ticks au lieu de 3, avec ratio minimum de 60% de ticks haussiers
- **Protection plus precoce**: 25% du span au lieu de 35%
- **Trailing stop**: Nouveau mecanisme de trailing stop base sur le max atteint
- **Time stop reduit**: 1h au lieu de 2h

### 2. btc_range_v1/config.py
**Nouveaux parametres:**
- `trendMaxAgainstPct`: Tendance baissiere max autorisee (1.5%)
- `atrStopMult`: Multiplicateur ATR pour le stop (1.5)
- `minStopDistancePct`: Distance minimum stop/entree (0.3%)
- `reboundMinUpRatio`: Ratio minimum de ticks haussiers (60%)
- `trailStopPct`: Pourcentage de trailing stop (0.4%)

### 3. config/btc_range.yaml
**3 profils disponibles:**
- `default`: Parametres equilibres (recommande pour commencer)
- `conservative`: Plus strict, moins de trades mais meilleure qualite
- `aggressive`: Plus de trades, plus risque

### 4. indicators/basic.py
**Ajouts:**
- Fonction `atr()`: Calcul de l'Average True Range
- Fonction `computeTrendState()`: Analyse de la tendance globale

### 5. dashboard_btc_range/app.py
**Ameliorations:**
- Affichage des metriques de range (low, high, ATR, trend)
- Meilleure gestion des erreurs

### 6. dashboard/static/app.js
**Corrections:**
- Fonction `td()` corrigee (retourne la valeur au lieu de template string vide)
- Fonction `fmtBucket()` corrigee pour eviter les erreurs null
- Fonction `fmtPnlPair()` corrigee pour les valeurs undefined
- Meilleure gestion des liens Binance

## Installation

1. **Sauvegarder** les fichiers originaux:
   ```bash
   cp btc_range_v1/logic.py btc_range_v1/logic.py.bak
   cp btc_range_v1/config.py btc_range_v1/config.py.bak
   cp config/btc_range.yaml config/btc_range.yaml.bak
   cp indicators/basic.py indicators/basic.py.bak
   cp dashboard_btc_range/app.py dashboard_btc_range/app.py.bak
   cp dashboard/static/app.js dashboard/static/app.js.bak
   ```

2. **Copier** les nouveaux fichiers:
   ```bash
   # Extraire le zip dans le repo
   unzip bot_modifications.zip -d /chemin/vers/ton/repo/
   ```

3. **Installer** la dependance pandas (si pas deja fait):
   ```bash
   pip install pandas
   ```

4. **Tester** en dry-run:
   ```bash
   export BTC_RANGE_DRY_RUN=1
   export BTC_RANGE_PROFILE=default
   python -m btc_range_v1.main
   ```

5. **Surveiller** les logs pendant 24-48h avant de passer en reel.

## Parametres recommandes

### Pour commencer (conservateur):
```yaml
profile: conservative
maxUsdcPerTrade: 50
trailStopPct: 0.003
minRewardRisk: 2.0
```

### Une fois stable (default):
```yaml
profile: default
maxUsdcPerTrade: 75
trailStopPct: 0.004
minRewardRisk: 1.5
```

## Monitoring

Verifier ces metriques dans le dashboard:
- **Win rate**: Doit etre > 45%
- **Profit factor**: Doit etre > 1.3
- **Max drawdown**: Ne doit pas depasser 5% du capital
- **Trades/jour**: 3-8 trades est ideal

## Rollback

Si probleme, restaurer les backups:
```bash
cp btc_range_v1/logic.py.bak btc_range_v1/logic.py
cp btc_range_v1/config.py.bak btc_range_v1/config.py
# etc.
```


## PATCH pour main.py

Le fichier `main.py` est trop volumineux pour être inclus dans le zip. 
Un fichier `PATCH_main_py.md` détaille les modifications exactes à apporter.

### Modifications clés:
1. **Import** des fonctions Range V1
2. **Fonction** `save_status_json()` pour le dashboard
3. **Buffer** klines pour l'analyse de range
4. **Analyse** de range à chaque itération
5. **Logging** des métriques range dans CHK
6. **Sauvegarde** du status JSON pour le dashboard
7. **Signal** d'entrée Range V1
8. **Validation** Range V1 avant entrée

### Application rapide:
```bash
cd /mnt/data/Trade/binance-aifout-bot/
# Ouvrir PATCH_main_py.md et appliquer les modifications manuellement
# Ou utiliser sed pour les remplacements simples
```
