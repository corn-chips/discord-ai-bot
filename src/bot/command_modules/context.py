"""Shared dependencies for slash-command registration."""

from dataclasses import dataclass
from typing import Any, Optional

from ...config import BotConfig
from ...services.gemini_client import GeminiClient
from ...services.token_tracker import TokenTracker


@dataclass(frozen=True)
class CommandContext:
    """Dependencies shared by command registrar callbacks."""

    bot: Any
    config: BotConfig
    gemini_client: GeminiClient
    performance_logger: Any
    token_tracker: Optional[TokenTracker]
