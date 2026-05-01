#!/usr/bin/env python3
"""
Dashboard for Main Bot - reads from data/logs/live/main/ or data/logs/dry/main/
"""

import os
import re
import csv
import json
import time
import hmac
import hashlib
import subprocess
import sys
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta
from functools import wraps
from pathlib import Path
from urllib.parse import urlencode

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.trade_memory import load_closed_trades, load_dashboard_cache, resolve_memory_db_path, sync_trade_memory
from flask import Flask, Response, jsonify, render_template, request, abort

# ... (reste du code identique mais avec les chemins modifiés)

# NEW: Log directory resolution with mode and bot type
LOG_DIR = Path(os.getenv("BOT_LOG_DIR", str(REPO_ROOT / "data" / "logs")))
# Subdirectories: dry/main/ or live/main/
MODE = "dry" if os.getenv("DRY_RUN", "").lower() in ("1", "true", "yes") else "live"
BOT_TYPE = "main"
LOG_DIR = LOG_DIR / MODE / BOT_TYPE
LOG_DIR.mkdir(parents=True, exist_ok=True)
