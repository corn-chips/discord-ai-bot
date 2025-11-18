
from google import genai
import asyncio
import os

async def check_async():
    try:
        client = genai.Client(api_key="test")
        if hasattr(client, 'aio'):
            print("Has client.aio")
        else:
            print("No client.aio")
            
        # Check dir of client
        # print(dir(client))
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    asyncio.run(check_async())
