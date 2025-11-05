"""
Simple test script to demonstrate the new Gemini-based image generation intent detection.
This replaces the old keyword matching system.
"""

import asyncio
import google.generativeai as genai
from src.config import BotConfig

async def check_image_generation_intent(message_content: str, api_key: str) -> bool:
    """
    Use Gemini Flash Lite to determine if the user is requesting image generation.
    
    Args:
        message_content: The message content to analyze
        api_key: Gemini API key
        
    Returns:
        True if the user wants image generation, False otherwise
    """
    try:
        # Configure API
        genai.configure(api_key=api_key)
        
        # Create a simple prompt for Gemini to classify the intent
        classification_prompt = f"""Analyze this user message and determine if they are requesting image generation or creation.

User message: "{message_content}"

Respond with ONLY one word:
- "yes" if the user is asking to generate, create, make, draw, or produce an image/picture
- "no" if the user is NOT asking for image generation

Your response:"""

        # Create a simple model instance for classification
        model = genai.GenerativeModel(
            model_name="gemini-2.0-flash-lite",
            generation_config={
                "temperature": 0.1,  # Low temperature for consistent classification
                "max_output_tokens": 10,  # Only need one word
            }
        )
        
        # Generate response
        response = await asyncio.to_thread(
            model.generate_content,
            classification_prompt
        )
        
        # Extract and normalize the response
        result = response.text.strip().lower()
        
        # Check if response is "yes"
        is_generation = result == "yes"
        
        print(f"Message: '{message_content}'")
        print(f"Intent: {'IMAGE GENERATION' if is_generation else 'NOT IMAGE GENERATION'}")
        print(f"Model response: '{result}'")
        print("-" * 80)
        
        return is_generation
        
    except Exception as e:
        print(f"Error checking image generation intent: {e}")
        return False


async def main():
    """Test various messages to see how the intent detection works."""
    
    # Load config to get API key
    import os
    api_key = os.getenv('GEMINI_API_KEY')
    if not api_key:
        print("Error: GEMINI_API_KEY environment variable not set")
        return
    
    # Test cases
    test_messages = [
        # Image generation requests (should return True)
        "generate an image of a cat",
        "create a picture of a sunset",
        "make me a drawing of a robot",
        "draw a picture of mountains",
        "can you generate an image of a cyberpunk city?",
        "produce an illustration of a dragon",
        
        # NOT image generation requests (should return False)
        "edit this image to remove the background",
        "can you help me with my homework?",
        "what's the weather like today?",
        "tell me about cats",
        "remove the person from this photo",
        "make this image brighter",
    ]
    
    print("=" * 80)
    print("TESTING GEMINI-BASED IMAGE GENERATION INTENT DETECTION")
    print("=" * 80)
    print()
    
    for message in test_messages:
        await check_image_generation_intent(message, api_key)
    
    print("\n✅ Testing complete!")
    print("\nThe old keyword matching system has been replaced with AI-based intent detection.")
    print("This provides more accurate and context-aware image generation detection.")


if __name__ == "__main__":
    asyncio.run(main())
