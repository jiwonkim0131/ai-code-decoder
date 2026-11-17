import time

import requests

API_URL = "https://api.example.com/users"


def fetch_users(page):
    for attempt in range(3):
        response = requests.get(API_URL, params={"page": page}, timeout=5)
        if response.status_code == 429:
            time.sleep(2**attempt)
            continue
        response.raise_for_status()
        return response.json()["users"]
    raise RuntimeError("rate limited")
