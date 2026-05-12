from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from typing import Any, AsyncIterator, Dict, List, Optional
from uuid import uuid4

import httpx


API_URL = "https://open.bigmodel.cn/api/paas/v4/chat/completions"
WEB_SEARCH_URL = "https://open.bigmodel.cn/api/paas/v4/web_search"
IMAGE_GENERATION_URL = "https://open.bigmodel.cn/api/paas/v4/async/images/generations"
VIDEO_GENERATION_URL = "https://open.bigmodel.cn/api/paas/v4/videos/generations"
ASYNC_RESULT_URL = "https://open.bigmodel.cn/api/paas/v4/async-result"


class GLMAPIError(RuntimeError):
    pass


@dataclass
class StreamEvent:
    kind: str
    content: str = ""
    usage: Optional[Dict[str, Any]] = None


def explain_http_status(status_code: int, text: str) -> str:
    if status_code in {401, 403}:
        return "鉴权失败：请检查 ZHIPUAI_API_KEY 是否正确。"
    if status_code == 429:
        return "请求被限流：请稍后重试，或切换更轻量模型。"
    if 500 <= status_code:
        return "服务端暂时异常：请稍后重试。"
    return f"请求失败：HTTP {status_code} {text[:300]}"


def build_payload(
    *,
    model: str,
    messages: List[Dict[str, str]],
    temperature: float,
    stream: bool,
    thinking: Optional[bool] = None,
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "stream": stream,
    }
    if thinking is not None and model.startswith("glm-4.7"):
        payload["chat_template_kwargs"] = {"enable_thinking": thinking}
    return payload


def extract_delta(data: Dict[str, Any]) -> str:
    choices = data.get("choices") or []
    if not choices:
        return ""
    choice = choices[0] or {}
    delta = choice.get("delta")
    if isinstance(delta, dict):
        content = delta.get("content")
        if isinstance(content, str):
            return content
    message = choice.get("message")
    if isinstance(message, dict):
        content = message.get("content")
        if isinstance(content, str):
            return content
    text = choice.get("text")
    return text if isinstance(text, str) else ""


class GLMClient:
    def __init__(self, api_key: Optional[str] = None, base_url: str = API_URL):
        self.api_key = api_key or os.getenv("ZHIPUAI_API_KEY", "")
        self.base_url = base_url

    def _headers(self) -> Dict[str, str]:
        if not self.api_key:
            raise GLMAPIError("缺少 ZHIPUAI_API_KEY，请先在 run.sh 中 export。")
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    async def complete_once(
        self,
        *,
        model: str,
        messages: List[Dict[str, str]],
        temperature: float,
        thinking: Optional[bool],
    ) -> StreamEvent:
        payload = build_payload(
            model=model,
            messages=messages,
            temperature=temperature,
            stream=False,
            thinking=thinking,
        )
        try:
            async with httpx.AsyncClient(timeout=90) as client:
                response = await client.post(
                    self.base_url,
                    headers=self._headers(),
                    json=payload,
                )
        except httpx.TimeoutException as exc:
            raise GLMAPIError("请求超时：请稍后重试或切换更快模型。") from exc
        except httpx.HTTPError as exc:
            raise GLMAPIError(f"网络请求失败：{exc}") from exc

        if response.status_code >= 400:
            raise GLMAPIError(explain_http_status(response.status_code, response.text))
        data = response.json()
        content = extract_delta(data)
        usage = data.get("usage") if isinstance(data.get("usage"), dict) else None
        return StreamEvent(kind="done", content=content, usage=usage)

    async def complete_json(
        self,
        *,
        model: str,
        messages: List[Dict[str, str]],
        temperature: float = 0.2,
        thinking: Optional[bool] = None,
    ) -> Dict[str, Any]:
        event = await self.complete_once(
            model=model,
            messages=messages,
            temperature=temperature,
            thinking=thinking,
        )
        return extract_json_payload(event.content)

    async def complete_stream(
        self,
        *,
        model: str,
        messages: List[Dict[str, str]],
        temperature: float,
        thinking: Optional[bool],
    ) -> AsyncIterator[StreamEvent]:
        payload = build_payload(
            model=model,
            messages=messages,
            temperature=temperature,
            stream=True,
            thinking=thinking,
        )
        usage: Optional[Dict[str, Any]] = None
        try:
            async with httpx.AsyncClient(timeout=None) as client:
                async with client.stream(
                    "POST",
                    self.base_url,
                    headers=self._headers(),
                    json=payload,
                    timeout=120,
                ) as response:
                    if response.status_code >= 400:
                        text = await response.aread()
                        raise GLMAPIError(
                            explain_http_status(
                                response.status_code,
                                text.decode("utf-8", errors="replace"),
                            )
                        )
                    async for line in response.aiter_lines():
                        line = line.strip()
                        if not line:
                            continue
                        if line.startswith("data:"):
                            line = line[5:].strip()
                        if line == "[DONE]":
                            break
                        try:
                            data = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        if isinstance(data.get("usage"), dict):
                            usage = data["usage"]
                        delta = extract_delta(data)
                        if delta:
                            yield StreamEvent(kind="delta", content=delta)
        except httpx.TimeoutException as exc:
            raise GLMAPIError("请求超时：请稍后重试或关闭流式输出。") from exc
        except httpx.HTTPError as exc:
            raise GLMAPIError(f"网络请求失败：{exc}") from exc
        yield StreamEvent(kind="done", usage=usage)

    async def web_search(
        self,
        *,
        query: str,
        search_engine: str = "search_std",
        count: int = 8,
        search_intent: bool = False,
        content_size: str = "medium",
    ) -> Dict[str, Any]:
        payload = {
            "search_query": query[:70],
            "search_engine": search_engine,
            "search_intent": search_intent,
            "count": max(1, min(count, 50)),
            "content_size": content_size,
            "request_id": "glm_tui_" + uuid4().hex,
            "user_id": "glm_tui_user",
        }
        return await self._post_json(WEB_SEARCH_URL, payload, timeout=60)

    async def generate_image(
        self,
        *,
        prompt: str,
        size: str = "1280x1280",
        watermark_enabled: bool = True,
    ) -> Dict[str, Any]:
        payload = {
            "model": "glm-image",
            "prompt": prompt,
            "quality": "hd",
            "size": size,
            "watermark_enabled": watermark_enabled,
            "request_id": "glm_tui_" + uuid4().hex,
            "user_id": "glm_tui_user",
        }
        return await self._post_json(IMAGE_GENERATION_URL, payload, timeout=60)

    async def generate_video(
        self,
        *,
        prompt: str,
        size: str = "1280x720",
        duration: int = 5,
        fps: int = 30,
        quality: str = "speed",
        with_audio: bool = False,
        watermark_enabled: bool = True,
    ) -> Dict[str, Any]:
        payload = {
            "model": "cogvideox-3",
            "prompt": prompt[:512],
            "quality": quality,
            "with_audio": with_audio,
            "watermark_enabled": watermark_enabled,
            "size": size,
            "fps": fps,
            "duration": duration,
            "request_id": "glm_tui_" + uuid4().hex,
            "user_id": "glm_tui_user",
        }
        return await self._post_json(VIDEO_GENERATION_URL, payload, timeout=60)

    async def async_result(self, task_id: str) -> Dict[str, Any]:
        safe_id = task_id.strip()
        if not safe_id:
            raise GLMAPIError("任务 ID 不能为空。")
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                response = await client.get(f"{ASYNC_RESULT_URL}/{safe_id}", headers=self._headers())
        except httpx.TimeoutException as exc:
            raise GLMAPIError("查询异步结果超时：请稍后重试。") from exc
        except httpx.HTTPError as exc:
            raise GLMAPIError(f"网络请求失败：{exc}") from exc
        if response.status_code >= 400:
            raise GLMAPIError(explain_http_status(response.status_code, response.text))
        try:
            data = response.json()
        except json.JSONDecodeError as exc:
            raise GLMAPIError("服务返回了不可解析的 JSON。") from exc
        if not isinstance(data, dict):
            raise GLMAPIError("服务返回值必须是 JSON object。")
        return data

    async def fetch_binary(self, url: str) -> tuple[bytes, str]:
        try:
            async with httpx.AsyncClient(timeout=120, follow_redirects=True) as client:
                response = await client.get(url)
        except httpx.TimeoutException as exc:
            raise GLMAPIError("下载生成结果超时：请稍后重试。") from exc
        except httpx.HTTPError as exc:
            raise GLMAPIError(f"下载生成结果失败：{exc}") from exc
        if response.status_code >= 400:
            raise GLMAPIError(explain_http_status(response.status_code, response.text))
        return response.content, response.headers.get("content-type", "")

    async def _post_json(self, url: str, payload: Dict[str, Any], timeout: int = 60) -> Dict[str, Any]:
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.post(url, headers=self._headers(), json=payload)
        except httpx.TimeoutException as exc:
            raise GLMAPIError("请求超时：请稍后重试。") from exc
        except httpx.HTTPError as exc:
            raise GLMAPIError(f"网络请求失败：{exc}") from exc
        if response.status_code >= 400:
            raise GLMAPIError(explain_http_status(response.status_code, response.text))
        try:
            data = response.json()
        except json.JSONDecodeError as exc:
            raise GLMAPIError("服务返回了不可解析的 JSON。") from exc
        if not isinstance(data, dict):
            raise GLMAPIError("服务返回值必须是 JSON object。")
        return data


def extract_json_payload(text: str) -> Dict[str, Any]:
    stripped = text.strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", stripped, flags=re.S)
    if fenced:
        stripped = fenced.group(1)
    try:
        data = json.loads(stripped)
    except json.JSONDecodeError:
        start = stripped.find("{")
        end = stripped.rfind("}")
        if start < 0 or end <= start:
            raise GLMAPIError("模型没有返回可解析 JSON")
        try:
            data = json.loads(stripped[start : end + 1])
        except json.JSONDecodeError as exc:
            raise GLMAPIError(f"模型 JSON 解析失败：{exc}") from exc
    if not isinstance(data, dict):
        raise GLMAPIError("模型 JSON 返回值必须是 object")
    return data
