import asyncio

import asyncpg


async def main():
    try:
        conn = await asyncpg.connect(
            host="localhost",
            port=5432,
            user="vakilsuite",
            password="vakilsuite",
            database="vakilsuite",
            ssl=False,
        )

        print("Connected!")

    except Exception as e:
        print("Connection failed:", e)

    finally:
        if "conn" in locals():
            await conn.close()
            print("Connection closed")


asyncio.run(main())
