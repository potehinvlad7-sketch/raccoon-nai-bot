"""Backward-compatible import wrapper for the NovelAI service client."""

from app.services.nai_client import NovelAIClient, NovelAIError

__all__ = ["NovelAIClient", "NovelAIError"]
