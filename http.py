from __future__ import annotations

import requests

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "Mozilla/5.0 MLB-HR-Blender/5.0",
    "Accept": "application/json,text/csv,*/*",
})


def get_json(url: str, *, params=None, timeout: int = 20) -> dict:
    try:
        response = SESSION.get(url, params=params, timeout=timeout)
        response.raise_for_status()
        data = response.json()
        return data if isinstance(data, dict) else {}
    except Exception as exc:
        print(f"HTTP JSON ERROR {url}: {exc}")
        return {}


def get_text(url: str, *, params=None, timeout: int = 30) -> str:
    try:
        response = SESSION.get(url, params=params, timeout=timeout)
        response.raise_for_status()
        return response.text
    except Exception as exc:
        print(f"HTTP TEXT ERROR {url}: {exc}")
        return ""
