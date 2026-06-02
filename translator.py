"""
翻译模块
使用硅基流动 API 进行中英文双向翻译
"""

import asyncio
import re
import time
from dataclasses import dataclass
from typing import Optional, List

import aiohttp
from loguru import logger


@dataclass
class TranslationConfig:
    api_key: str = ""
    model: str = "Qwen/Qwen3.5-4B"
    endpoint: str = "https://api.siliconflow.cn/v1/chat/completions"
    max_tokens: int = 1000
    temperature: float = 0.3
    source_lang: str = "en"
    target_lang: str = "zh"
    context_messages: int = 3
    timeout_seconds: float = 8.0


SYSTEM_PROMPT = """你是一个游戏语音实时翻译助手。你的任务是在中文和英文之间做实时互译。

翻译规则：
1. 如果原文是中文，翻译成自然、简洁的英文
2. 如果原文是英文，翻译成自然、简洁的中文
3. 保留游戏术语的常用英文原名；必要时用括号补充解释
4. 口语化表达要翻译成目标语言里的自然口语
5. 缩写和俚语要正确识别并翻译（如 lol、brb、gg、nt、wp 等）
6. 不要返回空字符串；如果原文不完整或难以理解，尽量翻译可理解部分，实在无法理解时中文目标返回“（听不清）”，英文目标返回“(unclear)”
7. 每句话精炼简洁，适合在游戏浮窗中阅读
8. 只输出翻译结果，不要添加任何解释或说明
9. 禁止进行思考推理，直接给出翻译结果，不要输出思考过程

常见游戏术语参考：
- push/peek: 推进/探头
- flank: 绕后
- rotate: 转点
- eco: 经济局
- full buy: 全起
- drop: 发枪/丢枪
- pick: 击杀/拿到
- one shot/hit: 残血/大残
- heaven/hell: 高台/地下
- spawn: 出生点
"""

ZH_RE = re.compile(r"[\u4e00-\u9fff]")


class GameTranslator:
    """游戏语音翻译器"""

    def __init__(self, config: TranslationConfig = None):
        self.config = config or TranslationConfig()
        self._context: List[dict] = []
        self._session: Optional[aiohttp.ClientSession] = None
        self._translation_count = 0
        self._total_time = 0

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=self.config.timeout_seconds)
            )
        return self._session

    def detect_language(self, text: str, detected_language: str = "") -> str:
        """Normalize language to zh/en for bidirectional translation."""
        lang = (detected_language or "").lower()
        if lang in ("zh", "zh-cn", "zh-tw", "chinese", "cmn", "yue"):
            return "zh"
        if lang in ("en", "eng", "english"):
            return "en"
        zh_count = len(ZH_RE.findall(text or ""))
        return "zh" if zh_count >= max(1, len(text.strip()) // 5) else "en"

    def get_target_language(self, source_language: str) -> str:
        return "en" if source_language == "zh" else "zh"

    def _language_name(self, language: str) -> str:
        return "英文" if language == "en" else "中文"

    async def translate(self, text: str, detected_language: str = "") -> str:
        """在中文和英文之间互译"""
        if not text or not text.strip():
            return ""

        if not self.config.api_key or self.config.api_key == "YOUR_SILICONFLOW_API_KEY":
            logger.warning("API Key 未配置，返回原文")
            return f"[未翻译] {text}"

        source_language = self.detect_language(text, detected_language)
        target_language = self.get_target_language(source_language)
        current_message = {
            "role": "user",
            "content": (
                f"请将以下{self._language_name(source_language)}内容翻译成"
                f"{self._language_name(target_language)}，只输出译文：{text}"
            )
        }

        # 实时字幕更看重低延迟，context_messages=0 时完全不带历史。
        max_ctx = max(0, self.config.context_messages * 2)
        if max_ctx and len(self._context) > max_ctx:
            self._context = self._context[-max_ctx:]

        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            *(self._context if max_ctx else []),
            current_message
        ]

        headers = {
            "Authorization": f"Bearer {self.config.api_key}",
            "Content-Type": "application/json"
        }

        payload = {
            "model": self.config.model,
            "messages": messages,
            "max_tokens": self.config.max_tokens,
            "temperature": self.config.temperature,
            "stream": False,
            "enable_thinking": False
        }

        start_time = time.time()

        try:
            session = await self._get_session()

            async with session.post(
                self.config.endpoint,
                headers=headers,
                json=payload
            ) as response:
                if response.status == 200:
                    data = await response.json()
                    choice = data["choices"][0]
                    message = choice.get("message") or {}
                    translation = (message.get("content") or "").strip()
                    reasoning = (message.get("reasoning_content") or "").strip()
                    elapsed = time.time() - start_time
                    self._translation_count += 1
                    self._total_time += elapsed
                    if not translation:
                        finish_reason = choice.get("finish_reason")
                        logger.warning(
                            "翻译 API 返回空 content: "
                            f"finish_reason={finish_reason}, reasoning_len={len(reasoning)}"
                        )
                        if reasoning:
                            logger.debug(f"reasoning_content 预览: {reasoning[:200]}")
                    else:
                        if max_ctx:
                            self._context.extend([
                                current_message,
                                {"role": "assistant", "content": translation}
                            ])
                            if len(self._context) > max_ctx:
                                self._context = self._context[-max_ctx:]
                    direction = f"{source_language}->{target_language}"
                    logger.info(f"翻译({direction}): {text[:50]}... → {translation[:50]}... ({elapsed:.2f}s)")
                    return translation
                else:
                    error_text = await response.text()
                    logger.error(f"翻译 API 错误: {response.status} - {error_text}")
                    return f"[翻译错误: {response.status}]"

        except asyncio.TimeoutError:
            logger.error("翻译 API 超时")
            return f"[翻译超时] {text[:80]}..."
        except Exception as e:
            logger.error(f"翻译异常: {e}")
            return f"[翻译失败] {text[:80]}..."

    async def translate_batch(self, texts: List[str]) -> List[str]:
        """批量翻译"""
        tasks = [self.translate(text) for text in texts]
        return await asyncio.gather(*tasks)

    async def translate_streaming(self, text: str):
        """流式翻译（暂未实现流式，先降级为等待完整结果）"""
        result = await self.translate(text)
        yield result

    def get_stats(self) -> dict:
        """获取翻译统计"""
        avg_time = self._total_time / self._translation_count if self._translation_count > 0 else 0
        return {
            "total_translations": self._translation_count,
            "average_time": round(avg_time, 3),
            "total_time": round(self._total_time, 2)
        }

    def clear_context(self):
        """清除翻译上下文"""
        self._context.clear()

    async def close(self):
        """关闭 HTTP 会话"""
        if self._session and not self._session.closed:
            await self._session.close()
