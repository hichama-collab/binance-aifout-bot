BTC Range V1

But
- Ajouter un deuxieme bot complet dans le meme repo.
- Garder le bot actuel auto-token tel quel.
- Creer un bot beaucoup plus simple, dedie a `BTCUSDC`, base sur un range local plutot que sur des bursts multi-tokens.

Decision de structure
- Pas de nouveau depot GitHub.
- Pas un simple profil du bot actuel.
- Meme repo, mais point d'entree separe:
  - bot actuel: `main.py`
  - bot BTC range: `python -m btc_range_v1.main`

Pourquoi
- La logique BTC range est differente du bot actuel:
  - mono-token
  - regime range / rebond
  - pas de rotation auto
  - pas de memoire toxicite
  - pas de logique burst / P-entry / multi-token
- La separer garde le bot actuel maintenable.

Principe de trading
- Contexte principal: range glissant en `5m`.
- Range de travail:
  - `low` = plus bas des dernieres `rangeWindowBars`
  - `high` = plus haut des dernieres `rangeWindowBars`
- Le bot n'entre que si:
  - la taille du range est ni trop petite ni trop grande
  - la derive generale du contexte reste raisonnable
  - le spread reste propre
  - le prix revient en zone basse du range
  - un petit rebond est visible sur les derniers ticks
- Le bot sort:
  - au target du range
  - au stop sous le bas du range
  - ou par time/stale exit si le trade ne vit pas

Fichiers
- `btc_range_v1/config.py`
- `btc_range_v1/logic.py`
- `btc_range_v1/main.py`
- `config/btc_range.yaml`
- `scripts/start-btc-range.sh`
- `scripts/stop-btc-range.sh`
- `dashboard_btc_range/app.py`
- `dashboard_btc_range/systemd/btc-range-botdash.service.example`
- `systemd/btc-range-bot.service.example`
- `.btc_range.env.example`

Lancement
- `./scripts/start-btc-range.sh`
- `./scripts/start-btc-range.sh BTCUSDC`

Variables utiles
- `BTC_RANGE_SYMBOL=BTCUSDC`
- `BTC_RANGE_PROFILE=default`
- `BTC_RANGE_DRY_RUN=1`
- `BTC_RANGE_CONFIG=config/btc_range.yaml`
- `BTC_RANGE_ENV_FILE=.btc_range.env`

Dashboard dedie
- port par defaut: `8100`
- env example: `dashboard_btc_range/botdash-btc-range.env`
- install venv: `./scripts/setup-btc-range-dashboard-venv.sh`

Attention importante
- Ce bot considere qu'il controle seul la position `BTC` du compte spot.
- Si tu detiens deja du BTC manuellement sur ce meme compte, il peut confondre ce solde avec sa propre position.
- Par defaut, il refuse donc de demarrer si un solde BTC significatif existe deja.
- Pour le vrai run, l'ideal est un compte spot dedie ou au minimum aucun BTC manuel deja present.

GitHub
- Aucune creation de nouveau repo requise.
- Le bon flux est:
  - coder localement dans ce repo
  - pousser ensuite sur une branche dediee si tu veux
  - ouvrir une PR plus tard si utile

Prochaines etapes recommandees
1. Tester `BTC_RANGE_DRY_RUN=1`.
2. Verifier les logs journaliers et `data/runtime/btc_range_v1_status.json`.
3. Ajuster `config/btc_range.yaml` avec de vraies observations BTC.
4. Exploiter le dashboard dedie pour lire les ranges, les positions et les trades fermes.
