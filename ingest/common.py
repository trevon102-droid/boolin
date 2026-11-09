"""Shared helpers: HTTP with retry/backoff, SQLite path."""
import os
import sqlite3
import time

import requests

DB_PATH = os.environ.get("RACKZ_DB", os.path.join(os.path.dirname(__file__), "..", "data", "rackz_edge.db"))
SLEEP = 0.4  # polite pause between calls


def get_json(url, params=None, retries=5, headers=None):
    """GET json; retry on timeouts, 429 and 5xx with exponential backoff (2,4,8,16,32s)."""
    for attempt in range(retries):
        try:
            r = requests.get(url, params=params, headers=headers, timeout=30)
            if r.status_code == 429 or r.status_code >= 500:
                raise requests.HTTPError(f"HTTP {r.status_code}")
            r.raise_for_status()
            time.sleep(SLEEP)
            return r.json()
        except (requests.RequestException, ValueError) as e:
            if attempt == retries - 1:
                raise
            wait = 2 ** (attempt + 1)
            print(f"  retry {attempt + 1}/{retries - 1} in {wait}s ({e})")
            time.sleep(wait)


def connect():
    path = os.path.abspath(DB_PATH)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    return sqlite3.connect(path)
