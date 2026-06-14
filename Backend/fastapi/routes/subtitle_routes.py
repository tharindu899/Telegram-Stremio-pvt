from fastapi import APIRouter, HTTPException, Depends, Query
from fastapi.responses import JSONResponse
from typing import Optional
from Backend import db
from Backend.fastapi.security.tokens import verify_token
from Backend.logger import LOGGER

router = APIRouter(prefix="/api/subtitles", tags=["Subtitles"])

@router.get("/list")
async def list_subtitles(imdb_id: str = Query(...), media_type: str = Query("movie"), season: Optional[int] = Query(None), episode: Optional[int] = Query(None), token_data: dict = Depends(verify_token)):
    try:
        return JSONResponse({"subtitles": await db.get_subtitles(imdb_id, media_type, season, episode)})
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/add")
async def add_subtitle(payload: dict, token_data: dict = Depends(verify_token)):
    try:
        for field in ['imdb_id', 'media_type', 'msg_id', 'chat_id', 'name']:
            if field not in payload: raise HTTPException(status_code=400, detail=f"Missing {field}")
        if await db.add_subtitle(payload['imdb_id'], payload['media_type'], payload):
            return JSONResponse({"success": True})
        raise HTTPException(status_code=500)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.delete("/delete")
async def delete_subtitle(imdb_id: str = Query(...), media_type: str = Query("movie"), subtitle_id: str = Query(...), token_data: dict = Depends(verify_token)):
    try:
        if await db.delete_subtitle(imdb_id, media_type, subtitle_id):
            return JSONResponse({"success": True})
        raise HTTPException(status_code=404)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
