import os
import asyncio
from telethon import TelegramClient
from fastapi import FastAPI
from telethon.sessions import StringSession
from supabase import create_client,Client


# Load Telegram credentials from environment
api_id = int(os.getenv("API_ID"))        # must be int
api_hash = os.getenv("API_HASH")
#session_string = os.getenv("SESSION_STRING")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
supabase: Client = create_client(SUPABASE_URL,SUPABASE_KEY)

#client = TelegramClient(StringSession(session_string), api_id, api_hash)

# Source and Target Channels from env (comma-separated)

# Create FastAPI app
app = FastAPI()

@app.get("/")
def home():
    return {"status": "running", "message": "Telegram forwarder active!"}


# Background task to forward messages
async def forward_messages(session_string):
    
    source = supabase.table("telegram_sessions").select("source_channels").execute() #source channel in telegram_sessions
    target = supabase.table("telegram_sessions").select("target_channels").execute() #target channel in telegram sessions
    sou = source.data[0]["source_channels"]
    tar = target.data[0]["target_channels"]
    source_channels = sou.split(",")
    target_channels = tar.split(",")

    client = TelegramClient(StringSession(session_string), api_id, api_hash)
    await client.start()
 
    #while True:
        

        for src in source_channels:
            if not src.strip():
                continue

            try:
                new_messages = await client.get_messages(src, limit=2)
                if not new_messages:
                    continue
                new_msg = new_messages[0]  # latest source message
            except Exception as e:
                print(f"Error fetching from {src}: {e}")
                continue

            for tgt in target_channels:
                if not tgt.strip():
                    continue

                try:
                    existed_messages = await client.get_messages(tgt, limit=50)

                    if existed_messages:
                        # Check if new_msg.text exists in any of the last 10 target messages
                        exists = any(m.message == new_msg.message for m in existed_messages)

                        if not exists:
                            await client.forward_messages(tgt, new_msg)
                            print(f"✅ Forwarded from {src} -> {tgt}")
                        else:
                            print(f"⚠️ Message already exists in {tgt}!")

                    else:
                        # target empty, safe to forward
                        await client.forward_messages(tgt, new_msg)
                        print(f"✅ Forwarded (target empty) {src} -> {tgt}")

                except Exception as e:
                    print(f"Error forwarding to {tgt}: {e}")

        await asyncio.sleep(60)  # check every 5 mins
        
        
async def main():
    while True:
        data = supabase.table("telegram_sessions").select("Session_string").execute()
        sessions = data.data or []
        tasks = [forward_messages(user["Session_string"]) for user in sessions]
        await asyncio.gather(*tasks)



# Run client + background task with FastAPI

@app.on_event("startup")
async def startup_event():
    #await client.start()
    asyncio.create_task(main())


