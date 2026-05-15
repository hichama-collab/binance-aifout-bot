# REVIEW_CODE.md — Binance AiFout Bot
> Produit le 2026-05-15 • Phase 1 du plan CLAUDE_CODE_PROMPT_FINAL.md  
> **À valider avant toute modification de code.**

---

## 1. Bugs identifiés

### BUG-01 🔴 CRITIQUE — `logTrade` NameError silencieuse dans `_reexec_to_symbol()`

**Fichier** : `main.py` — L600-606  
**Code** :
```python
def _reexec_to_symbol(current_symbol, new_symbol, *, source=".service.env"):
    msg = f"TOKEN_SWITCH old={current_symbol} new={new_symbol} source={source}"
    print(msg)
    try:
        logTrade(msg)   # ← NameError : logTrade est locale à main()
    except Exception:
        pass            # ← avalé silencieusement
    os.execv(...)
```
`logTrade` est un callable défini à l'intérieur de `main()` (L659+). Appelée depuis une fonction module-level, elle génère un `NameError` capturé sans trace. **Chaque changement de token ne laisse aucune trace dans les CSV de trades.**

**Fix** : Passer `logTrade` en paramètre optionnel : `def _reexec_to_symbol(..., log_fn=None)` et appeler `if log_fn: log_fn(msg)`.

---

### BUG-02 🔴 CRITIQUE — Entry adoptée au prix spot après crash (wallet orphelin)

**Fichier** : `main.py` — L975-994  
**Code** :
```python
if pos is not None and getattr(pos, 'entry', 0.0) == 0.0:
    # Tente de récupérer depuis active_position.json
    adopted_entry = _recovered_entry if _recovered_entry > 0 else float(bid)
    pos.entry = adopted_entry   # ← bid actuel si fichier absent
    pos.high = float(bid)
    pos.stop = 0.0
    pos.init_stops(cfg, profile, tick=tick)
```
Si `active_position.json` est absent (premier crash, ou fichier supprimé) et que le wallet contient un actif acheté à, disons, 3.65 USDC, le bot adopte le `bid` actuel comme entry. Si le marché a bougé, toute la logique PROTECT/PSELL/TRAILING sera calibrée sur le mauvais prix. **Risque de ne jamais couper une perte réelle.**

**Fix** : Si entry ne peut pas être récupérée → logguer une alerte critique et passer en mode IDLE (ne pas trader) plutôt qu'adopter le bid.

---

### BUG-03 🟡 MOYENNE — Deux clés config différentes pour le taux de frais

**Fichier** : `main.py` — L744 vs L2218  
```python
# L744 — FeeModel
_fee_model = FeeModel(fee_rate=float(getattr(cfg, 'defaultFeeRate', 0.001)))

# L2218 — Filtre exit non-critique
_fee_rate_exit = float(getattr(cfg, 'feeRate', 0.001) or 0.001)
```
`defaultFeeRate` vs `feeRate` : si une seule de ces clés est définie dans la config, les calculs divergent. En pratique les deux valent 0.001 par défaut, donc pas d'impact aujourd'hui, mais c'est une bombe à retardement.

**Fix** : Toujours utiliser `_fee_model.fee_rate` pour le filtre exit.

---

### BUG-04 🟡 MOYENNE — `save_status_json` écrit dans un fichier `btc_range_v1_status.json`

**Fichier** : `main.py` — L633-636  
```python
def save_status_json(runtime_dir: Path, data: dict):
    tmp = runtime_dir / "btc_range_v1_status.json.tmp"
    tmp.replace(runtime_dir / "btc_range_v1_status.json")  # ← nom du sous-bot BTC
```
Ce fichier est dans le bot principal mais écrit dans un fichier nommé pour le sous-bot `btc_range_v1`. Probablement un copier-coller. Le dashboard ou l'opérateur qui cherche le statut du bot principal ne trouvera pas le bon fichier.

**Fix** : Renommer en `bot_status.json`.

---

### BUG-05 🟢 FAIBLE — Double `return False` (dead code) dans `momentum_v1.py`

**Fichier** : `strategy/momentum_v1.py` — L43-47  
```python
if mom_pct < self.momMinPct:
    return False
    return False   # ← jamais exécuté (L44)
if float(ind.get('up_ratio', 0.0)) < self.momMinUpRatio:
    return False
    return False   # ← jamais exécuté (L47)
```
Code mort. Probablement un merge accident. Aucun impact fonctionnel.

**Fix** : Supprimer les lignes 44 et 47.

---

### BUG-06 🟢 FAIBLE — `lastTickSeq` initialisé à -1 (spec indiquait 0)

**Fichier** : `main.py` — L789  
```python
lastTickSeq = -1
```
Initialisé à -1, pas 0. Si `tick_seq` démarre à 0, le premier tick est bien capturé (0 ≠ -1). **Ce n'est pas un bug** par rapport à la spec — en fait c'est la bonne valeur. Noté ici pour clarté.

---

## 2. Dead code

| Fonction | Fichier | Lignes | Appelée ? | Verdict |
|----------|---------|--------|-----------|---------|
| `p1p4_ok()` | `main.py` | 105-117 | ❌ Jamais (la clé `p1p4_ok` dans l'indicateur dict vient d'ailleurs) | **Supprimer** |
| `orderbook_imbalance_ok()` | `main.py` | 362-376 | ❌ Jamais | **Supprimer** |
| `momentum_ok()` | `main.py` | 14-78 | ✅ L1011 | Utilisée |
| `instant_momentum_ok()` | `main.py` | 79-103 | ✅ L1003 | Utilisée |
| `tick_confirmation_ok()` | `main.py` | 119-138 | ✅ L1442 | Utilisée |
| `flow_pressure_ok()` | `main.py` | 140-178 | ✅ L1468 | Utilisée |
| `descending_tape_ok()` | `main.py` | 180-189 | ✅ L289 | Utilisée |
| `burst_entry_signal()` | `main.py` | 191-274 | ✅ L1022 | Utilisée |
| `burst_exit_reason()` | `main.py` | 276-347 | ✅ L2066 | Utilisée |
| `computeSignals()` | `indicators/basic.py` | — | ✅ L817 | Utilisée |

---

## 3. Magic numbers non configurables

| Ligne | Valeur | Contexte | Devrait être en config ? |
|-------|--------|----------|--------------------------|
| L1813 | `0.0025` | `buy_safety_buf = max(fee_buf, 0.0025)` — plancher du buffer achat | ✅ Oui → `buySafetyBufFloor` |
| L2125 | `0.0020` | cap max de `psellFailMaxHighPct` quand non configuré | ✅ Oui → `psellFailMaxHighPctCap` |
| `exit_rules.py` L52 | `0.008` | `max_loss_pct` par défaut | ✅ Oui → déjà via `cfg.maxLossPct` avec default |
| `exit_rules.py` L53 | `0.004` | `min_tp_pct` par défaut | ✅ Oui → déjà via `cfg.minTpPct` |
| `exit_rules.py` L65 | `0.008` | seuil pump reversal peak drop | ✅ Oui → `pumpReversalDropPct` |
| L695 | `0.003` | `momRangeMinPct` default | ✅ Oui → déjà via `cfg.momRangeMinPct` |

**Bilan** : Les seuils BURST sont bien configurables via `cfg` (L224-271). Les 2 valeurs hardcodées les plus importantes sont L1813 et L2125.

---

## 4. Race conditions

### RC-01 — `os.execv()` perd le ticks buffer

**Fichier** : `main.py` L607  
Lors d'un switch de token, `os.execv()` remplace entièrement le processus. Le buffer de ticks (`bidTicks`, `ticks`) est perdu. Au redémarrage, le bot doit accumuler un minimum de ticks avant de pouvoir décider d'entrer (warmup). **Non critique** car c'est le comportement attendu — le bot attend simplement avant de trader.

### RC-02 — Wallet sync vs position locale (délai potentiel)

**Fichier** : `state/wallet_sync.py`  
`walletSyncEvery()` peut mettre à jour `pos.qty` si détection de divergence. Interval par défaut : plusieurs secondes. Entre deux syncs, si une vente partielle externe survient, le PnL calculé sur la position locale est faux.

**Impact** : Faible en pratique (pas de trading manuel simultané) mais noté comme point de fragilité.

### RC-03 — `_maybe_reexec_on_token_change()` durant une position ouverte

**Fichier** : `main.py` autour de L1500-1620  
Si `.service.env` change pendant qu'une position est ouverte, `_reexec_to_symbol()` appellera `os.execv()` — redémarrant le bot avec une position non fermée. Le bot reprend via `wallet_sync` et la logique de récupération entry (BUG-02 ci-dessus). En l'état, si `active_position.json` est présent et cohérent, ça passe. Si absent → BUG-02.

---

## 5. Calcul des frais — État réel

| Endroit | Fichier | Frais déduits ? | Correct ? |
|---------|---------|-----------------|-----------|
| PnL réel au SELL | `main.py` L2317-2321 | ✅ Via `_fee_model.compute_net_pnl()` | ✅ OUI |
| CSV colonne `pnl` | `main.py` L2354 | ✅ `pnl = net_pnl` depuis fee_model | ✅ OUI |
| Filtre exit non-critique | `main.py` L2218-2225 | ✅ Déduits, formule correcte | ⚠️ Clé config différente (BUG-03) |
| `exit_rules.py` estimation | `strategy/exit_rules.py` L24-26 | ✅ Formule équivalente à fee_model | ✅ OUI |
| Dashboard / stats | via `core/trade_memory.py` | ✅ Lit les CSV nets | ✅ OUI |

**Bonne nouvelle** : le PnL enregistré dans les CSV est déjà net de frais. Pas de retraitement nécessaire pour l'historique.

---

## 6. Structure des modules — État général

| Module | État | Notes |
|--------|------|-------|
| `execution/fee_model.py` | ✅ Propre | Modèle complet, BNB discount, bien structuré |
| `strategy/exit_rules.py` | ✅ Propre | Timeout bloqué si PnL négatif ✅, logique saine |
| `strategy/momentum_v1.py` | ✅ Sain | Double return False à nettoyer (BUG-05) |
| `state/position.py` | ✅ Propre | Stops, high, protection bien implémentés |
| `state/persisted.py` | ✅ Propre | Atomicité via tmp file ✅ |
| `state/wallet_sync.py` | ✅ Sain | RC-02 à noter |
| `core/trade_memory.py` | ✅ Propre | SQLite, cache dashboard, correct |
| `TokenProfileSelector.py` | ✅ Sain | Pas de quality score historique (objet de la Phase 2) |
| `main.py` | ⚠️ Voir bugs | BUG-01 et BUG-02 critiques à corriger avant Phase 2+ |

---

## 7. Récapitulatif priorisé

| Priorité | ID | Quoi | Où | Impact |
|----------|----|------|----|--------|
| 🔴 P0 | BUG-01 | `logTrade` NameError dans `_reexec_to_symbol` | main.py L604 | Logs TOKEN_SWITCH perdus |
| 🔴 P0 | BUG-02 | Entry adoptée au bid après crash sans fichier | main.py L987-988 | Risk capital si crash |
| 🟡 P1 | BUG-03 | Deux clés fee rate différentes | main.py L744 vs L2218 | Incohérence silencieuse |
| 🟡 P1 | BUG-04 | `btc_range_v1_status.json` mauvais nom | main.py L633 | Dashboard confusion |
| 🟢 P2 | BUG-05 | Double `return False` mort | momentum_v1.py L44, L47 | Qualité code |
| 🟢 P2 | Dead | `p1p4_ok()`, `orderbook_imbalance_ok()` | main.py L105, L362 | Dead code |
| 🟢 P3 | Magic | `0.0025` et `0.0020` non configurables | main.py L1813, L2125 | Flexibilité |

---

## 8. Ce qui N'est PAS un bug (contraire à ce que laissait penser la spec)

- **`import json` manquant** : `json` est importé au niveau module (L5). ✅ Pas de bug.
- **`lastTickSeq = 0`** : En réalité c'est `-1`. ✅ Le premier tick est bien capturé.
- **PnL CSV ne déduit pas les frais** : En réalité le CSV contient déjà le `net_pnl`. ✅ Correct.
- **Formule fees dans `exit_rules.py`** : Mathématiquement identique à `fee_model.py`. ✅ Correct.

---

**En attente de validation avant de passer à la Phase 2 (Token Quality Score).**
