"""General, feature-discovery, and image slash-command registrars."""

import asyncio
import logging
from datetime import datetime
from typing import Optional

import discord
from discord import app_commands

from ...models.data_models import EditType, ImageEditRequest
from .context import CommandContext


logger = logging.getLogger("src.bot.commands")


def register_ping_command(context: CommandContext) -> None:
    bot = context.bot
    config = context.config
    gemini_client = context.gemini_client
    performance_logger = context.performance_logger
    token_tracker = context.token_tracker

    @bot.tree.command(name="ping", description="Check if the bot is responsive")
    async def ping(interaction: discord.Interaction):
        """Check bot responsiveness and latency."""
        latency = round(bot.latency * 1000)

        embed = discord.Embed(
            title="🏓 Pong!",
            description=f"Bot is online and responsive!",
            color=discord.Color.green()
        )
        embed.add_field(name="Latency", value=f"{latency}ms", inline=True)
        embed.add_field(name="Status", value="✅ Operational", inline=True)

        await interaction.response.send_message(embed=embed)



def register_feature_commands(context: CommandContext) -> None:
    bot = context.bot
    config = context.config
    gemini_client = context.gemini_client
    performance_logger = context.performance_logger
    token_tracker = context.token_tracker

    @bot.tree.command(name="features", description="Discover all available bot features and capabilities")
    async def features_command(interaction: discord.Interaction):
        """Display feature discovery information."""

        # Feature discovery
        embed = discord.Embed(
            title="✨ Bot Features & Capabilities",
            description="Discover everything this bot can do for you!",
            color=discord.Color.purple()
        )

        # Core AI features
        embed.add_field(
            name="🤖 AI Conversations",
            value="• **Context-aware responses** - Remembers conversation history\n"
                  "• **Multi-turn conversations** - Natural dialogue flow\n"
                  "• **Reply chain analysis** - Understands conversation threads\n"
                  "• **File analysis** - Upload PDFs and text files\n"
                  "• **Multiple AI models** - Choose the best for your needs",
            inline=False
        )

        # Image editing (if available)
        if hasattr(bot, 'image_processing_service') and bot.image_processing_service:
            embed.add_field(
                name="🖼️ Image Editing",
                value="• **Object removal** - Remove unwanted elements\n"
                      "• **Background replacement** - Change image backgrounds\n"
                      "• **Artistic style transfer** - Apply artistic styles\n"
                      "• **Color adjustments** - Brightness, contrast, saturation\n"
                      "• **Natural language instructions** - No complex commands needed",
                inline=False
            )

        # Enhanced UX features
        embed.add_field(
            name="✨ Enhanced Experience",
            value="• **Smart message splitting** - Long responses split intelligently\n"
                  "• **Rich embeds** - Beautiful, structured responses\n"
                  "• **Typing indicators** - Shows when bot is working\n"
                  "• **Reaction feedback** - Visual confirmation of actions\n"
                  "• **Progress updates** - Real-time processing status",
            inline=False
        )

        # Getting started
        embed.add_field(
            name="🚀 Getting Started",
            value="1. **Mention the bot** in any message: `@bot your question`\n"
                  "2. **Upload images** with editing instructions\n"
                  "3. **Use slash commands** for quick actions\n"
                  "4. **Reply to bot messages** for context-aware conversations\n"
                  "5. **Try different AI models** with `/model`",
            inline=False
        )

        embed.set_footer(text="Use /help for detailed instructions • Try mentioning the bot to get started!")

        await interaction.response.send_message(embed=embed)


    # Image editing commands (only if image processing is available)
    if hasattr(bot, 'image_processing_service') and bot.image_processing_service:

        @bot.tree.command(name="edit-image", description="Edit an uploaded image using AI")
        @app_commands.describe(
            image="The image to edit",
            instruction="What you want to do to the image",
            edit_type="Type of edit to perform (optional - will be auto-detected)"
        )
        @app_commands.choices(edit_type=[
            app_commands.Choice(name="Auto-detect (Recommended)", value="general_edit"),
            app_commands.Choice(name="Remove Object", value="object_removal"),
            app_commands.Choice(name="Change Background", value="background_replacement"),
            app_commands.Choice(name="Apply Style", value="style_transfer"),
            app_commands.Choice(name="Adjust Colors", value="color_adjustment"),
        ])
        async def edit_image(interaction: discord.Interaction, image: discord.Attachment,
                           instruction: str, edit_type: Optional[app_commands.Choice[str]] = None):
            """Edit an image using AI image processing."""
            try:
                # Validate image attachment
                if not image.content_type or not image.content_type.startswith('image/'):
                    await interaction.response.send_message(
                        "❌ Please upload a valid image file (PNG, JPEG, GIF)."
                    )
                    return

                # Check image size
                max_size_bytes = bot.config.max_image_size_mb * 1024 * 1024
                if image.size > max_size_bytes:
                    await interaction.response.send_message(
                        f"❌ Image is too large. Maximum size is {bot.config.max_image_size_mb}MB."
                    )
                    return

                # Defer response since this will take time
                await interaction.response.defer()

                # Download image
                image_data = await image.read()

                # Determine edit type
                if edit_type:
                    selected_edit_type = EditType(edit_type.value)
                else:
                    selected_edit_type = EditType.GENERAL_EDIT

                # Create edit request
                edit_request = ImageEditRequest(
                    user_id=str(interaction.user.id),
                    image_data=image_data,
                    instruction=instruction,
                    edit_type=selected_edit_type,
                    timestamp=datetime.now(),
                    channel_id=str(interaction.channel.id)
                )

                # Check rate limits
                rate_limit_info = await bot.image_processing_service.get_user_rate_limit_info(str(interaction.user.id))
                if rate_limit_info['requests_remaining'] <= 0:
                    reset_time = rate_limit_info.get('reset_time')
                    if reset_time:
                        await interaction.followup.send(f"⏳ You've reached your image editing limit. Try again after {reset_time}.")
                    else:
                        await interaction.followup.send("⏳ You've reached your image editing limit. Please try again later.")
                    return

                # Submit job
                job_id = await bot.image_processing_service.process_image_edit(edit_request)

                # Send initial response
                embed = discord.Embed(
                    title="🎨 Image Editing Started",
                    description=f"Processing your image edit request...",
                    color=discord.Color.blue()
                )
                embed.add_field(name="Instruction", value=instruction, inline=False)
                embed.add_field(name="Edit Type", value=selected_edit_type.value.replace('_', ' ').title(), inline=True)
                embed.add_field(name="Status", value="⏳ Processing...", inline=True)

                await interaction.followup.send(embed=embed)

                # Wait for completion
                max_wait_time = 120  # 2 minutes
                check_interval = 3
                elapsed_time = 0

                while elapsed_time < max_wait_time:
                    job_status = await bot.image_processing_service.get_job_status(job_id)

                    if not job_status:
                        await interaction.followup.send("❌ Image editing job was lost. Please try again.")
                        return

                    if job_status.status.value == "completed":
                        if job_status.result and job_status.result.success:
                            # Send the edited image
                            import io
                            edited_image_file = discord.File(
                                io.BytesIO(job_status.result.edited_image),
                                filename=f"edited_{interaction.user.id}_{int(datetime.now().timestamp())}.png"
                            )

                            success_embed = discord.Embed(
                                title="✅ Image Editing Complete",
                                description=f"Your image has been successfully edited!",
                                color=discord.Color.green()
                            )
                            success_embed.add_field(
                                name="Processing Time",
                                value=f"{job_status.result.processing_time:.1f} seconds",
                                inline=True
                            )

                            await interaction.followup.send(embed=success_embed, file=edited_image_file)
                            return
                        else:
                            error_msg = job_status.result.error_message if job_status.result else "Unknown error"
                            await interaction.followup.send(f"❌ Image editing failed: {error_msg}")
                            return

                    elif job_status.status.value == "failed":
                        error_msg = job_status.error_message or "Unknown error"
                        await interaction.followup.send(f"❌ Image editing failed: {error_msg}")
                        return

                    await asyncio.sleep(check_interval)
                    elapsed_time += check_interval

                # Timeout
                await interaction.followup.send("⏰ Image editing is taking longer than expected. Please try again later.")

            except Exception as e:
                logger.error(f"Error in edit-image command: {e}", exc_info=True)
                try:
                    await interaction.followup.send(f"❌ An error occurred: {str(e)}")
                except discord.HTTPException:
                    await interaction.response.send_message(f"❌ An error occurred: {str(e)}")


        @bot.tree.command(name="image-queue", description="Check the image processing queue status")
        async def image_queue(interaction: discord.Interaction):
            """Check the status of the image processing queue."""
            try:
                queue_info = bot.image_processing_service.get_queue_info()
                stats = bot.image_processing_service.get_statistics()

                embed = discord.Embed(
                    title="🎨 Image Processing Queue",
                    description="Current queue status and statistics",
                    color=discord.Color.blue()
                )

                embed.add_field(
                    name="📊 Queue Status",
                    value=f"**Queued Jobs:** {queue_info['queue_size']}\n"
                          f"**Active Jobs:** {queue_info['active_jobs']}\n"
                          f"**Max Concurrent:** {queue_info['max_concurrent']}",
                    inline=True
                )

                embed.add_field(
                    name="📈 Statistics",
                    value=f"**Total Requests:** {stats['total_requests']}\n"
                          f"**Successful:** {stats['successful_requests']}\n"
                          f"**Failed:** {stats['failed_requests']}",
                    inline=True
                )

                if stats['average_processing_time'] > 0:
                    embed.add_field(
                        name="⏱️ Performance",
                        value=f"**Avg Processing Time:** {stats['average_processing_time']:.1f}s\n"
                              f"**Service Available:** {'✅' if queue_info['service_available'] else '❌'}",
                        inline=True
                    )

                # User rate limit info
                rate_limit_info = await bot.image_processing_service.get_user_rate_limit_info(str(interaction.user.id))
                embed.add_field(
                    name="👤 Your Rate Limit",
                    value=f"**Requests Used:** {rate_limit_info['requests_used']}\n"
                          f"**Remaining:** {rate_limit_info['requests_remaining']}",
                    inline=False
                )

                await interaction.response.send_message(embed=embed)

            except Exception as e:
                logger.error(f"Error in image-queue command: {e}")
                await interaction.response.send_message(
                    "❌ Failed to get queue information. Please try again later."
                )
