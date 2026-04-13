# Burst Mode Handoff

Date: 2026-04-10

## But

Ajouter un mode d'entree "burst" pour capter les montees tres fortes et tres rapides, sans se faire bloquer par les filtres lents `mom / EMA / RSI / vol`.

## Idee retenue

- Entree normale conservee: logique `P1..P4` + filtres lents.
- Nouveau mode `BURST` prioritaire:
  - utilise les derniers ticks `BID`
  - fenetre courte en ticks + temps max
  - mesure:
    - move net
    - vitesse
    - efficacite du move
    - ratio pression haussiere / baissiere
    - pire claque intermediaire
- Si `BURST` valide:
  - achat immediat
  - bypass des filtres lents d'entree
  - cross spread force si active

## Fichiers modifies

- `core/config.py`
- `config/risk.yaml`
- `main.py`

## Reglages burst actuels

Dans `config/risk.yaml` profil `major`:

- `burst_entry_enabled: true`
- `burst_lookback_ticks: 4`
- `burst_max_window_sec: 4.0`
- `burst_min_return_pct: 0.00020`
- `burst_min_move_vs_spread: 4.0`
- `burst_min_velocity_pct_per_sec: 0.00005`
- `burst_min_efficiency: 0.55`
- `burst_min_pressure_ratio: 1.8`
- `burst_max_single_drop_pct: 0.00010`
- `burst_spread_max_mult: 1.6`
- `burst_force_cross_spread: true`
- `burst_exit_confirm_ticks: 3`
- `burst_exit_giveback_mult: 0.55`
- `burst_exit_min_drawdown_pct: 0.00010`
- `burst_exit_drawdown_vs_spread: 6.0`
- `burst_fail_ttl_sec: 8.0`
- `burst_fail_loss_pct: 0.0025`
- `burst_fail_loss_vs_spread: 2.5`
- `burst_reversal_min_peak_pct: 0.0012`
- `burst_follow_ttl_sec: 4.0`
- `burst_follow_min_extension_pct: 0.00008`
- `burst_exit_under_entry_pct: 0.00004`

## Logs utiles a inspecter

Chercher dans les logs:

- `BURST_TRIGGER`
- `BUY ... mode=BURST`
- `BURST_FAIL`
- `BURST_REVERSAL`
- `BURST_HANDOFF`
- `BUY_NOFILL`
- `SELL_NOFILL`

## Ce qu'on voudra evaluer apres test

1. Est-ce que des `BURST_TRIGGER` apparaissent vraiment sur BTC.
2. Est-ce qu'ils se transforment en `BUY_FILLED`.
3. Si oui, est-ce que les sorties burst sont:
   - trop rapides sur rejet mineur
   - trop lentes sur vrai retournement
   - ou bien dosees
4. Est-ce qu'il faut:
   - baisser `burst_min_return_pct`
   - baisser/monter `burst_min_pressure_ratio`
   - assouplir/durcir `burst_exit_giveback_mult`
   - ajuster `burst_fail_ttl_sec`
   - ajuster `burst_fail_loss_pct`
   - ajuster `burst_reversal_min_peak_pct`

## Intention algo

Le but n'est pas de compter des ticks verts.
Le but est de detecter une impulsion exploitable:

- rapide
- propre
- dominante
- peu cassee

## Si on reprend plus tard

Repartir des nouveaux logs de test, pas des intuitions.
La prochaine iteration doit etre du tuning de seuils, pas une re-ecriture complete.

## Analyse du run 2026-04-13

- PnL total observe: environ `-0.3007 USDC`
- 44 trades fermes
- Gagnant net principal: `GIGGLEUSDC`
- Gros point faible: sorties `PSELL`

Sous-total des sorties:

- `PSELL_ROLL5`: tres negatif
- `PSELL_FAIL`: negatif
- `PROTECT` et `TRAIL`: positifs
- `BURST_REVERSAL`: faible impact negatif

Conclusion de lecture:

1. Le probleme principal n'est pas le nouveau `BURST_REVERSAL`.
2. Le mode `P` reste clairement negatif.
3. Le mode `BURST` est meilleur que `P`, mais il declenche encore sur des impulsions trop faibles sur les majors.
4. Les vrais gagnants sont les bursts rapides et forts.

## Correction appliquee apres analyse

Premiere correction posee le 2026-04-13:

- `p_entry_enabled: false` pour `major`
- `burst_min_return_vs_fee_buf: 0.8`
- `burst_min_velocity_pct_per_sec: 0.00060`

Intention:

- couper les entrees `P` qui ont sous-performe
- empecher les bursts trop petits pour couvrir reellement le cout microstructurel
- filtrer les bursts lents de type `AAVE/ETH/HBAR`

## Hypothese pour le prochain run

Le bot devrait:

- faire moins de trades
- eviter les bursts trop mous sur majors
- rester present sur les vraies accelerations

Si le prochain run reste negatif, prochaine etape probable:

- rendre le score burst moins degeneré que `eff=1 / pressure=999`
- soit avec plus de ticks
- soit avec une fenetre temps plus riche que les 4 derniers ticks

## Logique de sortie burst actuelle

- Le burst ne coupe plus un trade uniquement parce qu'il "n'a pas encore decollé".
- `BURST_FAIL` ne sert plus qu'a sortir un vrai rejet rapide:
  - dans une petite fenetre initiale
  - avec perte sous l'entree suffisamment nette
  - et tape descendante confirmee
- `BURST_REVERSAL` ne s'active qu'apres une extension minimale reelle.
- `BURST_HANDOFF` marque le moment ou le trade repasse en gestion standard (`stop/protect/trailing/PSELL`).

## Memo session complete

Contexte de cette session:

- Le bot a ete lance tres peu de temps puis stoppe volontairement avant un run plus long.
- Les logs de reference sont dans `data/logs/logs003`.
- Deux graphes reels ont servi de base a la reflexion:
  - `CFGUSDC`
  - `TAOUSDC`
- Objectif de cette iteration:
  - ne pas figer l'algo final
  - faire une version de tuning intelligente pour laisser tourner environ 8h
  - revenir ensuite avec logs + graphes pour corriger au plus juste

Constats tires des premiers vrais trades:

- `CFGUSDC`:
  - entree `BURST` detectee puis executee
  - sortie `BURST_STALL` trop rapide
  - visuellement, le token a ensuite continue a monter
  - conclusion: l'ancienne sortie burst coupait une respiration normale et confondait pause et echec
- `TAOUSDC`:
  - ce trade de reference n'etait pas un burst utile a suivre pour cette correction
  - la sortie observee etait surtout un cas de gestion standard / `PSELL`
  - conclusion: le vrai bug a traiter etait bien dans la logique de sortie specifique au mode `BURST`

Decision algo prise:

- garder l'entree burst telle quelle pour l'instant
- corriger prioritairement la sortie burst
- laisser plus d'air au trade une fois l'entree prise
- reserver la coupe rapide uniquement aux vrais rejets
- laisser ensuite la gestion standard reprendre la main si le trade tient un minimum

Ce qui a ete change dans cette session:

- suppression pratique de la logique `BURST_STALL`
- ajout d'un vrai `BURST_FAIL` de debut de trade:
  - petite fenetre temporelle
  - perte reelle sous l'entree
  - confirmation par tape descendante
- durcissement de `BURST_REVERSAL`:
  - pas de reversal tant qu'il n'y a pas eu une extension minimum reelle
- ajout d'un log `BURST_HANDOFF`:
  - sert a voir quand la gestion burst arrete d'etre prioritaire
  - permet de suivre le relais vers `PROTECT` / `TRAIL` / `PSELL`

Parametres importants pour cette nuit:

- `burst_fail_ttl_sec: 8.0`
- `burst_fail_loss_pct: 0.0025`
- `burst_fail_loss_vs_spread: 2.5`
- `burst_reversal_min_peak_pct: 0.0012`

Intention de ces reglages:

- tuer vite un vrai faux depart
- ne plus tuer un trade simplement parce qu'il respire
- laisser la position vivre si elle n'est pas clairement rejetee
- observer si la gestion standard protege correctement ensuite

Contexte run de nuit:

- capital spot prevu pour le test: environ `10 EUR`
- duree visee: environ `8h`
- but principal: observer le comportement, pas maximiser le PnL sur ce run

Quand on reprendra demain:

Me renvoyer:

- le dossier de logs de ce run
- les lignes contenant:
  - `BURST_TRIGGER`
  - `BUY_FILLED`
  - `BURST_FAIL`
  - `BURST_REVERSAL`
  - `BURST_HANDOFF`
  - `SELL_FILLED`
- les graphes / screenshots des tokens effectivement trades
- si possible les cas ou:
  - le trade a ete coupe trop tot
  - le trade aurait du etre coupe plus vite
  - le trade a bien ete laisse courir

Lecture attendue demain:

- si on voit beaucoup de `BURST_FAIL` suivis de vraies montees:
  - le fail est encore trop agressif
- si on voit des `BURST_HANDOFF` puis des belles protections:
  - la direction est probablement bonne
- si on voit des `BURST_REVERSAL` apres vraies extensions:
  - la logique commence a devenir saine
- si on voit encore des pertes longues sans rejet clair:
  - il faudra resserrer la bascule burst vers sortie

Philosophie pour la prochaine version:

- la version de cette nuit sert a collecter de la verite terrain
- la prochaine version devra etre la version "propre"
- elle devra etre construite a partir des logs reels, pas d'intuition seule
