import asyncio
import sys

from fastls import AsyncSession


async def main(url):
    async with AsyncSession(profile="chrome/156/windows") as session:
        async with session.stream("GET", url) as response:
            print("HTTP", response.status_code)
            async for chunk in response.aiter_bytes():
                print("received", len(chunk), "bytes")


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1]))
