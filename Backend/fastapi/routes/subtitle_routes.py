"""
Subtitle Routes for Telegram-Stremio
"""
from fastapi import APIRouter, HTTPException, Depends, Query
from fastapi.responses import JSONResponse
from typing import Optional
from Backend import db
from Backend.fastapi.security.tokens import verify_token
from Backend.logger import LOGGER

router = APIRouter(prefix="/api/subtitles", tags=["Subtitles"])

@router.get("/list")
async def list_subtitles(
    imdb_id: str = Query(..., description="IMDB ID of the media"),
    media_type: str = Query("movie", regex="^(movie|tv)$"),
    season: Optional[int] = Query(None, description="Season number"),
    episode: Optional[int] = Query(None, description="Episode number"),
    token_data: dict = Depends(verify_token)
):
    """List all subtitles for a media item"""
    try:
        subtitles = await db.get_subtitles(imdb_id, media_type, season, episode)
        return JSONResponse({"subtitles": subtitles})
    except Exception as e:
        LOGGER.error(f"Error listing subtitles: {e}")
        raise HTTPException(status_code=500, detail="Failed to list subtitles")

@router.post("/add")
async def add_subtitle(
    payload: dict,
    token_data: dict = Depends(verify_token)
):
    """Add a subtitle to a media item"""
    try:
        required_fields = ['imdb_id', 'media_type', 'msg_id', 'chat_id', 'name']
        for field in required_fields:
            if field not in payload:
                raise HTTPException(status_code=400, detail=f"Missing required field: {field}")
        success = await db.add_subtitle(payload['imdb_id'], payload['media_type'], payload)
        if success:
            return JSONResponse({"success": True, "message": "Subtitle added"})
        raise HTTPException(status_code=500, detail="Failed to add subtitle")
    except Exception as e:
        LOGGER.error(f"Error adding subtitle: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.delete("/delete")
async def delete_subtitle(
    imdb_id: str = Query(..., description="IMDB ID"),
    media_type: str = Query("movie", regex="^(movie|tv)$"),
    subtitle_id: str = Query(..., description="Subtitle ID"),
    token_data: dict = Depends(verify_token)
):
    """Delete a subtitle"""
    try:
        success = await db.delete_subtitle(imdb_id, media_type, subtitle_id)
        if success:
            return JSONResponse({"success": True, "message": "Subtitle deleted"})
        raise HTTPException(status_code=404, detail="Subtitle not found")
    except Exception as e:
        LOGGER.error(f"Error deleting subtitle: {e}")
        raise HTTPException(status_code=500, detail="Failed to delete")
