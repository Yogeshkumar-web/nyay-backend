import asyncio
from sqlalchemy import text
from app.db.session import AsyncSessionLocal

async def check():
    async with AsyncSessionLocal() as session:
        result = await session.execute(text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name='users' ORDER BY ordinal_position"
        ))
        print("Users table columns:")
        for row in result:
            print(" -", row[0])

asyncio.run(check())