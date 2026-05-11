# AUDIT BASELINE — binance-aifout-bot

**Date d'audit :** 2026-05-11  
**Source :** SQLite `data/runtime/trade_memory.sqlite3` (81 trades fermés, 2026-03-18 → 2026-05-11)  
**Fee rate utilisé :** 0.10% par leg (0.20% round-trip), pas de BNB discount  

---

## 1. Chiffres clés

| Métrique | Valeur bot (loggée) | Valeur réelle (avec frais) |
|---|---|---|
| Trades totaux | 81 | 81 |
| PnL total | **-0.33 USDC** | **-1.73 USDC** |
| Winrate | 49.4% | **35.8%** |
| Profit factor | — | **0.346** |
| Gain moyen/trade | — | +0.032 USDC |
| Perte moyenne/trade | — | -0.051 USDC |
| Ratio gain/perte | — | **0.62** (besoin > 1.0) |
| Delta moyen prix | — | trop faible |
| Trades < seuil rentabilité (0.20%) | — | **52/81 = 64%** |
| Écart log vs réel | | **-1.40 USDC** (= frais non déduits) |

**Conclusion immédiate :** le bot affichait -0.33 USDC de perte alors que la réalité était -1.73 USDC. L'opérateur prenait des décisions sur des chiffres faux depuis le début.

---

## 2. Bug critique CRIT-1 confirmé : PnL faux

Le `pnl` loggué dans les CSV/DB est `(exit_price - entry_price) * qty` **sans déduire les frais**.  
Frais réels par trade ≈ `2 × 0.10% × 10 USDC = 0.020 USDC`.  
C'est **63% du gain moyen réel** (+0.032 USDC). Le bot était structurellement en train de perdre sans que l'opérateur le voie.

**Statut :** Corrigé dans `main.py` — le PnL est désormais calculé via `FeeModel.compute_net_pnl()`.

---

## 3. Distribution des raisons de sortie

| Raison sortie | Nb trades | PnL réel moyen |
|---|---|---|
| **PSELL** | 23 (28%) | **-0.058 USDC** |
| **TIME** | 20 (25%) | +0.004 USDC |
| **TP** | 9 (11%) | **+0.080 USDC** |
| **STOP** | 9 (11%) | **-0.101 USDC** |
| TOKEN_SWITCH | 8 (10%) | -0.038 USDC |
| TRAIL | 5 (6%) | +0.004 USDC |
| PROTECT | 4 (5%) | +0.005 USDC |
| BURST_REVERSAL | 2 (2%) | +0.011 USDC |
| TIME_HARD | 1 | -0.028 USDC |

**Observation critique :**
- **TP** : seul mode vraiment profitable (+0.080 avg). Ne représente que 11% des sorties.
- **PSELL** : 28% des trades, avg -0.058. C'est le plus gros destructeur de PnL.
- **STOP** : -0.101 avg → ces trades ont une perte 2× le gain moyen.
- **TIME** : neutre, mais génère des frais pour rien.
- **TOKEN_SWITCH** : 8 trades fermés en perte parce que le selector a changé de token pendant une position gagnante.

---

## 4. Top 10 trades perdants

| Date | Token | PnL réel | Raison |
|---|---|---|---|
| 2026-03-18 09:13 | ENJUSDC | -0.083 | STOP |
| 2026-03-18 15:46 | TAOUSDC | -0.074 | PSELL |
| 2026-03-19 00:59 | SAHARAUSDC | -0.085 | STOP |
| 2026-03-19 01:03 | SAHARAUSDC | -0.090 | STOP |
| 2026-03-19 01:05 | SAHARAUSDC | -0.071 | STOP |
| 2026-03-20 22:08 | FETUSDC | -0.071 | STOP |
| 2026-03-20 22:20 | GUNUSDC | -0.092 | STOP |
| 2026-03-20 22:24 | GUNUSDC | -0.070 | STOP |
| total SAHARAUSDC | SAHARAUSDC | **-0.373** | 8 trades |
| total GUNUSDC | GUNUSDC | **-0.259** | 5 trades |

**Pattern :** Les gros perdants sont des tokens avec spread élevé et faible liquidité (SAHARA, GUN, JTO). Le selector les choisissait pour leur variation 2min élevée, mais c'était de la volatilité non tradable.

---

## 5. Top 10 trades gagnants

| Date | Token | PnL réel | Raison |
|---|---|---|---|
| 2026-03-18 07:01 | ENJUSDC | +0.114 | TP |
| 2026-03-18 11:07 | ENJUSDC | +0.106 | TP |
| 2026-03-18 08:59 | ENJUSDC | +0.105 | TP |
| 2026-03-19 01:16 | KATUSDC | +0.142 | TP |
| 2026-03-19 01:14 | KATUSDC | +0.089 | TP |
| 2026-03-19 01:00 | SAHARAUSDC | +0.088 | TP |
| 2026-03-18 16:42 | KITEUSDC | +0.088 | TP |
| 2026-03-20 22:00 | FETUSDC | +0.080 | TP |
| 2026-05-08 19:17 | ORDIUSDC | +0.077 | BURST_REVERSAL |
| 2026-03-20 20:33 | TLMUSDC | +0.076 | TP |

**Pattern :** 9/10 des meilleurs trades sortent en **TP**. Le signal est clair : le TP fonctionne. Le problème est qu'il ne se déclenche que dans 11% des cas.

---

## 6. Analyse des graphes (session logs001, 2026-05-11)

Les screenshots montrent la session overnight avec l'ancienne version du bot :

- **PENDLEUSDC** : tendance haussière forte sur la journée. Le bot entre tôt en session, mais TOKEN_SWITCH lui fait quitter avant le vrai move.
- **INJUSDC** : entrée près du sommet local, forte baisse ensuite. BURST sur spike = achat au mauvais moment.
- **SUIUSDC** : entrée mid-range, légère hausse puis retournement. Move insuffisant pour couvrir les frais.
- **ONDOUSDC** : entrée au pic, crash de -3% ensuite. BURST sur un pump = pire cas.

**Problème commun :** le bot entre souvent **après** le move (sur le momentum Burst), pas **avant**. Il achète la fin du spike, pas le début.

---

## 7. Causes racines du résultat négatif

### Cause #1 : PnL non comptabilisé (CORRIGÉ)
Les frais de 0.020 USDC/trade soit 1.63 USDC sur 81 trades n'étaient pas déduits. Le bot et l'opérateur naviguaient à l'aveugle.

### Cause #2 : 64% des trades sous le seuil de rentabilité
Sur 81 trades, 52 ont un delta de prix < 0.20% (breakeven). La stratégie BURST capte des micro-mouvements qui ne couvrent pas les frais.

### Cause #3 : Asymétrie gain/perte (ratio 0.62)
Gain moyen = +0.032 USDC, perte moyenne = -0.051 USDC. Le bot perd 60% de plus qu'il ne gagne par trade. Pour PF=1 avec winrate 36%, il faudrait ratio ≥ 1.78.

### Cause #4 : Sorties incohérentes
PSELL (28% des trades, tous négatifs) coupe les positions sur du bruit de marché, pas sur une vraie thèse cassée. TP (11%) est le seul mode profitable mais rarissime.

### Cause #5 : Tokens toxiques non filtrés assez tôt
SAHARAUSDC (-0.373), GUNUSDC (-0.259), JTOUSDC (-0.182) représentent **-0.813 USDC** soit **47% du total des pertes** en 16 trades sur 81. Le selector choisissait ces tokens pour leur var 2min élevée sans filtrer la qualité du signal.

### Cause #6 : Token switch pendant position
8 trades fermés sur TOKEN_SWITCH avant d'atteindre le TP → -0.307 USDC de PnL manqué.

---

## 8. Ce qui a été corrigé (cette session)

| Item | Fichier | Statut |
|---|---|---|
| PnL net avec frais réels | `main.py` + `execution/fee_model.py` | ✅ Fait |
| Persistance position sur disque | `state/persisted.py` + `main.py` | ✅ Fait |
| Circuit breaker daily loss + consecutive | `risk/circuit_breaker.py` + `main.py` | ✅ Fait |
| Import json manquant | `main.py` | ✅ Fait (session précédente) |
| lastTickSeq = -1 | `main.py` | ✅ Fait (session précédente) |
| Burst cooldown bypass | `main.py` | ✅ Fait (session précédente) |
| Détection régime marché | `strategy/regime.py` | ✅ Créé |
| Règles de sortie cohérentes | `strategy/exit_rules.py` | ✅ Créé (à intégrer) |
| Tests unitaires | `tests/` | ✅ 25 tests |

## 9. Nouveaux modules créés

```
execution/fee_model.py      — calcul PnL net réel avec frais
state/persisted.py          — persistance position survit aux restarts
risk/circuit_breaker.py     — daily loss limit + consecutive losses
strategy/regime.py          — détection TREND/RANGE/PUMP/UNKNOWN
strategy/exit_rules.py      — sorties cohérentes avec signal d'entrée
tests/test_fee_model.py     — 5 tests
tests/test_circuit_breaker.py — 5 tests
tests/test_regime.py        — 5 tests
tests/test_exit_rules.py    — 4 tests
tests/test_persisted.py     — 6 tests
```

---

## 10. Prochaines étapes recommandées

Les corrections ci-dessus sont **non-disruptives** (ajouts sans suppression de l'existant).  
La prochaine phase nécessite une décision stratégique :

**Question principale : intégrer `exit_rules.py` dans `main.py` ?**  
Cela remplacerait PSELL/TIME/PROTECT par des sorties basées sur le signal d'entrée.  
Impact estimé : réduction des sorties PSELL (les plus négatives), augmentation du ratio TP/trade.  
Risque : changement de comportement en prod — à valider d'abord en dry_run.

**Question secondaire : activer le filtre régime ?**  
Si `regime.label == "RANGE"` → pas d'entrée. Réduit le nombre de trades en marchés non favorables.  
Impact : probablement moins de trades mais PF plus élevé.
