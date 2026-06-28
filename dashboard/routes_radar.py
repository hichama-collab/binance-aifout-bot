"""Token Radar dashboard routes.

The dashboard reads SQLite only; Binance scanning is handled by the systemd
one-shot scanner in tools/token_radar_scan.py.
"""

from __future__ import annotations

from flask import jsonify, render_template, request

from services.token_radar_store import (
    add_favorite,
    get_token_detail,
    get_top_tokens,
    list_favorites,
    remove_favorite,
    resolve_db_path,
)


def _bool_arg(name: str, default: bool = False) -> bool:
    value = str(request.args.get(name, "")).strip().lower()
    if value in ("1", "true", "yes", "on"):
        return True
    if value in ("0", "false", "no", "off"):
        return False
    return default


def _float_arg(name: str, default: float) -> float:
    try:
        return float(request.args.get(name, default))
    except Exception:
        return default


def _optional_float_arg(name: str) -> float | None:
    raw = request.args.get(name)
    if raw is None or str(raw).strip() == "":
        return None
    try:
        return float(raw)
    except Exception:
        return None


def _int_arg(name: str, default: int, min_value: int = 1, max_value: int = 1000) -> int:
    try:
        return max(min_value, min(max_value, int(request.args.get(name, default))))
    except Exception:
        return default


def _normalize_symbol(raw: str) -> str:
    symbol = (raw or "").strip().upper().replace("/", "").replace("-", "")
    if symbol and not symbol.endswith("USDC"):
        symbol = f"{symbol}USDC"
    return symbol


def register_radar_routes(app, require_basic_auth, base_dir):
    db_path = resolve_db_path(base_dir=base_dir)

    @app.route("/radar")
    @require_basic_auth
    def radar():
        return render_template("radar.html")

    @app.route("/api/radar/top")
    @require_basic_auth
    def api_radar_top():
        rows = get_top_tokens(
            period=request.args.get("period", "1h"),
            min_score=_float_arg("min_score", 0.0),
            min_volume=_float_arg("min_volume", 0.0),
            max_spread=_optional_float_arg("max_spread"),
            limit=_int_arg("limit", 500),
            favorites_only=_bool_arg("favorites_only", False),
            db_path=db_path,
        )
        return jsonify({"ok": True, "items": rows})

    @app.route("/api/radar/favorites", methods=["GET"])
    @require_basic_auth
    def api_radar_favorites():
        return jsonify({"ok": True, "items": list_favorites(db_path=db_path)})

    @app.route("/api/radar/favorites", methods=["POST"])
    @require_basic_auth
    def api_radar_add_favorite():
        data = request.get_json(silent=True) or {}
        symbol = _normalize_symbol(data.get("symbol", ""))
        note = str(data.get("note", "") or "")
        if not symbol:
            return jsonify({"ok": False, "error": "symbol_required"}), 400
        item = add_favorite(symbol, note=note, db_path=db_path)
        return jsonify({"ok": True, "item": item})

    @app.route("/api/radar/favorites/<symbol>", methods=["DELETE"])
    @require_basic_auth
    def api_radar_delete_favorite(symbol):
        removed = remove_favorite(_normalize_symbol(symbol), db_path=db_path)
        return jsonify({"ok": True, "removed": removed})

    @app.route("/api/radar/token/<symbol>")
    @require_basic_auth
    def api_radar_token(symbol):
        detail = get_token_detail(
            _normalize_symbol(symbol),
            limit=_int_arg("limit", 100, 1, 500),
            db_path=db_path,
        )
        if not detail.get("latest"):
            return jsonify({"ok": False, "error": "not_found", "symbol": detail["symbol"]}), 404
        return jsonify({"ok": True, **detail})
