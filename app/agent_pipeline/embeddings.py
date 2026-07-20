"""Optional OpenAI-compatible embeddings with bounded, non-sensitive errors."""

import math
from typing import Protocol

import requests


class EmbeddingUnavailable(RuntimeError):
    """Embedding configuration, transport, or response validation failed."""


class EmbeddingClient(Protocol):
    def embed(self, texts: list[str]) -> list[list[float]]: ...


class OpenAICompatibleEmbeddingClient:
    def __init__(
        self,
        *,
        model: str,
        api_key: str | None,
        base_url: str,
        timeout_seconds: int = 15,
    ) -> None:
        if not model.strip() or not base_url.strip():
            raise EmbeddingUnavailable("embedding 配置缺少 model 或 base_url")
        self.model = model.strip()
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        payload = {"model": self.model, "input": texts}
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        try:
            response = requests.post(
                f"{self.base_url}/embeddings",
                json=payload,
                headers=headers,
                timeout=self.timeout_seconds,
            )
            response.raise_for_status()
            body = response.json()
        except Exception as error:
            raise EmbeddingUnavailable(
                f"embedding 请求失败：{type(error).__name__}"
            ) from error

        data = body.get("data") if isinstance(body, dict) else None
        if not isinstance(data, list) or len(data) != len(texts):
            raise EmbeddingUnavailable("embedding 响应数量不匹配")
        try:
            ordered = sorted(data, key=lambda item: int(item.get("index", 0)))
            vectors = [item["embedding"] for item in ordered]
            if any(not isinstance(vector, list) or not vector for vector in vectors):
                raise ValueError("空向量")
            dimension = len(vectors[0])
            if any(len(vector) != dimension for vector in vectors):
                raise ValueError("向量维度不一致")
            if any(
                not isinstance(value, (int, float)) or not math.isfinite(float(value))
                for vector in vectors
                for value in vector
            ):
                raise ValueError("向量包含非有限数值")
            return [[float(value) for value in vector] for vector in vectors]
        except (KeyError, TypeError, ValueError) as error:
            raise EmbeddingUnavailable(
                f"embedding 响应校验失败：{type(error).__name__}"
            ) from error
