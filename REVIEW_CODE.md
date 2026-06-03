# REVIEW_CODE.md — Phase 1 Audit v3
**Date :** 2026-06-03  
**Base :** `main.py` (2458 lignes), branche `main`

---

## 1.1 Vérification des bugs B1→B6

### B1 — `import json` manquant pour `save_status_json()`
**CORRIGÉ.** `import json` présent ligne 5.  
Résidu inoffensif : `import json as _json` redondant ligne 1968 (peut être supprimé).

### B2 — `_reexec_to_symbol()` appelle `logTrade` hors scope
**CORRIGÉ.** Signature ligne 581 : `def _reexec_to_symbol(..., *, log_fn=None)`.  
Appelants passent `log_fn=logTrade` (lignes 573, 932).

### B3 — `lastTickSeq = 0` rate le premier tick
**CORRIGÉ.** Ligne 773 : `lastTickSeq = -1`.

### B4 — `pos.entry = float(bid)` adopte un faux prix d'entrée
**CORRIGÉ.** Lignes 958–968 : le code tente de récupérer l'entry depuis `active_position.json`.  
Si introuvable, `pos.entry` reste 0.0 et le bot sort en `WALLET_UNTRACKED` (ligne 2076) sans inventer un prix.  
⚠️ **Résidu mineur** : ligne 2085, si `load_dynamics` échoue, `init_dynamics(float(pos.entry), ..., bid)` est appelé avec `bid` comme `current_bid` (3e arg) — c'est le bid observé, pas l'entry. Acceptable car `current_bid` sert à initialiser `maxiprice`, pas `entry_price`.

### B5 — PnL CSV calculé sans frais Binance
**PARTIELLEMENT CORRIGÉ.** La valeur de `pnl` dans le CSV provient de `_pnl_detail["net_pnl"]` (ligne 2366) qui passe par `FeeModel.compute_net_pnl()` ✓.  
**Gap restant :** la colonne s'appelle toujours `pnl`, pas `pnl_net`. Pas de colonne `pnl_net` séparée. Le dashboard et les outils d'analyse qui lisent `pnl` ignorent potentiellement que c'est déjà net.  
**Fix Phase 2 :** renommer ou ajouter colonne `pnl_net`.

### B6 — Sampling ticks non équitemporal
**EXISTE TOUJOURS.** Les ticks ne sont ajoutés que lors d'un `has_new_tick` (changement de `tick_seq`). En marché plat, plusieurs cycles de boucle s'écoulent sans nouveau tick, créant des gaps temporels invisibles dans `bidTicks`. Le burst window utilise `bidTicks[-1][0] - bidTicks[-lookback][0]` pour calculer l'elapsed, ce qui peut produire des fenêtres > `burst_max_window_sec` même si le signal est récent.  
**Fix Phase 6 :** documenter/corriger en ajoutant un tick horodaté même si le prix est identique.

---

## 1.2 Inventaire des fonctions de signal

| Fonction | Présente | Utilisée dans flux | Flag config | Verdict |
|---|---|---|---|---|
| `momentum_ok` | ✅ L.15 | ✅ L.999 | `momUseInstant=false` | **GARDÉE** — chemin principal |
| `instant_momentum_ok` | ✅ L.80 | ✅ L.991 | `momUseInstant=true` | **GARDÉE** — chemin alternatif |
| `p1p4_ok` | ❌ absente | ❌ | — | **N'EXISTE PAS** dans le code actuel |
| `tick_confirmation_ok` | ✅ L.106 | ✅ L.1446 | `tick_confirmation_enabled=false` | **GARDÉE** — inactive par défaut |
| `flow_pressure_ok` | ✅ L.127 | ✅ L.1472 | `flow_defense_enabled=false` | **GARDÉE** — inactive par défaut |
| `descending_tape_ok` | ✅ L.167 | ✅ L.276 | interne burst_exit | **GARDÉE** — appelée par burst_exit_reason |
| `burst_entry_signal` | ✅ L.178 | ✅ L.1010 | `burst_entry_enabled` | **GARDÉE** — signal principal |
| `burst_exit_reason` | ✅ L.263 | ✅ L.2111 | actif | **GARDÉE** — exit management |
| `orderbook_imbalance_ok` | ❌ absente | ❌ | — | **N'EXISTE PAS** dans le code actuel |
| `computeSignals` | ✅ indicateurs | ✅ L.801 | actif | **GARDÉE** — calcule RSI/EMA |

**Pas de dead code signal** : les fonctions présentes sont toutes connectées au flux (certaines sous flags désactivés par défaut).

---

## 1.3 Inventaire des calculs PnL

| Fichier | Ligne | Méthode | Frais inclus ? |
|---|---|---|---|
| `main.py` | 2362–2366 | `_fee_model.compute_net_pnl(entry, qty, exitPx, filledQty)` | ✅ OUI |
| `main.py` | 2267–2270 | Calcul inline pour exit guard (`_exp_pnl_net`) | ✅ OUI |
| `main.py` | 2390 | `logTrade(f"SELL ... pnl={pnl}")` | ✅ net_pnl |
| `main.py` | 2398, 2426 | `"pnl": pnl` dans CSV/JSON exit | ✅ net_pnl — mais colonne nommée `pnl` |
| `tools/pnl_audit.py` | multiple | Lit colonne `pnl` depuis CSV | ✅ si CSV est net |
| `dashboard/app.py` | multiple | Lit colonne `pnl` depuis DB/CSV | ✅ si CSV est net |
| `state/token_quality.py` | rebuild | Lit `pnl` depuis CSV, passe dans `compute_quality_score` | ✅ si CSV est net |

**Conclusion :** Depuis l'intégration de `FeeModel`, tous les PnL passent par les frais. Le seul gap est l'absence de colonne `pnl_net` distincte — la colonne `pnl` contient le net sans que ce soit explicite.

---

## 1.4 Inventaire des sorties (exits)

| Mode | Condition de déclenchement | PnL net garanti positif ? |
|---|---|---|
| **TP** | `price >= pos.tp` (entry × 1.008) | ✅ OUI si arming correct |
| **TRAIL** | `pos.armed` et `price <= pos.stop` (stop ≥ entry × 1.0015) | ✅ OUI — stop ≥ breakeven |
| **PROTECT** | `pos.protectArmed` et `price <= stop` | ⚠️ PARFOIS — stop peut être < fees si protect_lock_pct < fee_rate×2 |
| **STOP** | `price <= pos.stop` (stop = entry × 0.992) | ❌ NON — hard stop, perte voulue |
| **TIME** | `age ≥ maxPosTime` ET `price ≥ entry*(1+feeBufPct)` | ✅ OUI — breakeven garanti |
| **TIME_HARD** | `age ≥ hardMaxPosTime` (1200s) | ❌ NON — sortie forcée sans condition |
| **BURST_FAIL** | Entrée BURST + perte > fail_loss_pct en < fail_ttl_sec | ❌ NON — cut loss rapide intentionnel |
| **BURST_REVERSAL** | Peak significatif puis retournement descending tape | ❌ NON — peut couper en perte |
| **PSELL FAST** | Perdu tôt, descending tape | ⚠️ BLOQUÉ par exit guard (L.2264) si net < 0 |
| **PSELL STALE** | Trade stagnant, dans rouge | ⚠️ BLOQUÉ par exit guard (L.2264) si net < 0 |
| **PSELL FAIL** | `age ≥ psell_fail_age_sec (100s)`, perte > psell_fail_loss_pct | ❌ **NON — ET PAS BLOQUÉ** |
| **WALLET_UNTRACKED** | `pos.entry == 0` après wallet sync | ❌ NON — liquidation d'urgence |
| **TOKEN_SWITCH** | `.service.env` change de symbol | ❌ NON — sortie mid-position |

### 🚨 Gap critique : PSELL FAIL non protégé

**Ligne 2264 :** `_non_critical_exits = ("TIME", "PROTECT", "PSELL STALE", "PSELL FAST")`

`PSELL FAIL` n'est PAS dans cette liste. Le guard `if _is_non_critical and ... _exp_pnl_net < 0` (L.2266–2275) ne s'applique pas à `PSELL FAIL`.  
→ Le bot sort en perte nette sur PSELL FAIL même si le prix est au-dessus de la vraie zone de profit.  
**Fix Phase 6 :** ajouter `"PSELL FAIL"` aux exits gardés.

---

## 1.5 État des modules Phase 2→5 (déjà partiellement implémentés)

| Module | Statut actuel |
|---|---|
| `execution/fee_model.py` (FeeModel) | ✅ Implémenté, utilisé dans main.py |
| `execution/fee_calculator.py` (FeeCalculator spec v3) | ❌ Absent — `fee_model.py` existe avec API similaire |
| `state/token_quality.py` | ✅ Implémenté |
| `state/token_quality.json` | ⚠️ Présent mais 5 tokens toxiques NON bloqués (historique insuffisant) |
| `strategy/pic_filter.py` | ✅ Implémenté, importé dans main.py |
| `strategy/pic_filter` actif | ⚠️ Sous `picFilter_enabled` — **désactivé par défaut dans risk.yaml** |
| `strategy/position_dynamics.py` | ✅ Implémenté, utilisé |
| `tools/rebuild_token_quality.py` | ❌ Absent |
| `tools/reset_stats.py` | ❌ Absent |

---

## Résumé priorités Phase 2→7

1. **Phase 2** : Adapter `FeeModel` → interface `FeeCalculator` spec v3, ajouter colonne `pnl_net` au CSV, exposer `is_profitable_to_exit()`.
2. **Phase 3** : Créer `tools/rebuild_token_quality.py`, scanner logs historiques, vérifier SAHARAUSDC/GUNUSDC/JTOUSDC/TONUSDC/PENDLEUSDC `blocked=True`.
3. **Phase 4** : Activer `picFilter_enabled: true` par défaut dans `risk.yaml`.
4. **Phase 5** : Position dynamics déjà en place — vérifier trailing/breakeven sur cas réels.
5. **Phase 6** : Ajouter `PSELL FAIL` au exit guard. Fix B6 sampling. Nettoyer résidus.
6. **Phase 7** : `tools/reset_stats.py`, tests intégration.

---

**STOP. Attends "GO Phase 2" pour continuer.**
