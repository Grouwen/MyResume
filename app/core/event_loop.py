import asyncio
import selectors
import sys


def loop_factory() -> asyncio.AbstractEventLoop:
    """Windows 下 psycopg 异步必须跑在 SelectorEventLoop 上"""
    if sys.platform == "win32":
        return asyncio.SelectorEventLoop(selectors.SelectSelector())
    return asyncio.new_event_loop()