from fastapi import FastAPI, Request
import os
from telethon import TelegramClient
import asyncio
app = FastAPI()

@app.get("/")
api_id = os.getenv(api_id)
api_hash = os.getenv("api_hash")
client = TelegramClient("my_session",api_id,api_hash)
        
source_channels = []
source_channels = os.getenv("source_channels")
target_channels = []
target_channels = os.getenv("target_channels")

async def main():
   while True:
    for src in source_channels:
       new_messages = await client.get_messages(src,limit=1)
       for trc in target_channels:
          existed_messages = await client.get_messages(trc,limit=1)
          if new_messages and existed_messages:
             if new_messages[0].message != existed_messages[0].message :
                await client.forward_messages(trc,new_messages)
                print(f"forwarded from {src}-->{trc}}")
                else:
                   print(f"already existed in {trc}"")
          else:
             print(f"no messages in {src} or {trc})
    await asyncio.sleep(300)
with client:
   client.loop.run_unti_complete(main())
   print("complete...")
   return "surya it's completed.."
    
