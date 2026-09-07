from datetime import UTC, datetime

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter(tags=["health"])


class Health(BaseModel):
    status: str
    server_time: str


@router.get("/health", response_model=Health)
async def health() -> Health:
    return Health(status="ok", server_time=datetime.now(UTC).isoformat())
