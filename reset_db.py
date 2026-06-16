import asyncio
from sqlalchemy import text
from app.db.session import AsyncSessionLocal

async def reset():
    async with AsyncSessionLocal() as session:
        await session.execute(text("DROP SCHEMA public CASCADE"))
        await session.execute(text("CREATE SCHEMA public"))
        # Enums bhi drop karo
        await session.execute(text("DROP TYPE IF EXISTS user_role CASCADE"))
        await session.execute(text("DROP TYPE IF EXISTS case_type CASCADE"))
        await session.execute(text("DROP TYPE IF EXISTS bench_type CASCADE"))
        await session.execute(text("DROP TYPE IF EXISTS case_stage CASCADE"))
        await session.execute(text("DROP TYPE IF EXISTS case_status CASCADE"))
        await session.execute(text("DROP TYPE IF EXISTS case_number_type CASCADE"))
        await session.execute(text("DROP TYPE IF EXISTS party_type CASCADE"))
        await session.execute(text("DROP TYPE IF EXISTS document_type CASCADE"))
        await session.execute(text("DROP TYPE IF EXISTS upload_status CASCADE"))
        await session.execute(text("DROP TYPE IF EXISTS ocr_status CASCADE"))
        await session.commit()
        print("Database reset complete")

asyncio.run(reset())