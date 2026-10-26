from fastapi import Depends, FastAPI

from app.api.routes import router as submit_similarity_router
from app.auth.dependencies import require_jwt_token

app = FastAPI(title="Idea Similarity Check API")
app.include_router(
    submit_similarity_router,
    dependencies=[Depends(require_jwt_token)],
)


@app.get("/health")
def health_check():
    return {"status": "ok"}
