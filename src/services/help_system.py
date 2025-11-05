"""
Advanced help and command suggestion system for Discord bot.

This module provides contextual help, command suggestions for typos,
feature discovery, and usage guidance.
"""

import logging
import difflib
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass
from enum import Enum

import discord

from ..config import BotConfig
from .user_experience_service import UserExperienceService, Status


logger = logging.getLogger(__name__)


class HelpCategory(Enum):
    """Categories for organizing help content."""
    GENERAL = "general"
    IMAGE_EDITING = "image_editing"
    COMMANDS = "commands"
    FEATURES = "features"
    TROUBLESHOOTING = "troubleshooting"


@dataclass
class HelpSection:
    """Structure for help content sections."""
    title: str
    content: str
    category: HelpCategory
    keywords: List[str]
    examples: Optional[List[str]] = None


@dataclass
class CommandSuggestion:
    """Structure for command suggestions."""
    original: str
    suggestion: str
    confidence: float
    reason: str


class HelpSystem:
    """
    Advanced help and command suggestion system.
    
    Implements requirements 5.2, 5.3, 5.4, 5.5 for contextual help,
    command suggestions, feature discovery, and usage guidance.
    """
    
    def __init__(self, config: BotConfig, ux_service: UserExperienceService):
        """
        Initialize the help system.
        
        Args:
            config: Bot configuration
            ux_service: User experience service for creating embeds
        """
        self.config = config
        self.ux_service = ux_service
        
        # Initialize help content
        self._initialize_help_content()
        
        # Initialize command database for suggestions
        self._initialize_command_database()
    
    def _initialize_help_content(self):
        """Initialize the help content database."""
        self.help_sections = [
            HelpSection(
                title="Getting Started",
                content="Simply mention me (@bot) in any message to get an AI response! "
                       "I can help with questions, analysis, conversations, and image editing.",
                category=HelpCategory.GENERAL,
                keywords=["start", "begin", "how", "use", "basic"],
                examples=[
                    "@bot What's the weather like?",
                    "@bot Explain quantum physics",
                    "@bot Help me write an email"
                ]
            ),
            HelpSection(
                title="Image Editing",
                content="Upload an image and mention me with natural language instructions. "
                       "I can remove objects, change backgrounds, apply artistic styles, and adjust colors.",
                category=HelpCategory.IMAGE_EDITING,
                keywords=["image", "edit", "photo", "picture", "visual"],
                examples=[
                    "@bot remove the background",
                    "@bot make this look like a painting",
                    "@bot brighten this image",
                    "@bot replace background with beach scene",
                    "@bot remove the person in red"
                ]
            ),
            HelpSection(
                title="Slash Commands",
                content="Use slash commands for quick access to bot features and settings.",
                category=HelpCategory.COMMANDS,
                keywords=["command", "slash", "/", "function"],
                examples=[
                    "/help - Show detailed help",
                    "/ping - Check bot status",
                    "/model - Switch AI models",
                    "/stats - View usage statistics",
                    "/config - View current settings"
                ]
            ),
            HelpSection(
                title="Advanced Features",
                content="I can analyze context from conversation history, process multiple images, "
                       "read uploaded files, and provide detailed responses with sources.",
                category=HelpCategory.FEATURES,
                keywords=["advanced", "feature", "context", "file", "analysis"],
                examples=[
                    "Reply to messages for context-aware responses",
                    "Upload PDFs for document analysis",
                    "Attach multiple images for comparison",
                    "Use thinking mode for detailed analysis"
                ]
            ),
            HelpSection(
                title="Troubleshooting",
                content="If you're having issues, check my permissions, try rephrasing your request, "
                       "or use simpler language. For image editing, ensure images are under 10MB.",
                category=HelpCategory.TROUBLESHOOTING,
                keywords=["problem", "issue", "error", "help", "trouble", "fix"],
                examples=[
                    "Check bot permissions in server settings",
                    "Try uploading smaller images",
                    "Rephrase your request more clearly",
                    "Use /ping to check if bot is responsive"
                ]
            )
        ]
    
    def _initialize_command_database(self):
        """Initialize the command database for suggestions."""
        self.known_commands = {
            # Slash commands
            "/help": "Show help information",
            "/ping": "Check bot status",
            "/model": "Switch AI models",
            "/stats": "View usage statistics",
            "/config": "View configuration",
            "/prompt-mode": "Switch response modes",
            "/clear-cache": "Clear bot cache (admin)",
            
            # Natural language commands
            "edit image": "Edit uploaded images",
            "remove background": "Remove image background",
            "change background": "Replace image background",
            "make artistic": "Apply artistic style to image",
            "brighten image": "Adjust image brightness",
            "help": "Get help information",
            "what can you do": "Show bot capabilities",
            "how to use": "Show usage instructions"
        }
        
        # Common typos and variations
        self.command_variations = {
            "hlep": "help",
            "halp": "help",
            "hepl": "help",
            "pign": "ping",
            "pgni": "ping",
            "stat": "stats",
            "stast": "stats",
            "modle": "model",
            "modl": "model",
            "edit": "edit image",
            "eidt": "edit image",
            "remov": "remove background",
            "chang": "change background",
            "brighten": "brighten image",
            "briten": "brighten image"
        }
    
    async def provide_contextual_help(
        self,
        message: discord.Message,
        query: Optional[str] = None
    ) -> discord.Embed:
        """
        Provide contextual help based on user query or message content.
        
        Implements requirement 5.2: Create contextual help command with examples.
        
        Args:
            message: Discord message requesting help
            query: Optional specific help query
            
        Returns:
            Help embed tailored to the context
        """
        try:
            # Determine help context from message or query
            context = query or message.content.lower()
            
            # Find relevant help sections
            relevant_sections = self._find_relevant_help_sections(context)
            
            if not relevant_sections:
                # Provide general help if no specific context found
                return self._create_general_help_embed()
            
            # Create contextual help embed
            embed = self.ux_service.create_status_embed(
                title="🤖 Contextual Help",
                description="Here's information relevant to your question:",
                status=Status.INFO
            )
            
            # Add relevant sections
            for section in relevant_sections[:3]:  # Limit to 3 sections
                field_value = section.content
                
                if section.examples:
                    field_value += "\n\n**Examples:**\n"
                    field_value += "\n".join(f"• {example}" for example in section.examples[:3])
                
                embed.add_field(
                    name=f"📋 {section.title}",
                    value=field_value[:1024],  # Discord field limit
                    inline=False
                )
            
            # Add footer with additional help
            embed.set_footer(text="Use /help for complete documentation • Ask specific questions for more targeted help")
            
            return embed
            
        except Exception as e:
            logger.error(f"Error providing contextual help: {e}", exc_info=True)
            return self._create_error_help_embed()
    
    def _find_relevant_help_sections(self, query: str) -> List[HelpSection]:
        """
        Find help sections relevant to the query.
        
        Args:
            query: User query or message content
            
        Returns:
            List of relevant help sections, sorted by relevance
        """
        query_lower = query.lower()
        scored_sections = []
        
        for section in self.help_sections:
            score = 0
            
            # Check keywords
            for keyword in section.keywords:
                if keyword in query_lower:
                    score += 2
            
            # Check title
            if any(word in query_lower for word in section.title.lower().split()):
                score += 3
            
            # Check content
            content_words = section.content.lower().split()
            query_words = query_lower.split()
            common_words = set(content_words) & set(query_words)
            score += len(common_words) * 0.5
            
            # Check examples
            if section.examples:
                for example in section.examples:
                    if any(word in query_lower for word in example.lower().split()):
                        score += 1
            
            if score > 0:
                scored_sections.append((section, score))
        
        # Sort by score and return sections
        scored_sections.sort(key=lambda x: x[1], reverse=True)
        return [section for section, score in scored_sections]
    
    async def suggest_similar_commands(
        self,
        message: discord.Message,
        invalid_command: str
    ) -> Optional[discord.Embed]:
        """
        Suggest similar commands for typos or unclear requests.
        
        Implements requirement 5.3: Implement command suggestion algorithm
        for typos/unclear requests.
        
        Args:
            message: Discord message with invalid command
            invalid_command: The invalid command text
            
        Returns:
            Embed with command suggestions or None if no suggestions
        """
        try:
            suggestions = self._generate_command_suggestions(invalid_command)
            
            if not suggestions:
                return None
            
            embed = self.ux_service.create_status_embed(
                title="🔍 Did you mean?",
                description=f"I didn't recognize `{invalid_command}`, but here are some suggestions:",
                status=Status.INFO
            )
            
            # Add top suggestions
            for i, suggestion in enumerate(suggestions[:5], 1):
                confidence_bar = "█" * int(suggestion.confidence * 10) + "░" * (10 - int(suggestion.confidence * 10))
                
                embed.add_field(
                    name=f"{i}. {suggestion.suggestion}",
                    value=f"**Confidence:** {confidence_bar} ({suggestion.confidence:.0%})\n"
                           f"**Reason:** {suggestion.reason}",
                    inline=False
                )
            
            # Add help footer
            embed.set_footer(text="Use /help to see all available commands")
            
            return embed
            
        except Exception as e:
            logger.error(f"Error generating command suggestions: {e}", exc_info=True)
            return None
    
    def _generate_command_suggestions(self, invalid_command: str) -> List[CommandSuggestion]:
        """
        Generate command suggestions for an invalid command.
        
        Args:
            invalid_command: The invalid command text
            
        Returns:
            List of command suggestions sorted by confidence
        """
        suggestions = []
        invalid_lower = invalid_command.lower().strip()
        
        # Check direct variations first
        if invalid_lower in self.command_variations:
            correct_command = self.command_variations[invalid_lower]
            suggestions.append(CommandSuggestion(
                original=invalid_command,
                suggestion=correct_command,
                confidence=0.95,
                reason="Common typo correction"
            ))
        
        # Check similarity with known commands
        for command, description in self.known_commands.items():
            # Calculate similarity using different methods
            
            # 1. Sequence matching
            seq_ratio = difflib.SequenceMatcher(None, invalid_lower, command.lower()).ratio()
            
            # 2. Partial matching
            partial_ratio = 0
            if invalid_lower in command.lower() or command.lower() in invalid_lower:
                partial_ratio = 0.8
            
            # 3. Word matching
            invalid_words = set(invalid_lower.split())
            command_words = set(command.lower().split())
            word_ratio = len(invalid_words & command_words) / max(len(invalid_words), len(command_words))
            
            # Calculate overall confidence
            confidence = max(seq_ratio, partial_ratio, word_ratio * 0.7)
            
            # Only suggest if confidence is above threshold
            if confidence >= self.config.command_suggestion_threshold:
                reason = self._get_suggestion_reason(seq_ratio, partial_ratio, word_ratio)
                
                suggestions.append(CommandSuggestion(
                    original=invalid_command,
                    suggestion=command,
                    confidence=confidence,
                    reason=reason
                ))
        
        # Sort by confidence
        suggestions.sort(key=lambda x: x.confidence, reverse=True)
        
        return suggestions
    
    def _get_suggestion_reason(self, seq_ratio: float, partial_ratio: float, word_ratio: float) -> str:
        """Get reason for command suggestion based on matching ratios."""
        if partial_ratio > 0.5:
            return "Contains similar text"
        elif word_ratio > 0.5:
            return "Similar keywords"
        elif seq_ratio > 0.7:
            return "Similar spelling"
        else:
            return "Possible match"
    
    async def provide_feature_discovery(self, message: discord.Message) -> discord.Embed:
        """
        Provide feature discovery and usage guidance.
        
        Implements requirements 5.4, 5.5: Add feature discovery and usage guidance.
        
        Args:
            message: Discord message requesting feature discovery
            
        Returns:
            Feature discovery embed
        """
        try:
            embed = self.ux_service.create_status_embed(
                title="✨ Feature Discovery",
                description="Discover what I can do for you!",
                status=Status.INFO
            )
            
            # Core features
            embed.add_field(
                name="🤖 AI Conversations",
                value="• Context-aware responses\n"
                      "• Multi-turn conversations\n"
                      "• Reply chain analysis\n"
                      "• File and document analysis",
                inline=True
            )
            
            embed.add_field(
                name="🖼️ Image Editing",
                value="• Object removal\n"
                      "• Background replacement\n"
                      "• Artistic style transfer\n"
                      "• Color adjustments",
                inline=True
            )
            
            embed.add_field(
                name="⚡ Quick Commands",
                value="• `/help` - Detailed help\n"
                      "• `/model` - Switch AI models\n"
                      "• `/stats` - Usage statistics\n"
                      "• `/config` - View settings",
                inline=True
            )
            
            # Advanced features
            embed.add_field(
                name="🔧 Advanced Features",
                value="• **PDF Processing:** Upload PDFs for analysis\n"
                      "• **Multi-Image:** Compare multiple images\n"
                      "• **Context Memory:** Remembers conversation history\n"
                      "• **Smart Splitting:** Long responses split intelligently",
                inline=False
            )
            
            # Usage tips
            embed.add_field(
                name="💡 Pro Tips",
                value="• Use natural language - no special syntax needed\n"
                      "• Reply to my messages for better context\n"
                      "• Be specific with image editing requests\n"
                      "• Try different AI models for varied responses",
                inline=False
            )
            
            # Getting started
            embed.add_field(
                name="🚀 Getting Started",
                value="1. **Mention me** in any message: `@bot your question`\n"
                      "2. **Upload images** with editing instructions\n"
                      "3. **Use slash commands** for quick actions\n"
                      "4. **Ask for help** anytime with specific questions",
                inline=False
            )
            
            embed.set_footer(text="Try mentioning me with a question or upload an image to get started!")
            
            return embed
            
        except Exception as e:
            logger.error(f"Error providing feature discovery: {e}", exc_info=True)
            return self._create_error_help_embed()
    
    def _create_general_help_embed(self) -> discord.Embed:
        """Create a general help embed when no specific context is found."""
        embed = self.ux_service.create_status_embed(
            title="🤖 General Help",
            description="Here's how to use this bot:",
            status=Status.INFO
        )
        
        embed.add_field(
            name="💬 Basic Usage",
            value="Simply mention me (@bot) in any message to get an AI response!",
            inline=False
        )
        
        embed.add_field(
            name="🖼️ Image Editing",
            value="Upload an image and mention me with instructions like:\n"
                  "• `@bot remove the background`\n"
                  "• `@bot make it artistic`",
            inline=False
        )
        
        embed.add_field(
            name="⚡ Quick Commands",
            value="`/help` - Detailed help\n"
                  "`/ping` - Check status\n"
                  "`/model` - Switch AI models",
            inline=False
        )
        
        embed.set_footer(text="Ask me specific questions for more targeted help!")
        
        return embed
    
    def _create_error_help_embed(self) -> discord.Embed:
        """Create an error help embed when help generation fails."""
        return self.ux_service.create_status_embed(
            title="❌ Help Error",
            description="I'm having trouble generating help right now. "
                       "Try using `/help` for basic information or ask me a specific question!",
            status=Status.ERROR
        )
    
    async def handle_unsupported_operation(
        self,
        message: discord.Message,
        operation: str
    ) -> discord.Embed:
        """
        Handle requests for unsupported operations with alternatives.
        
        Implements requirement 5.5: Where users request unsupported operations,
        explain limitations and suggest alternatives.
        
        Args:
            message: Discord message with unsupported request
            operation: The unsupported operation requested
            
        Returns:
            Embed explaining limitations and alternatives
        """
        try:
            embed = self.ux_service.create_status_embed(
                title="🚫 Unsupported Operation",
                description=f"I can't perform `{operation}` right now, but here are some alternatives:",
                status=Status.WARNING
            )
            
            # Provide alternatives based on operation type
            alternatives = self._get_operation_alternatives(operation.lower())
            
            if alternatives:
                embed.add_field(
                    name="💡 Try These Instead",
                    value="\n".join(f"• {alt}" for alt in alternatives),
                    inline=False
                )
            
            # General suggestions
            embed.add_field(
                name="🔧 What I Can Do",
                value="• Answer questions and have conversations\n"
                      "• Edit images (remove objects, change backgrounds, etc.)\n"
                      "• Analyze uploaded files and documents\n"
                      "• Provide detailed explanations and analysis",
                inline=False
            )
            
            embed.add_field(
                name="📝 Need Help?",
                value="Use `/help` for full capabilities or ask me:\n"
                      "`@bot what can you do?`",
                inline=False
            )
            
            return embed
            
        except Exception as e:
            logger.error(f"Error handling unsupported operation: {e}", exc_info=True)
            return self._create_error_help_embed()
    
    def _get_operation_alternatives(self, operation: str) -> List[str]:
        """Get alternative suggestions for unsupported operations."""
        alternatives_map = {
            "video": [
                "Upload individual frames as images for analysis",
                "Describe the video content and I can help analyze it",
                "Extract screenshots and I can edit those images"
            ],
            "audio": [
                "Describe the audio content for analysis",
                "Upload a transcript and I can help with text analysis",
                "Ask questions about audio processing concepts"
            ],
            "real-time": [
                "Upload images for editing instead",
                "Ask questions about the topic you're interested in",
                "Use static content for analysis"
            ],
            "download": [
                "Upload files directly to Discord",
                "Share content as text or images",
                "Describe what you need help with"
            ],
            "external": [
                "Upload content directly to our conversation",
                "Describe what you're trying to accomplish",
                "Ask for guidance on the topic"
            ]
        }
        
        # Find matching alternatives
        for key, alts in alternatives_map.items():
            if key in operation:
                return alts
        
        # Default alternatives
        return [
            "Try rephrasing your request",
            "Upload relevant files or images",
            "Ask me what I can help with specifically"
        ]