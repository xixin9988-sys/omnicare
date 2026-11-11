import asyncio

from app.core.db import init_db
from app.main import seed_demo_data


async def main() -> None:
    await init_db()
    await seed_demo_data()


if __name__ == "__main__":
    asyncio.run(main())
