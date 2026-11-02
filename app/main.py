import asyncio
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI

from app.api.routes import (
    document_validation_router,
    router as submit_similarity_router,
)
from app.auth.dependencies import require_jwt_token
from app.core.config import INDEX_SCHEDULER_ENABLED


@asynccontextmanager
async def lifespan(_: FastAPI):
    scheduler_task = None
    if INDEX_SCHEDULER_ENABLED:
        from app.services.document_validation_scheduler import (
            run_document_validation_schedule,
        )

        scheduler_task = asyncio.create_task(run_document_validation_schedule())
    try:
        yield
    finally:
        if scheduler_task is not None:
            scheduler_task.cancel()
            try:
                await scheduler_task
            except asyncio.CancelledError:
                pass

app = FastAPI(title="Idea Similarity Check API", lifespan=lifespan)
app.include_router(
    submit_similarity_router,
    dependencies=[Depends(require_jwt_token)],
)
app.include_router(
    document_validation_router,
    dependencies=[Depends(require_jwt_token)],
)


@app.get("/health")
def health_check():
    return {"status": "ok"}
