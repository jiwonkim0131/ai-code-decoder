import asyncio

from tools import call_tool, merge_results, plan_tools


async def run_tools(question):
    calls = plan_tools(question)
    tasks = [call_tool(name, args) for name, args in calls]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    return merge_results(results)
