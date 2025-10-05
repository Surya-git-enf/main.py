# App.py
import os
import asyncio
import logging
from typing import Union, List, Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from telethon import TelegramClient
from telethon.sessions import StringSession
from supabase import create_client, Client

# -------------------------
# Basic logging
# -------------------------
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

# -------------------------
# Environment / config checks
# -------------------------
try:
    API_ID = int(os.getenv("API_ID") or 0)
except ValueError:
    API_ID = 0
API_HASH = os.getenv("API_HASH")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

if not (API_ID and API_HASH):
    logger.error("Missing Telegram API credentials: API_ID and/or API_HASH.")
    # Do not raise here — FastAPI can still start but forwarding will log issues.
if not (SUPABASE_URL and SUPABASE_KEY):
    logger.error("Missing Supabase credentials: SUPABASE_URL and/or SUPABASE_KEY.")

# Initialize Supabase client (will raise if credentials are invalid)
supabase: Optional[Client] = None
if SUPABASE_URL and SUPABASE_KEY:
    supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

# -------------------------
# FastAPI app + CORS
# -------------------------
app = FastAPI(title="Telegram Forwarder")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # change to specific origins in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# -------------------------
# Helpers
# -------------------------
def normalize_channel_item(item):
    """Return int if numeric else stripped string. Handles leading '-' for channel IDs (supergroups)."""
    if item is None:
        return None
    s = str(item).strip()
    if not s:
        return None
    # allow negative numeric ids for channels/supergroups
    try:
        if s.lstrip("-").isdigit():
            return int(s)
    except Exception:
        pass
    return s

async def safe_get_session_rows():
    """Return all rows from telegram_sessions table (or empty list)."""
    if not supabase:
        return []
    try:
        res = supabase.table("telegram_sessions").select("*").execute()
        return res.data or []
    except Exception as e:
        logger.exception("Supabase read failed: %s", e)
        return []

# -------------------------
# Core forwarding logic
# -------------------------
async def forward_messages(session_string: str):
    """
    Background task per Telegram session string.
    Behavior: pairwise forwarding - source[i] -> target[i]
    """
    if not (API_ID and API_HASH):
        logger.error("Telegram API credentials missing. Forwarder won't start.")
        return

    client = TelegramClient(StringSession(session_string), API_ID, API_HASH)

    try:
        await client.start()
    except Exception as e:
        logger.exception("Failed to start Telegram client for session: %s", e)
        return

    logger.info("Started forwarder for session (truncated): %s...", session_string[:20])

    while True:
        try:
            # Fetch the row corresponding to this session string
            if not supabase:
                logger.error("Supabase client not initialized.")
                await asyncio.sleep(60)
                continue

            db_res = supabase.table("telegram_sessions").select("source_channels", "target_channels", "user_id", "automation_state").eq("Session_string", session_string).execute()
            rows = db_res.data or []
            if not rows:
                logger.warning("No supabase row found for this session string — sleeping.")
                await asyncio.sleep(60)
                continue

            row = rows[0]

            # Optional: check automation state (if you use a column 'automation_state')
            if row.get("automation_state") in ("off", "false", False, 0):
                logger.info("Automation disabled for user %s. Sleeping...", row.get("user_id"))
                await asyncio.sleep(60)
                continue

            # Expecting source_channels and target_channels to be list-like in DB
            sou = row.get("source_channels") or []
            tar = row.get("target_channels") or []

            # Ensure they are lists
            if not isinstance(sou, list):
                sou = [sou] if sou else []
            if not isinstance(tar, list):
                tar = [tar] if tar else []

            # iterate pairwise up to min length
            pair_count = min(len(sou), len(tar))
            if pair_count == 0:
                logger.debug("No source-target pairs found for user %s", row.get("user_id"))
                await asyncio.sleep(60)
                continue

            for idx in range(pair_count):
                raw_src = sou[idx]
                raw_tgt = tar[idx]
                src = normalize_channel_item(raw_src)
                tgt = normalize_channel_item(raw_tgt)

                if src is None or tgt is None:
                    logger.debug("Skipping empty src/tgt at index %d: %r -> %r", idx, raw_src, raw_tgt)
                    continue

                # Fetch the latest message from source
                try:
                    new_messages = await client.get_messages(src, limit=1)
                except Exception as e:
                    logger.warning("Failed to get messages from %s: %s", src, e)
                    continue

                if not new_messages:
                    logger.debug("No messages found in source %s", src)
                    continue

                new_msg = new_messages[0]

                # Fetch recent messages in target to avoid duplicates
                try:
                    existed = await client.get_messages(tgt, limit=30)
                except Exception as e:
                    logger.warning("Failed to get messages from target %s: %s", tgt, e)
                    existed = []

                # Dup detection: compare message text if available, else compare ids/media signatures
                def msg_text(m):
                    return getattr(m, "message", None)

                exists = False
                new_text = msg_text(new_msg)
                if new_text is not None:
                    exists = any(msg_text(m) == new_text for m in existed)
                else:
                    # Fallback: check if same media/file id present
                    # Telethon Message may have .media attribute; compare by repr or by id
                    exists = any(
                        getattr(m, "media", None) == getattr(new_msg, "media", None) and m.sender_id == new_msg.sender_id
                        for m in existed
                    )

                if exists:
                    logger.info("Skipping forward - already exists in target %s (pair index %d).", tgt, idx)
                    continue

                # Forward
                try:
                    await client.forward_messages(tgt, new_msg)
                    logger.info("✅ Forwarded from %s -> %s (pair index %d).", src, tgt, idx)
                except Exception as e:
                    logger.exception("Error forwarding %s -> %s: %s", src, tgt, e)

            # Sleep before next poll
            await asyncio.sleep(60)
        except Exception as e:
            logger.exception("Unexpected loop error in forward_messages: %s", e)
            await asyncio.sleep(60)

# -------------------------
# Startup: create tasks for every session
# -------------------------
async def main_manager():
    """
    Create one forward_messages task per session row in supabase.
    This function keeps running so tasks remain alive.
    """
    logger.info("Main manager starting - scanning sessions...")
    started_sessions = set()

    while True:
        try:
            rows = await asyncio.to_thread(lambda: supabase.table("telegram_sessions").select("Session_string").execute().data if supabase else [])
            rows = rows or []
            for r in rows:
                sess = r.get("Session_string")
                if not sess:
                    continue
                if sess in started_sessions:
                    continue
                # launch a background forwarder for this session
                asyncio.create_task(forward_messages(sess))
                started_sessions.add(sess)
                logger.info("Launched forwarder task for a session (truncated).")
        except Exception as e:
            logger.exception("Error while launching session tasks: %s", e)

        # Re-scan every 60 seconds to pick up new sessions
        await asyncio.sleep(60)

# -------------------------
# FastAPI endpoints (kept / tidied)
# -------------------------
class ChannelsIn(BaseModel):
    user_id: str
    source: Union[int, str]
    target: Union[int, str]

class EditCh(BaseModel):
    user_id: str
    source_value: Union[int, str]
    target_value: Union[int, str]
    index: int

class RecentRpl(BaseModel):
    user_id: str

class Draft(BaseModel):
    user_id: str

class UserEmail(BaseModel):
    email: str

class Toggle(BaseModel):
    user: str
    pos: str

@app.get("/")
def home():
    return {"status": "running", "message": "Telegram forwarder active!"}

@app.put("/add_channel")
async def add_channel(add: ChannelsIn):
    if not supabase:
        raise HTTPException(status_code=500, detail="Supabase not configured.")
    resp = supabase.table("telegram_sessions").select("source_channels", "target_channels").eq("user_id", add.user_id).execute()
    rows = resp.data or []
    if not rows:
        raise HTTPException(status_code=404, detail="User not found.")

    sources = rows[0].get("source_channels") or []
    targets = rows[0].get("target_channels") or []
    if not isinstance(sources, list):
        sources = [sources] if sources else []
    if not isinstance(targets, list):
        targets = [targets] if targets else []

    sources.append(str(add.source))
    targets.append(str(add.target))

    try:
        supabase.table("telegram_sessions").update({"source_channels": sources}).eq("user_id", add.user_id).execute()
        supabase.table("telegram_sessions").update({"target_channels": targets}).eq("user_id", add.user_id).execute()
        return {"message": "created successfully"}
    except Exception as e:
        logger.exception("Add channel error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))

@app.put("/edit_channel")
async def edit_channel(edit: EditCh):
    if not supabase:
        raise HTTPException(status_code=500, detail="Supabase not configured.")
    resp = supabase.table("telegram_sessions").select("source_channels", "target_channels").eq("user_id", edit.user_id).execute()
    rows = resp.data or []
    if not rows:
        raise HTTPException(status_code=404, detail="User not found.")

    sources = rows[0].get("source_channels") or []
    targets = rows[0].get("target_channels") or []
    if not isinstance(sources, list) or not isinstance(targets, list):
        raise HTTPException(status_code=400, detail="Channels data is not a list.")

    try:
        sources[edit.index] = str(edit.source_value)
        targets[edit.index] = str(edit.target_value)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Index error: {e}")

    try:
        supabase.table("telegram_sessions").update({"source_channels": sources}).eq("user_id", edit.user_id).execute()
        supabase.table("telegram_sessions").update({"target_channels": targets}).eq("user_id", edit.user_id).execute()
        return {"message": "edited successfully"}
    except Exception as e:
        logger.exception("Edit channel error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))

@app.delete("/del_channel")
async def delete_channel(id: int, user_id: str):
    if not supabase:
        raise HTTPException(status_code=500, detail="Supabase not configured.")
    resp = supabase.table("telegram_sessions").select("source_channels", "target_channels").eq("user_id", user_id).execute()
    rows = resp.data or []
    if not rows:
        raise HTTPException(status_code=404, detail="User not found.")

    sources = rows[0].get("source_channels") or []
    targets = rows[0].get("target_channels") or []
    if id < 0 or id >= min(len(sources), len(targets)):
        raise HTTPException(status_code=400, detail="Invalid index.")

    try:
        # remove pair-wise by index
        sources.pop(id)
        targets.pop(id)
        supabase.table("telegram_sessions").update({"source_channels": sources}).eq("user_id", user_id).execute()
        supabase.table("telegram_sessions").update({"target_channels": targets}).eq("user_id", user_id).execute()
        return {"message": "deleted successfully"}
    except Exception as e:
        logger.exception("Delete error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/recent_replies")
def replies(rpl: RecentRpl):
    if not supabase:
        raise HTTPException(status_code=500, detail="Supabase not configured.")
    replies = supabase.table("telegram_sessions").select("recent_replies").eq("user_id", rpl.user_id).execute()
    return {"recent_replies": replies.data if replies and replies.data else []}

@app.post("/drafts")
def get_drafts(df: Draft):
    if not supabase:
        raise HTTPException(status_code=500, detail="Supabase not configured.")
    drafts = supabase.table("telegram_sessions").select("Drafts").eq("user_id", df.user_id).execute()
    return {"drafts": drafts.data if drafts and drafts.data else []}

@app.post("/user")
def get_user(user: UserEmail):
    if not supabase:
        raise HTTPException(status_code=500, detail="Supabase not configured.")
    user_id = supabase.table("telegram_sessions").select("user_id").eq("email", user.email).execute()
    return {"user_id": user_id.data if user_id and user_id.data else []}

@app.put("/state")
def state(us: Toggle):
    if not supabase:
        raise HTTPException(status_code=500, detail="Supabase not configured.")
    st = supabase.table("telegram_sessions").update({"automation_state": us.pos}).eq("user_id", us.user).execute()
    return {"message": f"automation is turned {us.pos} successfully"}

# -------------------------
# FastAPI startup
# -------------------------
@app.on_event("startup")
async def startup_event():
    # Launch manager which in turn launches per-session forwarders
    logger.info("Starting main manager...")
    asyncio.create_task(main_manager())
