import asyncio
import random

MAX_CONCURRENCY = 5
sem = asyncio.Semaphore(MAX_CONCURRENCY)


async def call_tool(name: str) -> str:
    await asyncio.sleep(random.uniform(0.1, 2.0))
    if random.random() < 0.2:
        raise ConnectionError(f"{name} failed")
    return f"{name}: ok"


async def call_with_retry(name: str, retries: int = 3) -> str | None:
    async with sem:
        for attempt in range(retries):
            try:
                return await asyncio.wait_for(call_tool(name), timeout=1.5)
            except (ConnectionError, asyncio.TimeoutError):
                await asyncio.sleep(2 ** attempt * 0.1)
    return None


async def main() -> None:
    tools = [f"tool-{i}" for i in range(10)]
    results = await asyncio.gather(*(call_with_retry(t) for t in tools))
    ok = [r for r in results if r is not None]
    print(f"{len(ok)}/{len(tools)} succeeded")


asyncio.run(main())
