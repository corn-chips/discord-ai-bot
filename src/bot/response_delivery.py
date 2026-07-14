"""Discord response rendering, splitting, pagination, and delivery."""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, List, Optional

import discord


logger = logging.getLogger(__name__)

AsyncCallback = Callable[..., Awaitable[Any]]


class SplitResponsePaginatorView(discord.ui.View):
    """Simple paginator for split responses with sender-priority navigation."""

    def __init__(
        self,
        pages: List[str],
        sender_user_id: int,
        title: str,
        priority_window_seconds: float = 2.0,
        timeout: float = 120.0,
    ) -> None:
        super().__init__(timeout=timeout)
        self.pages = pages
        self.sender_user_id = sender_user_id
        self.title = title
        self.priority_window_seconds = priority_window_seconds
        self.current_page_index = 0
        self.last_sender_click_time: Optional[datetime] = None
        self._interaction_lock = asyncio.Lock()
        self.message: Optional[discord.Message] = None
        self._update_button_states()

    def build_embed(self) -> discord.Embed:
        embed = discord.Embed(
            title=self.title,
            description=self.pages[self.current_page_index],
            color=discord.Color.blurple(),
        )
        embed.set_footer(text=f"Page {self.current_page_index + 1}/{len(self.pages)}")
        return embed

    def _update_button_states(self, *, disable_all: bool = False) -> None:
        self.previous_page.disabled = disable_all or self.current_page_index <= 0
        self.next_page.disabled = disable_all or self.current_page_index >= len(self.pages) - 1

    def _priority_window_remaining(self) -> float:
        if self.last_sender_click_time is None:
            return 0.0
        elapsed = (datetime.now(timezone.utc) - self.last_sender_click_time).total_seconds()
        return max(0.0, self.priority_window_seconds - elapsed)

    async def _handle_navigation(self, interaction: discord.Interaction, delta: int) -> None:
        async with self._interaction_lock:
            if interaction.user.id != self.sender_user_id:
                remaining = self._priority_window_remaining()
                if remaining > 0:
                    await interaction.response.send_message(
                        f"The message sender has priority for {remaining:.1f}s.",
                        ephemeral=True,
                    )
                    return

            new_index = self.current_page_index + delta
            if new_index < 0 or new_index >= len(self.pages):
                await interaction.response.defer()
                return

            self.current_page_index = new_index
            if interaction.user.id == self.sender_user_id:
                self.last_sender_click_time = datetime.now(timezone.utc)

            self._update_button_states()
            await interaction.response.edit_message(embed=self.build_embed(), view=self)

    @discord.ui.button(label="\u2B05\uFE0F", style=discord.ButtonStyle.secondary)
    async def previous_page(self, interaction: discord.Interaction, _button: discord.ui.Button):
        await self._handle_navigation(interaction, -1)

    @discord.ui.button(label="\u27A1\uFE0F", style=discord.ButtonStyle.secondary)
    async def next_page(self, interaction: discord.Interaction, _button: discord.ui.Button):
        await self._handle_navigation(interaction, 1)

    async def on_timeout(self) -> None:
        self._update_button_states(disable_all=True)
        if not self.message:
            return
        try:
            await self.message.edit(view=self)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            pass


class ResponseDeliveryCoordinator:
    """Render and deliver model responses while preserving Discord semantics."""

    def __init__(
        self,
        *,
        message_splitter: Any,
        content_renderer: Any,
        error_manager: Any,
        get_split_length: Callable[[], int],
        index_sent_bot_response: AsyncCallback,
    ) -> None:
        self.message_splitter = message_splitter
        self.content_renderer = content_renderer
        self.error_manager = error_manager
        self.get_split_length = get_split_length
        self.index_sent_bot_response = index_sent_bot_response

    async def send_response_safely(
        self,
        message: discord.Message,
        response_content: str,
        grounding_sources: Optional[list] = None,
    ) -> Optional[discord.Message]:
        """Render and send one response with the existing Discord fallbacks."""
        try:
            if not response_content or not response_content.strip():
                logger.warning("Empty response content generated")
                await message.reply(
                    "I generated a response, but it appears to be empty. "
                    "Could you try asking differently? 🤔"
                )
                return None

            rendered = self.content_renderer.process_response(response_content)
            response_content = rendered.text
            attachments = rendered.attachments
            if attachments:
                logger.info(
                    "Content renderer produced %s image attachment(s) (LaTeX)",
                    len(attachments),
                )

            direct_message_limit = min(self.get_split_length(), 2000)
            if len(response_content) > direct_message_limit:
                logger.info(
                    "Response too long (%s chars), splitting into multiple messages",
                    len(response_content),
                )
                sent_message = await self.send_split_response(
                    message,
                    response_content,
                    attachments=attachments,
                )
            else:
                sent_message = await message.reply(
                    response_content,
                    files=attachments if attachments else None,
                )
                logger.info(
                    "Successfully sent response to %s in #%s",
                    message.author,
                    getattr(message.channel, "name", "DM"),
                )

            if grounding_sources:
                await self.send_grounding_sources(sent_message, grounding_sources)

            await self.index_sent_bot_response(
                source_message=message,
                sent_message=sent_message,
                response_content=response_content,
            )
            return sent_message

        except discord.Forbidden as exc:
            logger.error("Permission denied sending response in channel %s", message.channel.id)
            error_context = self.error_manager.create_error_context(
                exc,
                "I don't have permission to send messages in this channel. "
                "Please check my permissions! 🔒",
            )
            await self.error_manager.send_error_response(
                message,
                error_context,
                fallback_reaction="🔒",
            )

        except discord.HTTPException as exc:
            if exc.status == 429:
                logger.warning("Rate limited sending response: %s", exc)
                error_context = self.error_manager.create_error_context(
                    exc,
                    "I'm being rate limited by Discord. Please try again in a moment! 🕒",
                )
            elif exc.status >= 500:
                logger.error("Discord server error sending response: %s", exc)
                error_context = self.error_manager.create_error_context(
                    exc,
                    "Discord is experiencing issues. Please try again in a moment! 🛠️",
                )
            else:
                logger.error("HTTP error sending response: %s", exc)
                error_context = self.error_manager.create_error_context(
                    exc,
                    "I had trouble sending my response. Please try again! 📤",
                )

            await self.error_manager.send_error_response(
                message,
                error_context,
                fallback_reaction="⚠️",
            )

        except discord.NotFound as exc:
            logger.error("Message or channel not found: %s", exc)
            error_context = self.error_manager.create_error_context(
                exc,
                "The message or channel no longer exists. Please try again! 🔍",
            )
            await self.error_manager.send_error_response(
                message,
                error_context,
                fallback_reaction="❓",
            )

        except Exception as exc:
            logger.error("Unexpected error sending response: %s", exc, exc_info=True)
            error_context = self.error_manager.handle_discord_error(exc, message)
            await self.error_manager.send_error_response(
                message,
                error_context,
                fallback_reaction="⚠️",
            )

        return None

    @staticmethod
    def clean_split_part_for_embed(content: str) -> str:
        """Remove continuation markers so embed pages show clean content."""
        cleaned = re.sub(r'^\*\(continued from part \d+/\d+\)\*\n\n', '', content)
        cleaned = re.sub(r'\n\n\*\(continues in part \d+/\d+\)\*$', '', cleaned)
        cleaned = re.sub(r'^\*\(continued\.\.\.\)\*\n\n', '', cleaned)
        cleaned = re.sub(r'\n\n\*\(continues\.\.\.\)\*$', '', cleaned)
        cleaned = cleaned.strip()
        return cleaned if cleaned else content.strip()

    async def send_paginated_embed(
        self,
        pages: List[str],
        sender_user_id: int,
        send_page_callable: Callable[..., Awaitable[discord.Message]],
        *,
        attachments: Optional[List[discord.File]] = None,
        title: str = "Response",
    ) -> discord.Message:
        """Send split content as one embed with arrow navigation."""
        view = SplitResponsePaginatorView(
            pages=pages,
            sender_user_id=sender_user_id,
            title=title,
            priority_window_seconds=2.0,
            timeout=120.0,
        )
        send_kwargs = {"embed": view.build_embed(), "view": view}
        if attachments:
            send_kwargs["files"] = attachments

        sent_message = await send_page_callable(**send_kwargs)
        view.message = sent_message
        return sent_message

    async def send_split_response(
        self,
        message: discord.Message,
        response_content: str,
        attachments: Optional[list] = None,
    ) -> discord.Message:
        """Split a long response and prefer a single paginator message."""
        try:
            message_parts = self.message_splitter.split_message(response_content)
            stats = self.message_splitter.get_split_statistics(message_parts)
            logger.info("Message split statistics: %s", stats)

            if not self.message_splitter.validate_split_integrity(
                response_content,
                message_parts,
            ):
                logger.warning("Split integrity validation failed, falling back to simple split")
                return await self.send_simple_split_response(
                    message,
                    response_content,
                    attachments=attachments,
                )

            if len(message_parts) <= 1:
                return await message.reply(
                    message_parts[0].content,
                    files=attachments if attachments else None,
                )

            embed_pages = [
                self.clean_split_part_for_embed(part.content)
                for part in message_parts
            ]

            async def _send_first_page(**kwargs) -> discord.Message:
                return await message.reply(**kwargs)

            sent_message = await self.send_paginated_embed(
                pages=embed_pages,
                sender_user_id=message.author.id,
                send_page_callable=_send_first_page,
                attachments=attachments,
                title="Response",
            )
            logger.info(
                "Successfully sent response in paginated embed with %s pages",
                len(embed_pages),
            )
            return sent_message

        except Exception as exc:
            logger.error("Error in intelligent message splitting: %s", exc, exc_info=True)
            logger.info("Falling back to simple message splitting")
            return await self.send_simple_split_response(
                message,
                response_content,
                attachments=attachments,
            )

    async def send_simple_split_response(
        self,
        message: discord.Message,
        response_content: str,
        attachments: Optional[list] = None,
    ) -> discord.Message:
        """Send lossless fixed-size chunks when intelligent splitting fails."""
        max_length = min(self.get_split_length(), 2000)
        parts = [
            response_content[start:start + max_length]
            for start in range(0, len(response_content), max_length)
        ]
        first_message = None
        for index, part in enumerate(parts):
            if index == 0:
                sent = await message.reply(
                    part,
                    files=attachments if attachments else None,
                )
                first_message = sent
            else:
                await message.channel.send(part)
            logger.info("Sent message part %s/%s", index + 1, len(parts))

        logger.info(
            "Successfully sent response in %s parts using simple splitting",
            len(parts),
        )
        return first_message

    async def send_grounding_sources(
        self,
        reply_message: discord.Message,
        grounding_sources: list,
    ) -> None:
        """Reply to the response with up to ten grounding sources."""
        try:
            if not grounding_sources:
                return

            sources_text = "📚 **Sources:**\n"
            for index, source in enumerate(grounding_sources[:10], 1):
                uri = source.get("uri", "")
                title = source.get("title")
                if title:
                    sources_text += f"{index}. {title}: <{uri}>\n"
                else:
                    sources_text += f"{index}. <{uri}>\n"

            if len(sources_text) > 2000:
                sources_text = sources_text[:1997] + "..."

            await reply_message.reply(sources_text)
            logger.info("Successfully sent %s grounding sources", len(grounding_sources))
        except discord.Forbidden:
            logger.warning("No permission to send grounding sources message")
        except discord.HTTPException as exc:
            logger.error("Failed to send grounding sources: %s", exc)
        except Exception as exc:
            logger.error("Unexpected error sending grounding sources: %s", exc, exc_info=True)
