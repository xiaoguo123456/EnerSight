"""当前账号。docs/06 §五、docs/09 §4.3"""

from typing import Annotated

from fastapi import APIRouter, Depends, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import CurrentUserDep
from app.db import get_session
from app.services import station as svc

router = APIRouter(prefix="/v1/me", tags=["me"])
DbDep = Annotated[AsyncSession, Depends(get_session)]


@router.delete("", status_code=204)
async def delete_my_data(user: CurrentUserDep, db: DbDep) -> Response:
    """删除我的数据：本账号全部自建场站及其预警、发电记录、报告与预测留档。不可恢复。"""
    await svc.delete_owner_data(db, user.id)
    return Response(status_code=204)
