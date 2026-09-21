"""Deployment smoke check: the site answers, health is green, and the API
serves public event types without leaking private data."""
import os
import sys

import requests

BASE = os.environ.get("SITE_URL", "https://scheduler.dtcdev.click").rstrip("/")


def check(path, expect=200):
    url = f"{BASE}{path}"
    response = requests.get(url, timeout=15)
    assert response.status_code == expect, f"{url} -> {response.status_code}"
    return response


def main():
    check("/", 200)
    health = check("/health", 200).json()
    assert health.get("status") == "ok", health
    types = check("/api/v1/types", 200).json()
    assert isinstance(types.get("event_types"), list)
    assert "RootAdmin" not in check("/api/v1/types", 200).text
    print("verify ok:", BASE)


if __name__ == "__main__":
    try:
        main()
    except AssertionError as exc:
        print(f"verify failed: {exc}", file=sys.stderr)
        sys.exit(1)
