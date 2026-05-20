import asyncio
import sys
import logging

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.db.session import AsyncSessionLocal

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def check(table_name: str) -> None:
    try:
        async with AsyncSessionLocal() as session:
            # Validate connection
            await session.execute(text("SELECT 1"))

            logger.info(f"Checking table: {table_name}")

            result = await session.execute(
                text(
                    """
                    SELECT column_name
                    FROM information_schema.columns
                    WHERE table_name = :table_name
                    ORDER BY ordinal_position
                    """
                ),
                {"table_name": table_name},
            )

            rows = result.fetchall()

            if not rows:
                logger.warning(f"No columns found for table: {table_name}")
                return

            print(f"\nColumns in '{table_name}':")
            for row in rows:
                print(f" - {row[0]}")

    except SQLAlchemyError as e:
        logger.error(f"Database error: {e}")
        sys.exit(1)

    except Exception:
        logger.exception("Unexpected error")
        sys.exit(1)


if __name__ == "__main__":
    table = sys.argv[1] if len(sys.argv) > 1 else "users"
    asyncio.run(check(table))
