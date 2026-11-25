import time
from concurrent.futures import ThreadPoolExecutor

import httpx

RATE_API = "https://api.example.com/rates"
CACHE_TTL = 60
_cache: dict[str, tuple[float, float]] = {}

client = httpx.Client(timeout=3.0)


def get_rate(currency: str) -> float:
    cached = _cache.get(currency)
    if cached and time.monotonic() - cached[1] < CACHE_TTL:
        return cached[0]
    response = client.get(RATE_API, params={"base": "KRW", "to": currency})
    response.raise_for_status()
    rate = response.json()["rate"]
    _cache[currency] = (rate, time.monotonic())
    return rate


def convert_all(amount: int, currencies: list[str]) -> dict[str, float]:
    with ThreadPoolExecutor(max_workers=4) as pool:
        rates = pool.map(get_rate, currencies)
    return {c: round(amount * r, 2) for c, r in zip(currencies, rates)}
