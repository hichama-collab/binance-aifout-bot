#!/usr/bin/env python3
"""
Dashboard for BTC Range V1 Bot - reads from data/logs/live/btc_range/ or data/logs/dry/btc_range/
"""

import csv
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path

from flask import Flask, Response, jsonify, render_template, request

REPO_ROOT = Path(__file__).resolve().parents[1]
STATIC_DIR = REPO_ROOT / "dashboard" / "static"
TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"

app = Flask(__name__, template_folder=str(TEMPLATES_DIR), static_folder=str(STATIC_DIR))

# NEW: Log directory resolution with mode and bot type
BASE_DIR = Path(os.getenv("BOT_BASE_DIR", str(REPO_ROOT))).resolve()
LOG_DIR = Path(os.getenv("BOT_LOG_DIR", str(BASE_DIR / "data" / "logs"))).resolve()
MODE = "dry" if os.getenv("BTC_RANGE_DRY_RUN", "").lower() in ("1", "true", "yes") else "live"
BOT_TYPE = "btc_range"
LOG_DIR = LOG_DIR / MODE / BOT_TYPE
LOG_DIR.mkdir(parents=True, exist_ok=True)

# ... (reste du code identique mais avec LOG_DIR pointant vers le sous-répertoire)
