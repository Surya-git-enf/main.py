import os
import asyncio
from telethon import TelegramClient
from fastapi import FastAPI
from telethon.sessions import StringSession
from supabase import create_client,Client
from pydantic import BaseModel
from typing import Union

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


#source_channels = [ch.strip() for s in sou for ch in s.split(",")]
#target_channels = [ch.strip() for t in tar for ch in t.split(",")]
        

@app.get("/")
def home():
    return {"status": "running", "message": "Telegram forwarder active!"}


# Background task to forward messages
async def forward_messages(session_string):
        client = TelegramClient(StringSession(session_string), api_id, api_hash)
        await client.start()
        #while True:
                
        source = supabase.table("telegram_sessions").select("source_channels").execute() #source channel in telegram_sessions
        target = supabase.table("telegram_sessions").select("target_channels").execute() #target channel in telegram sessions
        sou = source.data[0]["source_channels"] or [] # data in source channels
        tar = target.data[0]["target_channels"] or []  # data in target channels    
        for s, t in zip(sou, tar):
                
                source_channels = [int(ch) if ch.strip("-").isdigit()  else ch.strip() for ch in s.split(",") if ch.strip()]
                target_channels = [int(ch) if ch.strip("-").isdigit()  else ch.strip() for ch in t.split(",") if ch.strip()]
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
        await asyncio.sleep(60)

class channels(BaseModel):
    user_id:str
    source: Union[int,str]
    target: Union[int,str]
        
@app.put("/add_channel")
async def add_channel(add:channels):
    #user = supabase.table("telegram_sessions").select("user_id").eq(execute()
    #user_id = user.data[0]["user_id"]                                                 
    source_response = supabase.table("telegram_sessions").select("source_channels").eq("user_id",add.user_id).execute()
    target_resource = supabase.table("telegram_sessions").select("target_channels").eq("user_id",add.user_id).execute()
    sources = source_response.data[0]["source_channels"] or []
    targets = target_resource.data[0]["target_channels"] or []    
    sources.append(str(add.source))
    targets.append(str(add.target))
    try:
        source_result = supabase.table("telegram_sessions").update({"source_channels":sources}).eq("user_id",add.user_id).execute()
        target_result = supabase.table("telegram_sessions").update({"target_channels":targets}).eq("user_id",add.user_id).execute()
        return {"message":"updated successfully"}
    except Exception as e:
        return {"error":str(e)}
    

class edit_ch(BaseModel):
        user_id:str
        source_value:Union[int,str]
        target_value:Union[int,str]
        index:int
@app.put("/edit_channel")
async def edit_channel(edit:edit_ch):                                            
        source_response = supabase.table("telegram_sessions").select("source_channels").eq("user_id",edit.user_id).execute()
        target_resource = supabase.table("telegram_sessions").select("target_channels").eq("user_id",edit.user_id).execute()
        sources = source_response.data[0]["source_channels"] or []
        targets = target_resource.data[0]["target_channels"] or []
        sources[edit.index] = edit.source_value
        targets[edit.index] = edit.target_value
        try:
            edit_result = supabase.table("telegram_sessions").update({"source_channels":sources}).eq("user_id",edit.user_id).execute() #when source edit value is stored in edit_resulr
            edit_results = supabase.table("telegram_sessions").update({"target_channels":targets}).eq("user_id",edit.user_id).execute() #this is for target edits
            return{"message":"edited successfully"}
        except Exception as e:
                return{"error":str(e)}
                

                
@app.delete("/del_channel")
async def delete_channel(id:int,user_id:str):                                             
        source_response = supabase.table("telegram_sessions").select("source_channels").eq("user_id",user_id).execute()
        target_resource = supabase.table("telegram_sessions").select("target_channels").eq("user_id",user_id).execute()
        sources = source_response.data[0]["source_channels"] or []
        targets = target_resource.data[0]["target_channels"] or []
        sources.remove(sources[id])
        targets.remove(targets[id])
        try:
               del_source = supabase.table("telegram_sessions").update({"source_channels":sources}).eq("user_id",user_id).execute() # delete channel from source_channels 
               del_target = supabase.table("telegram_sessions").update({"target_channels":targets}).eq("user_id",user_id).execute() # delete channel from target also 
               return {"message":"deleted successfuly"}
        except Exception as e:
                
                return {"error":str(e)}

# Run client + background task with FastAPI

@app.on_event("startup")
async def startup_event():
    #await client.start()
    asyncio.create_task(main())


