"""Research, retrieval, and summarization slash-command registrars."""

import logging
import os
from io import BytesIO
from datetime import datetime
from typing import Optional

import discord
import jinja2
from discord import app_commands

from ...models.data_models import MessageContext
from .context import CommandContext


logger = logging.getLogger("src.bot.commands")


def register_deepresearch_command(context: CommandContext) -> None:
    bot = context.bot
    config = context.config
    gemini_client = context.gemini_client
    performance_logger = context.performance_logger
    token_tracker = context.token_tracker

    @bot.tree.command(name="deepresearch", description="Perform a deep research task and generate a report")
    @app_commands.describe(topic="The topic to research")
    async def deepresearch(interaction: discord.Interaction, topic: str):
        """Perform deep research on a topic and generate a comprehensive report."""
        await interaction.response.defer(thinking=True)

        try:
            research_complexity = "medium"
            synthesis_complexity = "high"
            research_cfg = config.model_complexity.get(research_complexity, {})
            synthesis_cfg = config.model_complexity.get(synthesis_complexity, {})
            research_model = str(research_cfg.get("model", gemini_client.get_current_model()))
            research_thinking = str(research_cfg.get("thinking_level", "default"))
            synthesis_model = str(synthesis_cfg.get("model", gemini_client.get_current_model()))
            synthesis_thinking = str(synthesis_cfg.get("thinking_level", "default"))

            # Step 1: Research Phase (Flash + Search)
            await interaction.followup.send(f"🔍 **Starting Deep Research on:** *{topic}*\nStep 1/2: Gathering information...")

            research_prompt = f"Research the following topic in depth: {topic}. Provide comprehensive details, facts, statistics, and different perspectives. Focus on gathering raw information."

            research_response = await gemini_client.generate_response(
                prompt=research_prompt,
                model_override=research_model,
                complexity_override=research_complexity,
                thinking_level_override=research_thinking,
                search_override=True
            )

            if not research_response.success:
                await interaction.followup.send(f"❌ Research failed: {research_response.content}")
                return

            research_data = research_response.content

            # Step 2: Thinking Phase (Pro + Template)
            await interaction.followup.send(f"🧠 Step 2/2: Analyzing and synthesizing report...")

            # Load template
            template_path = os.path.join("grok-prompts", "default_deepsearch_final_summarizer_prompt.j2")
            if not os.path.exists(template_path):
                # Fallback if path is different or running from different cwd
                template_path = os.path.join(os.getcwd(), "grok-prompts", "default_deepsearch_final_summarizer_prompt.j2")

            try:
                with open(template_path, "r", encoding="utf-8") as f:
                    template_content = f.read()

                template = jinja2.Template(template_content)
                final_prompt = template.render(
                    question=topic,
                    answer=research_data,
                    current_time=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    language="English",
                    prefill=False,
                    is_grok_file_update_request=False,
                    real_time_data_provider_called=False,
                    contains_url=True,
                    supported_inline_rich_content_tools=False
                )
            except Exception as e:
                logger.error(f"Template error: {e}")
                # Fallback prompt if template fails
                final_prompt = f"User Query: {topic}\n\nResearch Data:\n{research_data}\n\nPlease write a comprehensive deep research report based on the above data."

            # Generate final report using Pro model
            report_response = await gemini_client.generate_response(
                prompt=final_prompt,
                model_override=synthesis_model,
                complexity_override=synthesis_complexity,
                thinking_level_override=synthesis_thinking,
                search_override=False # We already searched
            )

            if not report_response.success:
                await interaction.followup.send(f"❌ Report generation failed: {report_response.content}")
                return

            report_content = report_response.content

            # Step 3: Upload File
            file_buffer = BytesIO(report_content.encode('utf-8'))
            # Sanitize filename
            safe_topic = "".join([c for c in topic if c.isalnum() or c in (' ', '-', '_')]).strip()
            filename = f"DeepResearch_{safe_topic[:30].replace(' ', '_')}.md"
            discord_file = discord.File(file_buffer, filename=filename)

            await interaction.followup.send(f"✅ **Deep Research Complete!**\nHere is your report on: *{topic}*", file=discord_file)

        except Exception as e:
            logger.error(f"Deep research error: {e}", exc_info=True)
            await interaction.followup.send(f"❌ An error occurred during deep research: {str(e)}")



def create_rag_group(context: CommandContext) -> app_commands.Group:
    bot = context.bot
    config = context.config
    gemini_client = context.gemini_client
    performance_logger = context.performance_logger
    token_tracker = context.token_tracker

    rag_group = app_commands.Group(name="rag", description="Manage local message retrieval memory")

    @rag_group.command(name="status", description="Show local message RAG index status")
    async def rag_status(interaction: discord.Interaction):
        index_service = getattr(bot, "message_index_service", None)
        if not index_service:
            await interaction.response.send_message("Message RAG index is not available.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        status = await index_service.get_status_async(channel_id=interaction.channel_id)
        embed = discord.Embed(
            title="Message RAG Status",
            color=discord.Color.blurple(),
            timestamp=datetime.now(),
        )
        embed.add_field(name="Enabled", value=str(config.rag_enabled), inline=True)
        embed.add_field(name="FTS5", value="Enabled" if status["fts_enabled"] else "Unavailable", inline=True)
        embed.add_field(
            name="Embedding Model",
            value=f"{status['embedding_model']} ({status['embedding_dimensions']} dimensions)",
            inline=False,
        )
        embed.add_field(name="Local Database", value=status["database_path"], inline=False)
        embed.add_field(name="Indexed Messages", value=f"{status['messages']:,}", inline=True)
        embed.add_field(name="Embedded", value=f"{status['embedded']:,}", inline=True)
        embed.add_field(name="Pending", value=f"{status['pending_embeddings']:,}", inline=True)
        embed.add_field(name="Failed Embeddings", value=f"{status['failed_embeddings']:,}", inline=True)
        embed.add_field(name="Skipped (Trivial)", value=f"{status.get('skipped_embeddings', 0):,}", inline=True)
        embed.add_field(name="Cached Vectors", value=f"{status.get('cached_vectors', 0):,}", inline=True)
        embed.add_field(name="Cache RAM", value=f"{status.get('vector_cache_bytes', 0) / (1024 * 1024):.1f} MiB", inline=True)

        retriever = getattr(bot, "hybrid_context_retriever", None)
        pregeneration = (
            retriever.get_pregeneration_status(interaction.channel_id)
            if retriever
            else None
        )
        if pregeneration:
            scope = (
                "entire accessible history"
                if pregeneration["limit"] is None
                else f"up to {pregeneration['limit']:,} messages"
            )
            details = [
                f"Phase: **{pregeneration['phase'].title()}**",
                f"Scope: {scope}",
                f"Indexed this run: {pregeneration['indexed']:,}",
                f"Embedded this run: {pregeneration['embedded']:,}",
            ]
            if pregeneration["error"]:
                details.append(f"Error: {pregeneration['error'][:300]}")
            embed.add_field(
                name="Pre-generation Job",
                value="\n".join(details),
                inline=False,
            )
        embed.set_footer(text="Counts are scoped to this channel where applicable.")
        await interaction.followup.send(embed=embed, ephemeral=True)

    @rag_group.command(name="backfill", description="Pre-generate local RAG data from channel history")
    @app_commands.describe(limit="Messages to scan; omit or use 0 for the entire accessible history")
    async def rag_backfill(interaction: discord.Interaction, limit: Optional[int] = None):
        index_service = getattr(bot, "message_index_service", None)
        if not index_service:
            await interaction.response.send_message("Message RAG index is not available.", ephemeral=True)
            return
        if not interaction.channel:
            await interaction.response.send_message("This command must be run in a channel.", ephemeral=True)
            return

        retriever = getattr(bot, "hybrid_context_retriever", None)
        if not retriever:
            await interaction.response.send_message("Message RAG pre-generation is not available.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        configured_limit = config.rag_backfill_limit if limit is None else limit
        scan_limit = None if configured_limit <= 0 else configured_limit
        started = retriever.start_channel_pregeneration(
            interaction.channel,
            limit=scan_limit,
            include_bot_user_id=bot.user.id if config.rag_index_bot_responses and bot.user else None,
        )
        if not started:
            await interaction.followup.send(
                "A RAG pre-generation job is already running for this channel.",
                ephemeral=True,
            )
            return

        scope = (
            "the entire accessible channel history"
            if scan_limit is None
            else f"up to {scan_limit:,} messages"
        )
        await interaction.followup.send(
            f"Started RAG pre-generation for {scope}. Data is saved incrementally to "
            f"`{index_service.db_path}`. Use `/rag status` to monitor it.",
            ephemeral=True,
        )

    @rag_group.command(name="delete", description="Delete stored message RAG data")
    @app_commands.describe(scope="Choose whether to delete this channel or every channel")
    @app_commands.choices(scope=[
        app_commands.Choice(name="Current channel", value="channel"),
        app_commands.Choice(name="All channels", value="all"),
    ])
    async def rag_delete(
        interaction: discord.Interaction,
        scope: app_commands.Choice[str],
    ):
        index_service = getattr(bot, "message_index_service", None)
        if not index_service:
            await interaction.response.send_message(
                "Message RAG index is not available.",
                ephemeral=True,
            )
            return
        if scope.value == "channel" and interaction.channel_id is None:
            await interaction.response.send_message(
                "This command must be run in a channel.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)
        channel_id = interaction.channel_id if scope.value == "channel" else None
        retriever = getattr(bot, "hybrid_context_retriever", None)
        try:
            if retriever:
                await retriever.cancel_background_work(channel_id=channel_id)
            deleted = await index_service.delete_rag_data_async(
                channel_id=channel_id,
            )
        except Exception as exc:
            logger.error("RAG deletion failed: %s", exc, exc_info=True)
            await interaction.followup.send(
                "Failed to delete the stored RAG data. Check the bot logs for details.",
                ephemeral=True,
            )
            return

        deleted_scope = "this channel" if channel_id is not None else "all channels"
        await interaction.followup.send(
            f"Deleted {deleted['messages']:,} indexed RAG message(s) and "
            f"{deleted['pins']:,} pinned memory item(s) for {deleted_scope}. "
            "New messages can be indexed again automatically.",
            ephemeral=True,
        )


    return rag_group


def register_summarize_command(context: CommandContext) -> None:
    bot = context.bot
    config = context.config
    gemini_client = context.gemini_client
    performance_logger = context.performance_logger
    token_tracker = context.token_tracker

    @bot.tree.command(name="summarize", description="Summarize the current conversation")
    async def summarize(interaction: discord.Interaction):
        """Summarize the conversation in the current channel."""
        await interaction.response.defer(thinking=True)

        messages = []
        last_msg_time = None

        try:
            # Fetch messages backwards
            # Limit to 500 to avoid excessive processing, but should cover most "current" conversations
            async for message in interaction.channel.history(limit=config.channel_history_limit):
                current_msg_time = message.created_at

                if last_msg_time:
                    time_diff = last_msg_time - current_msg_time
                    # If gap is greater than 24 hours, stop fetching
                    if time_diff.total_seconds() > 86400:
                        break

                messages.append(message)
                last_msg_time = current_msg_time

            if not messages:
                await interaction.followup.send("No recent conversation found to summarize.")
                return

            # Reverse to chronological order (oldest to newest)
            messages.reverse()

            # Build context list
            context_list = []
            for msg in messages:
                content = msg.content

                # Handle attachments
                if msg.attachments:
                    attachment_names = [att.filename for att in msg.attachments]
                    if content:
                        content += f" [Attachments: {', '.join(attachment_names)}]"
                    else:
                        content = f"[Attachments: {', '.join(attachment_names)}]"

                # Create MessageContext
                msg_context = MessageContext(
                    content=content,
                    author=msg.author.display_name,
                    timestamp=msg.created_at,
                    message_id=msg.id,
                    is_reply=(msg.reference is not None),
                    replied_to_id=msg.reference.message_id if msg.reference else None
                )
                context_list.append(msg_context)

            prompt = "Please summarize the conversation. Focus on the main topics discussed, key decisions made, and any action items."

            # Generate summary using context
            response = await gemini_client.generate_response(prompt, context=context_list)

            if response.success:
                summary = response.content
                # Check length limits
                safe_len = config.safe_split_length
                if len(summary) > safe_len:
                    # Split into chunks
                    chunks = [summary[i:i+safe_len] for i in range(0, len(summary), safe_len)]

                    async def _send_summary_page(**kwargs):
                        return await interaction.followup.send(wait=True, **kwargs)

                    try:
                        await bot._send_paginated_embed(
                            pages=chunks,
                            sender_user_id=interaction.user.id,
                            send_page_callable=_send_summary_page,
                            title="Conversation Summary",
                        )
                    except Exception as paginate_error:
                        logger.warning(f"Falling back to flat summary chunks: {paginate_error}")
                        await interaction.followup.send(f"\u2705 **Conversation Summary** (Part 1/{len(chunks)})")
                        await interaction.followup.send(chunks[0])
                        for i, chunk in enumerate(chunks[1:], 1):
                            await interaction.channel.send(f"**(Part {i+1}/{len(chunks)})**\n{chunk}")
                else:
                    await interaction.followup.send(f"✅ **Conversation Summary**\n\n{summary}")
            else:
                await interaction.followup.send(f"❌ Failed to generate summary: {response.content}")

        except Exception as e:
            logger.error(f"Summarize error: {e}", exc_info=True)
            await interaction.followup.send(f"❌ An error occurred while summarizing: {str(e)}")

    # ── Personality / Tone Command ────────────────────────────────────
