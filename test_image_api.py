"""
Test script to verify Gemini 2.5 Flash Image API is working correctly.
"""
import asyncio
import os
from dotenv import load_dotenv
from io import BytesIO
import PIL.Image

# Load environment variables
load_dotenv()

try:
    from google import genai
    from google.genai import types
    SDK_AVAILABLE = True
except ImportError:
    print("❌ google.genai SDK not available!")
    SDK_AVAILABLE = False
    exit(1)

async def test_image_generation():
    """Test basic image generation."""
    api_key = os.getenv('GEMINI_API_KEY')
    if not api_key:
        print("❌ GEMINI_API_KEY not found in environment")
        return False
    
    print(f"✅ API Key found: {api_key[:10]}...{api_key[-4:]}")
    
    try:
        # Initialize client
        client = genai.Client(api_key=api_key)
        print("✅ Client initialized")
        
        # Test simple generation
        prompt = "Generate an image: A red apple on a white background"
        print(f"\n🎨 Testing generation with prompt: '{prompt}'")
        
        response = await asyncio.to_thread(
            client.models.generate_content,
            model="gemini-2.5-flash-image",
            contents=[prompt],
            config=types.GenerateContentConfig(
                response_modalities=['Image']
            )
        )
        
        print(f"✅ Response received")
        print(f"   - Has candidates: {hasattr(response, 'candidates')}")
        
        if hasattr(response, 'candidates') and response.candidates:
            print(f"   - Number of candidates: {len(response.candidates)}")
            
            for idx, candidate in enumerate(response.candidates):
                if hasattr(candidate, 'content') and candidate.content:
                    if hasattr(candidate.content, 'parts'):
                        print(f"   - Number of parts: {len(candidate.content.parts)}")
                        
                        for part_idx, part in enumerate(candidate.content.parts):
                            # Check for inline_data
                            if hasattr(part, 'inline_data') and part.inline_data is not None:
                                print(f"   ✅ Found inline_data in part {part_idx}")
                                image_bytes = part.inline_data.data
                                print(f"   - Image size: {len(image_bytes)} bytes")
                                
                                # Try to open as PIL Image
                                try:
                                    image = PIL.Image.open(BytesIO(image_bytes))
                                    print(f"   ✅ Successfully loaded as PIL Image: {image.size}, {image.format}")
                                    image.save("test_generated.png")
                                    print(f"   ✅ Saved as test_generated.png")
                                    return True
                                except Exception as e:
                                    print(f"   ❌ Failed to load as PIL Image: {e}")
                            
                            # Check for text
                            if hasattr(part, 'text'):
                                print(f"   ⚠️  Part {part_idx} contains text: {part.text[:100]}")
        
        print("❌ No image data found in response")
        return False
        
    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()
        return False

async def test_image_editing():
    """Test image editing with an uploaded image."""
    api_key = os.getenv('GEMINI_API_KEY')
    if not api_key:
        print("❌ GEMINI_API_KEY not found in environment")
        return False
    
    print(f"\n🎨 Testing image editing...")
    
    # Create a simple test image
    test_image = PIL.Image.new('RGB', (512, 512), color='white')
    img_bytes = BytesIO()
    test_image.save(img_bytes, format='PNG')
    img_bytes.seek(0)
    
    try:
        client = genai.Client(api_key=api_key)
        
        # Load image for editing
        image = PIL.Image.open(img_bytes)
        prompt = "Add a red apple in the center\n\nReturn the edited image as the output."
        
        print(f"   - Prompt: '{prompt}'")
        print(f"   - Image: {image.size}, {image.format}")
        
        response = await asyncio.to_thread(
            client.models.generate_content,
            model="gemini-2.5-flash-image",
            contents=[image, prompt],
            config=types.GenerateContentConfig(
                response_modalities=['Image']
            )
        )
        
        print(f"✅ Response received")
        
        if hasattr(response, 'candidates') and response.candidates:
            for candidate in response.candidates:
                if hasattr(candidate, 'content') and candidate.content:
                    if hasattr(candidate.content, 'parts'):
                        for part_idx, part in enumerate(candidate.content.parts):
                            if hasattr(part, 'inline_data') and part.inline_data is not None:
                                print(f"   ✅ Found inline_data in part {part_idx}")
                                image_bytes = part.inline_data.data
                                
                                edited_image = PIL.Image.open(BytesIO(image_bytes))
                                print(f"   ✅ Successfully loaded edited image: {edited_image.size}")
                                edited_image.save("test_edited.png")
                                print(f"   ✅ Saved as test_edited.png")
                                return True
        
        print("❌ No image data found in response")
        return False
        
    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    print("=" * 60)
    print("Gemini 2.5 Flash Image API Test")
    print("=" * 60)
    
    # Test generation
    result1 = asyncio.run(test_image_generation())
    
    # Test editing
    result2 = asyncio.run(test_image_editing())
    
    print("\n" + "=" * 60)
    print("Test Results:")
    print(f"  Generation: {'✅ PASS' if result1 else '❌ FAIL'}")
    print(f"  Editing: {'✅ PASS' if result2 else '❌ FAIL'}")
    print("=" * 60)
