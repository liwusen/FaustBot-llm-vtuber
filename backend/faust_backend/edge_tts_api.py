"""
Edge TTS 语音管理 API
提供语音列表获取、搜索、缓存管理等接口
"""
import asyncio
from fastapi import APIRouter, HTTPException, Query
from typing import List, Optional, Dict
from pydantic import BaseModel
from .edge_tts_manager import edge_tts_manager

router = APIRouter(prefix="/faust/edge-tts", tags=["edge-tts"])

def register_edge_tts_routes(app):
    """注册Edge TTS路由到FastAPI应用"""
    app.include_router(router)

class VoiceResponse(BaseModel):
    """语音信息响应"""
    name: str
    voice_id: str
    gender: str
    content_categories: str
    voice_personalities: str
    language: str

class CacheStatus(BaseModel):
    """缓存状态响应"""
    cached: bool
    expires: Optional[str]
    expires_in: Optional[str]

class VoiceSearchResponse(BaseModel):
    """语音搜索响应"""
    voices: List[VoiceResponse]
    total: int
    query: str

@router.get("/voices", response_model=List[VoiceResponse])
async def get_all_voices():
    """获取所有Edge TTS语音列表"""
    try:
        voices = await edge_tts_manager.fetch_voices()
        return [VoiceResponse(**voice) for voice in voices]
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取语音列表失败: {str(e)}")

@router.get("/voices/search", response_model=VoiceSearchResponse)
async def search_voices(
    q: Optional[str] = Query("", description="搜索关键词"),
    language: Optional[str] = Query("", description="语言代码"),
    gender: Optional[str] = Query("", description="性别 (Male/Female)")
):
    """搜索语音"""
    try:
        voices = await edge_tts_manager.search_voices(q or "", language or "", gender or "")
        return VoiceSearchResponse(
            voices=[VoiceResponse(**voice) for voice in voices],
            total=len(voices),
            query=q or ""
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"搜索语音失败: {str(e)}")

@router.get("/voices/{voice_id}", response_model=VoiceResponse)
async def get_voice_by_id(voice_id: str):
    """根据ID获取语音信息"""
    try:
        voice = await edge_tts_manager.get_voice_by_id(voice_id)
        if not voice:
            raise HTTPException(status_code=404, detail=f"未找到语音ID: {voice_id}")
        return VoiceResponse(**voice)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取语音信息失败: {str(e)}")

@router.get("/cache/status", response_model=CacheStatus)
async def get_cache_status():
    """获取缓存状态"""
    try:
        status = edge_tts_manager.get_cache_status()
        return CacheStatus(**status)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取缓存状态失败: {str(e)}")

@router.post("/cache/refresh")
async def refresh_cache():
    """强制刷新缓存"""
    try:
        voices = await edge_tts_manager.refresh_cache()
        return {
            "message": "缓存刷新成功",
            "total_voices": len(voices),
            "cache_status": edge_tts_manager.get_cache_status()
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"刷新缓存失败: {str(e)}")

@router.get("/languages")
async def get_languages():
    """获取所有可用语言"""
    try:
        voices = await edge_tts_manager.fetch_voices()
        languages = list(set(voice["language"] for voice in voices))
        languages.sort()
        return {"languages": languages}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取语言列表失败: {str(e)}")

@router.get("/genders")
async def get_genders():
    """获取所有可用性别"""
    try:
        voices = await edge_tts_manager.fetch_voices()
        genders = list(set(voice["gender"] for voice in voices))
        genders.sort()
        return {"genders": genders}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取性别列表失败: {str(e)}")

@router.get("/stats")
async def get_stats():
    """获取语音统计信息"""
    try:
        voices = await edge_tts_manager.fetch_voices()
        
        # 统计信息
        total_voices = len(voices)
        gender_stats = {}
        language_stats = {}
        
        for voice in voices:
            gender = voice["gender"]
            language = voice["language"]
            
            gender_stats[gender] = gender_stats.get(gender, 0) + 1
            language_stats[language] = language_stats.get(language, 0) + 1
        
        return {
            "total_voices": total_voices,
            "gender_distribution": gender_stats,
            "language_distribution": language_stats,
            "cache_status": edge_tts_manager.get_cache_status()
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取统计信息失败: {str(e)}")