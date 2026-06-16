import asyncio
import httpx
import time
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Config
BASE_URL = "http://localhost:8000/api/v1"
TEST_TOKEN = "replace_with_valid_token"
CONCURRENT_REQUESTS = 10


async def simulate_ai_call(client: httpx.AsyncClient, req_id: int):
    logger.info(f"Req {req_id}: Starting...")
    start_time = time.time()

    try:
        # Example: Retry extraction endpoint which triggers Celery
        response = await client.post(
            f"{BASE_URL}/documents/dummy-id/extraction/retry",
            headers={"Authorization": f"Bearer {TEST_TOKEN}"},
        )
        duration = time.time() - start_time

        if response.status_code in (200, 202):
            logger.info(f"Req {req_id}: Success in {duration:.2f}s")
            return True
        else:
            logger.warning(
                f"Req {req_id}: Failed with {response.status_code} in {duration:.2f}s"
            )
            return False

    except Exception as e:
        logger.error(f"Req {req_id}: Error - {str(e)}")
        return False


async def run_load_test():
    logger.info(f"Starting load test with {CONCURRENT_REQUESTS} concurrent requests...")

    async with httpx.AsyncClient(timeout=30.0) as client:
        tasks = [simulate_ai_call(client, i) for i in range(CONCURRENT_REQUESTS)]
        results = await asyncio.gather(*tasks)

        success_count = sum(1 for r in results if r)
        logger.info(f"Test complete. Success: {success_count}/{CONCURRENT_REQUESTS}")


if __name__ == "__main__":
    asyncio.run(run_load_test())
