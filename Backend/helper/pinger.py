"""Low-noise self health ping for hosted deployments."""

import asyncio
import time

import aiohttp

from Backend.config import Telegram
from Backend.logger import LOGGER


async def ping():
    """Keep the public health endpoint warm without filling production logs.

    Successful pings are debug-only. Repeating failures are collapsed to one
    warning every six hours per failure type, so an upstream rate limit cannot
    flood the Hugging Face container log.
    """
    sleep_time = 1200
    manifest_url = f"{Telegram.BASE_URL}/api/system/stats"
    last_problem = ""
    last_problem_at = 0.0

    def report_problem(message: str) -> None:
        nonlocal last_problem, last_problem_at
        now = time.monotonic()
        if message != last_problem or (now - last_problem_at) >= 6 * 60 * 60:
            LOGGER.warning(message)
            last_problem = message
            last_problem_at = now

    while True:
        await asyncio.sleep(sleep_time)
        try:
            async with aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=10)
            ) as session:
                async with session.get(manifest_url) as resp:
                    status = int(resp.status)
                    if 200 <= status < 400:
                        LOGGER.debug("Health ping ok: %s", status)
                        last_problem = ""
                        last_problem_at = 0.0
                    else:
                        report_problem(f"Health ping returned HTTP {status}; suppressing repeats for 6 hours.")
        except asyncio.TimeoutError:
            report_problem("Health ping timed out; suppressing repeats for 6 hours.")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            report_problem(f"Health ping failed ({type(exc).__name__}); suppressing repeats for 6 hours.")
