"""
Edge TTS 语音管理器
负责获取、解析和缓存 Edge TTS 语音列表
"""
import json
import os
import subprocess
import asyncio
import aiofiles
from datetime import datetime, timedelta
from typing import List, Dict, Optional
import logging

logger = logging.getLogger(__name__)

try:
    import faust_backend.config_loader as conf
except ImportError:
    conf = None

MODULE_DIR = os.path.dirname(os.path.abspath(__file__))
BACKEND_DIR = os.path.dirname(MODULE_DIR)
PROJECT_ROOT = os.path.dirname(BACKEND_DIR)
DEFAULT_EDGE_TTS_EXE = os.path.join(PROJECT_ROOT, ".runtime", "Scripts", "edge-tts.exe")
DEFAULT_CACHE_DIR = os.path.join(conf.CONFIG_ROOT, "cache") if conf else os.path.join(BACKEND_DIR, "cache")

class EdgeTTSManager:
    """Edge TTS 语音管理器"""
    
    def __init__(self, cache_dir: Optional[str] = None, edge_tts_executable: Optional[str] = None):
        self.cache_dir = os.path.abspath(cache_dir or DEFAULT_CACHE_DIR)
        self.cache_file = os.path.join(self.cache_dir, "edge_tts_voices.json")
        self.edge_tts_executable = os.path.abspath(edge_tts_executable or DEFAULT_EDGE_TTS_EXE)
        self.cache_expiry_hours = 24  # 缓存24小时
        self.ensure_cache_dir()
    
    def ensure_cache_dir(self):
        """确保缓存目录存在"""
        os.makedirs(self.cache_dir, exist_ok=True)
    
    def get_cache_status(self) -> Dict:
        """获取缓存状态"""
        if not os.path.exists(self.cache_file):
            return {"cached": False, "expires": None}
        
        try:
            file_time = datetime.fromtimestamp(os.path.getmtime(self.cache_file))
            expires = file_time + timedelta(hours=self.cache_expiry_hours)
            return {
                "cached": True,
                "expires": expires.isoformat(),
                "expires_in": str(expires - datetime.now()).split('.')[0]  # 秒数
            }
        except Exception as e:
            logger.error(f"获取缓存状态失败: {e}")
            return {"cached": False, "expires": None}
    
    def parse_voice_line(self, line: str) -> Optional[Dict]:
        """解析单行语音信息"""
        try:
            # 移除行首的空格和破折号
            line = line.strip()
            if not line or line.startswith('-'):
                return None
            
            # 分割列
            parts = line.split()
            if len(parts) < 4:
                return None
            
            # 提取各列
            name = parts[0]
            gender = parts[1]
            content_categories = parts[2]
            voice_personalities = ' '.join(parts[3:])
            
            # 清理数据
            name = name.replace('-', ' ').title()
            gender = gender.capitalize()
            content_categories = content_categories.replace(',', ', ')
            voice_personalities = voice_personalities.replace(',', ', ')
            
            return {
                "name": name,
                "voice_id": line.split()[0],  # 原始voice ID
                "gender": gender,
                "content_categories": content_categories,
                "voice_personalities": voice_personalities,
                "language": self.extract_language(line.split()[0])
            }
        except Exception as e:
            logger.error(f"解析语音行失败: {line}, 错误: {e}")
            return None
    
    def extract_language(self, voice_id: str) -> str:
        """从voice_id提取语言代码"""
        try:
            # 提取语言代码，如 af-ZA, ar-AE 等
            if '-' in voice_id:
                return voice_id.split('-')[0] + '-' + voice_id.split('-')[1]
            return voice_id.split('-')[0] if '-' in voice_id else 'unknown'
        except:
            return 'unknown'
    
    async def fetch_voices(self) -> List[Dict]:
        """获取语音列表"""
        try:
            # 检查缓存是否有效
            cache_status = self.get_cache_status()
            if cache_status["cached"]:
                logger.info("使用缓存的语音列表")
                return await self.load_cached_voices()
            
            logger.info("正在获取最新的语音列表...")
            voices = await self.fetch_fresh_voices()
            await self.save_cached_voices(voices)
            return voices
            
        except Exception as e:
            logger.error(f"获取语音列表失败: {e}")
            # 如果获取失败，尝试返回缓存
            return await self.load_cached_voices()
    
    async def fetch_fresh_voices(self) -> List[Dict]:
        """获取最新的语音列表"""
        try:
            if not os.path.exists(self.edge_tts_executable):
                raise FileNotFoundError(f"未找到 edge-tts 可执行文件: {self.edge_tts_executable}")

            # 运行edge-tts命令
            result = subprocess.run(
                [self.edge_tts_executable, "--list-voices"],
                capture_output=True,
                text=True,
                timeout=30,
                cwd=PROJECT_ROOT
            )
            
            if result.returncode != 0:
                raise Exception(f"edge-tts命令失败: {result.stderr}")
            
            # 解析输出
            lines = result.stdout.strip().split('\n')
            voices = []
            
            # 跳过标题行
            for line in lines[2:]:  # 跳过前两行（标题和分隔线）
                voice_info = self.parse_voice_line(line)
                if voice_info:
                    voices.append(voice_info)
            
            logger.info(f"成功获取 {len(voices)} 个语音")
            return voices
            
        except subprocess.TimeoutExpired:
            raise Exception("获取语音列表超时")
        except Exception as e:
            raise Exception(f"获取语音列表失败: {e}")
    
    async def load_cached_voices(self) -> List[Dict]:
        """加载缓存的语音列表"""
        try:
            if not os.path.exists(self.cache_file):
                return []
            
            async with aiofiles.open(self.cache_file, 'r', encoding='utf-8') as f:
                content = await f.read()
                voices = json.loads(content)
                logger.info(f"从缓存加载了 {len(voices)} 个语音")
                return voices
                
        except Exception as e:
            logger.error(f"加载缓存失败: {e}")
            return []
    
    async def save_cached_voices(self, voices: List[Dict]):
        """保存语音列表到缓存"""
        try:
            async with aiofiles.open(self.cache_file, 'w', encoding='utf-8') as f:
                await f.write(json.dumps(voices, ensure_ascii=False, indent=2))
            logger.info(f"语音列表已保存到缓存: {len(voices)} 个语音")
        except Exception as e:
            logger.error(f"保存缓存失败: {e}")
    
    async def search_voices(self, query: str = "", language: str = "", gender: str = "") -> List[Dict]:
        """搜索语音"""
        all_voices = await self.fetch_voices()
        
        filtered_voices = []
        query_lower = query.lower()
        
        for voice in all_voices:
            # 搜索条件匹配
            match_query = not query_lower or (
                query_lower in voice["name"].lower() or
                query_lower in voice["voice_id"].lower() or
                query_lower in voice["voice_personalities"].lower()
            )
            
            match_language = not language or language == voice["language"]
            match_gender = not gender or gender == voice["gender"]
            
            if match_query and match_language and match_gender:
                filtered_voices.append(voice)
        
        return filtered_voices
    
    async def get_voice_by_id(self, voice_id: str) -> Optional[Dict]:
        """根据ID获取语音信息"""
        voices = await self.fetch_voices()
        for voice in voices:
            if voice["voice_id"] == voice_id:
                return voice
        return None
    
    async def refresh_cache(self) -> List[Dict]:
        """强制刷新缓存"""
        try:
            # 删除旧缓存
            if os.path.exists(self.cache_file):
                os.remove(self.cache_file)
                logger.info("已删除旧缓存")
            
            # 获取新数据
            voices = await self.fetch_fresh_voices()
            await self.save_cached_voices(voices)
            return voices
            
        except Exception as e:
            logger.error(f"刷新缓存失败: {e}")
            return await self.load_cached_voices()

# 全局实例
edge_tts_manager = EdgeTTSManager()