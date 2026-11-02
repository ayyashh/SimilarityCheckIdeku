from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app.core.config import (
    INDEX_SCHEDULER_HOUR,
    INDEX_SCHEDULER_MINUTE,
    INDEX_SCHEDULER_TIMEZONE,
)

logger = logging.getLogger(__name__)


async def run_document_validation_schedule() -> None:
    """Run the handover batch daily at the configured local time."""
    timezone = ZoneInfo(INDEX_SCHEDULER_TIMEZONE)
    if not 0 <= INDEX_SCHEDULER_HOUR <= 23 or not 0 <= INDEX_SCHEDULER_MINUTE <= 59:
        raise ValueError("INDEX_SCHEDULER_HOUR/MINUTE berada di luar rentang yang valid.")

    while True:
        now = datetime.now(timezone)
        scheduled = now.replace(
            hour=INDEX_SCHEDULER_HOUR,
            minute=INDEX_SCHEDULER_MINUTE,
            second=0,
            microsecond=0,
        )
        if scheduled <= now:
            scheduled += timedelta(days=1)
        delay = (scheduled - now).total_seconds()
        logger.info("[HANDOVER VALIDATION] Jadwal berikutnya: %s", scheduled.isoformat())
        await asyncio.sleep(delay)

        try:
            from app.services.document_validation_batch import DocumentValidationBatchService

            await asyncio.to_thread(DocumentValidationBatchService().run)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("[HANDOVER VALIDATION] Batch terjadwal gagal.")
