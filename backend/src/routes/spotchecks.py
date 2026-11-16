from fastapi import APIRouter, Depends, Header
from src.config.database import get_db
from src.middlewares.auth import current_user, can_write
from src.schemas.spotcheck import SpotClaimInput, SpotReportInput, SpotCloseInput
from src.services import spotchecks
router=APIRouter(prefix='/spotchecks')
@router.get('')
def listing(db=Depends(get_db),u=Depends(current_user)):
    return spotchecks.listing(db)
@router.post('',status_code=201)
def claim(data:SpotClaimInput,db=Depends(get_db),u=Depends(can_write),x_request_no:str|None=Header(default=None)):
    return spotchecks.claim(db,data,u,request_no=x_request_no)
@router.get('/{id}')
def detail(id:int,db=Depends(get_db),u=Depends(current_user)):
    return spotchecks.detail(db,id)
@router.post('/{id}/reports')
def report(id:int,data:SpotReportInput,db=Depends(get_db),u=Depends(can_write)):
    return spotchecks.report(db,id,data,u)
@router.post('/{id}/close')
def close(id:int,data:SpotCloseInput|None=None,db=Depends(get_db),u=Depends(can_write)):
    return spotchecks.close(db,id,data.close_no if data else None,u)
@router.post('/{id}/cancel')
def cancel(id:int,db=Depends(get_db),u=Depends(can_write)):
    return spotchecks.cancel(db,id,u)
