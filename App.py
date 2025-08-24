import os
import asyncio
from telethon import TelegramClient
from fastapi import FastAPI
from telethon.sessions import StringSession

# Load Telegram credentials from environment
api_id = int(os.getenv("API_ID"))        # must be int
api_hash = os.getenv("API_HASH")
session_string = os.getenv("SESSION_STRING")
# Create Telegram client
client = TelegramClient(StringSession(session_string), api_id, api_hash)

# Source and Target Channels from env (comma-separated)
source_channels = os.getenv("SOURCE_CHANNELS", "").split(",")
target_channels = os.getenv("TARGET_CHANNELS", "").split(",")

# Create FastAPI app
app = FastAPI()

@app.get("/")
def home():
    return {"status": "running", "message": "Telegram forwarder active!"}


# Background task to forward messages
async def forward_messages():
    while True:
        for src in source_channels:
            if not src.strip():
                continue

            try:
                new_messages = await client.get_messages(src, limit=1)
            except Exception as e:
                print(f"Error fetching from {src}: {e}")
                continue

            for tgt in target_channels:
                if not tgt.strip():
                    continue

                try:
                    existed_messages = await client.get_messages(tgt, limit=100)

                    if new_messages and existed_messages:
                        if new_messages[0].message != existed_messages[0].message:
                            await client.forward_messages(tgt, new_messages)
                            print(f"✅ Forwarded from {src} -> {tgt}")
                            
                        else:
                            print(f"⚠️ Already existed in {tgt}!")
                            
                    else:
                        print(f"ℹ️ No messages in {src} or {tgt}") 

                except Exception as e:
                    print(f"Error forwarding to {tgt}: {e}")

        await asyncio.sleep(300)  # check every 5 mins


# Run client + background task with FastAPI
@app.on_event("startup")
async def startup_event():
    await client.start()
    asyncio.create_task(forward_messages())
