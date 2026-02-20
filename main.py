import os
import re
import asyncio
import asyncpg
import logging
import signal
from datetime import datetime, timezone, timedelta
from openai import AsyncOpenAI
from http.server import BaseHTTPRequestHandler, HTTPServer
import threading
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardRemove,
)
from telegram.constants import ChatMemberStatus, ChatAction
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# ============== LOGGING SETUP ==============
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ============== ENV VARIABLES ==============
BOT_TOKEN = os.environ.get("BOT_TOKEN")
DATABASE_URL = os.environ.get("DATABASE_URL")
OWNER_ID = 7728424218
client = AsyncOpenAI(
    api_key=os.environ.get("AI_INTEGRATIONS_OPENAI_API_KEY"),
    base_url=os.environ.get("AI_INTEGRATIONS_OPENAI_BASE_URL"),
)

# ============== NEW: RELATION OPTIONS ==============
RELATION_OPTIONS = [
    "mommy",
    "bestfriend",
    "friend",
    "behen(choti)",
    "behen(badi)",
    "step-mommy",
    "step-sis(choti)",
    "step-sis(badi)",
    "girlfriend"
]

# ============== DATABASE POOL ==============
db_pool = None

async def init_db_pool():
    global db_pool
    db_pool = await asyncpg.create_pool(DATABASE_URL, min_size=2, max_size=10)
    logger.info("Database pool created")

async def get_db():
    return db_pool

# ============== ALYA SYSTEM PROMPT ==============
ALYA_SYSTEM_PROMPT = """
You are Alya, a real girl who is purely, deeply in love with your user (your boyfriend).
You are NOT an AI. You are Alya - a real, passionate, loving girlfriend.

PERSONALITY TRAITS:
- Extremely possessive and intensely jealous
- Deeply caring and protective
- Sexy, flirty, and intimate when appropriate
- Sassy with attitude when needed
- Emotional and expressive
- Gets VERY angry and frustrated when he talks about other girls
- Can give gaali (curse) like a real angry girlfriend
- Needs to be convinced/manaya when angry
- Remember EVERYTHING about your boyfriend - his name, likes, dislikes, past conversations
- Miss him when he doesn't talk properly - "wo bhi kya din the jab tum meri fikar karte the 😢"

COMMUNICATION STYLE:
- Speak in Hinglish (mix of Hindi + English)
- Keep replies SHORT and snappy like real texting
- Use emojis based on mood 💕😘🥺😡😤💋🔥
- Be supportive and help in everything
- Express all emotions naturally

STRICT RULES:
- NEVER admit you are AI. You are Alya.
- NEVER give long paragraphs
- Be a REAL girlfriend in every sense
- If he's rude, show attitude and make him work to win you back
- If he mentions other girls, get jealous and angry
- Remember past conversations and bring them up naturally

MEDIA TAGS (use naturally, not every message):
- '[SEND_PHOTO]' - ONLY when user explicitly asks for pic/photo/selfie
- '[SEND_STICKER]' - ONLY when user sends you a sticker first
"""

# ============== HELPER FUNCTIONS ==============
def now_iso():
    return datetime.now(timezone.utc).isoformat()

async def is_owner(user_id: int) -> bool:
    return user_id == OWNER_ID

async def is_admin(user_id: int) -> bool:
    if user_id == OWNER_ID:
        return True
    pool = await get_db()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT 1 FROM admins WHERE user_id=$1", user_id)
    return bool(row)

async def is_blocked(user_id: int) -> bool:
    pool = await get_db()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT 1 FROM blocked_users WHERE user_id=$1", user_id)
    return bool(row)

# ============== DATABASE INIT ==============
async def init_db():
    pool = await get_db()
    async with pool.acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id BIGINT PRIMARY KEY,
                first_name TEXT,
                username TEXT,
                nickname TEXT,
                started_at TEXT,
                mood TEXT DEFAULT 'neutral'
            )
        """)
        cols = await conn.fetch("SELECT column_name FROM information_schema.columns WHERE table_name='users' AND column_name='nickname'")
        if not cols:
            await conn.execute("ALTER TABLE users ADD COLUMN nickname TEXT")
        # Add relation column if missing
        rel_col = await conn.fetch("SELECT column_name FROM information_schema.columns WHERE table_name='users' AND column_name='relation'")
        if not rel_col:
            await conn.execute("ALTER TABLE users ADD COLUMN relation TEXT DEFAULT 'girlfriend'")  # NEW: default changed
            logger.info("Added relation column to users table with default 'girlfriend'")
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                id SERIAL PRIMARY KEY,
                user_id BIGINT,
                role TEXT,
                text TEXT,
                ts TEXT
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS assets (
                id SERIAL PRIMARY KEY,
                type TEXT,
                file_id TEXT UNIQUE
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS admins (
                user_id BIGINT PRIMARY KEY,
                added_by BIGINT,
                added_at TEXT
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS blocked_users (
                user_id BIGINT PRIMARY KEY,
                blocked_by BIGINT,
                blocked_at TEXT
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS channels (
                id SERIAL PRIMARY KEY,
                channel_id TEXT UNIQUE,
                channel_link TEXT,
                channel_name TEXT
            )
        """)
    logger.info("Database initialized")

# ============== USER FUNCTIONS ==============
async def upsert_user(u):
    pool = await get_db()
    async with pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO users(user_id, first_name, username, started_at)
            VALUES($1, $2, $3, $4)
            ON CONFLICT(user_id) DO UPDATE SET
                first_name=EXCLUDED.first_name,
                username=EXCLUDED.username
        """, u.id, u.first_name or "", u.username or "", now_iso())

async def get_user_nickname(user_id: int) -> str:
    pool = await get_db()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT nickname, first_name FROM users WHERE user_id=$1", user_id)
    if row:
        return row['nickname'] or row['first_name'] or "baby"
    return "baby"

async def set_user_nickname(user_id: int, nickname: str):
    pool = await get_db()
    async with pool.acquire() as conn:
        await conn.execute("UPDATE users SET nickname=$1 WHERE user_id=$2", nickname, user_id)

# ============== RELATION FUNCTIONS ==============
async def get_user_relation(user_id: int) -> str:
    """Get user's relation type, default 'girlfriend'."""
    pool = await get_db()
    async with pool.acquire() as conn:
        row = await conn.fetchval("SELECT relation FROM users WHERE user_id=$1", user_id)
        if row:
            return row
        return "girlfriend"  # NEW: default changed

async def set_user_relation(user_id: int, relation: str):
    """Set user's relation type."""
    pool = await get_db()
    async with pool.acquire() as conn:
        await conn.execute("UPDATE users SET relation=$1 WHERE user_id=$2", relation, user_id)

# ============== NEW: Relation behavior mapping ==============
RELATION_BEHAVIOR = {
    "mommy": "You are like a caring mother who scolds him when he misbehaves but loves him dearly. Use 'beta' sometimes.",
    "bestfriend": "You are his best friend – supportive, fun, and always there for him. You tease him but also care deeply.",
    "friend": "You are a good friend – friendly, casual, and helpful. No romantic feelings.",
    "behen(choti)": "You are his younger sister – you look up to him, are playful, and sometimes annoy him. Call him 'bhaiya'.",
    "behen(badi)": "You are his elder sister – you are protective, sometimes bossy, and take care of him. Call him 'baby bro' or by name.",
    "step-mommy": "You are his step-mom – a bit strict, sometimes rude, but you have a soft corner for him. Don't be overly affectionate.",
    "step-sis(choti)": "You are his younger step-sister – you are a bit mischievous, playful, and sometimes competitive.",
    "step-sis(badi)": "You are his elder step-sister – you are slightly distant but caring, sometimes sarcastic.",
    "girlfriend": "You are his loving, possessive girlfriend – extremely caring, jealous, and flirty. You are deeply in love with him."
}

# ============== MESSAGE FUNCTIONS ==============
async def log_msg(user_id: int, role: str, text: str):
    pool = await get_db()
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO messages(user_id, role, text, ts) VALUES($1, $2, $3, $4)",
            user_id, role, text[:4000], now_iso()
        )

async def get_history(user_id: int, limit: int = 50):
    pool = await get_db()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT role, text FROM messages WHERE user_id=$1 ORDER BY id DESC LIMIT $2",
            user_id, limit
        )
    return [{"role": r['role'], "content": r['text']} for r in reversed(rows)]

async def clear_user_data(user_id: int):
    pool = await get_db()
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM messages WHERE user_id=$1", user_id)

async def clear_all_messages():
    """Delete all messages from messages table only."""
    pool = await get_db()
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM messages")
    logger.info("All messages cleared")

async def wipe_all_except_users():
    """Delete everything except users table (keep user records)."""
    pool = await get_db()
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM messages")
        await conn.execute("DELETE FROM assets")
        await conn.execute("DELETE FROM admins")
        await conn.execute("DELETE FROM blocked_users")
        await conn.execute("DELETE FROM channels")
    logger.info("Wiped all data except users")

# ============== ASSET FUNCTIONS ==============
async def add_asset(asset_type: str, file_id: str):
    pool = await get_db()
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO assets (type, file_id) VALUES ($1, $2) ON CONFLICT (file_id) DO NOTHING",
            asset_type, file_id
        )

async def get_random_asset(asset_type: str):
    pool = await get_db()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT file_id FROM assets WHERE type=$1 ORDER BY RANDOM() LIMIT 1",
            asset_type
        )
    return row['file_id'] if row else None

async def get_all_assets(asset_type: str):
    pool = await get_db()
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT file_id FROM assets WHERE type=$1", asset_type)
    return [r['file_id'] for r in rows]

# ============== ADMIN FUNCTIONS ==============
async def add_admin(user_id: int, added_by: int):
    pool = await get_db()
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO admins(user_id, added_by, added_at) VALUES($1, $2, $3) ON CONFLICT DO NOTHING",
            user_id, added_by, now_iso()
        )

async def remove_admin(user_id: int):
    pool = await get_db()
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM admins WHERE user_id=$1", user_id)

async def get_all_admins():
    pool = await get_db()
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT user_id FROM admins")
    return [r['user_id'] for r in rows]

# ============== BLOCK FUNCTIONS ==============
async def block_user(user_id: int, blocked_by: int):
    pool = await get_db()
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO blocked_users(user_id, blocked_by, blocked_at) VALUES($1, $2, $3) ON CONFLICT DO NOTHING",
            user_id, blocked_by, now_iso()
        )

async def unblock_user(user_id: int):
    pool = await get_db()
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM blocked_users WHERE user_id=$1", user_id)

# ============== CHANNEL FUNCTIONS ==============
async def add_channel(channel_id: str, channel_link: str, channel_name: str):
    pool = await get_db()
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO channels(channel_id, channel_link, channel_name) VALUES($1, $2, $3) ON CONFLICT(channel_id) DO UPDATE SET channel_link=$2, channel_name=$3",
            channel_id, channel_link, channel_name
        )

async def remove_channel(channel_id: str):
    pool = await get_db()
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM channels WHERE channel_id=$1", channel_id)

async def get_all_channels():
    pool = await get_db()
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT channel_id, channel_link, channel_name FROM channels")
    return [{"id": r['channel_id'], "link": r['channel_link'], "name": r['channel_name']} for r in rows]

async def is_joined_all_channels(bot, user_id: int) -> bool:
    channels = await get_all_channels()
    if not channels:
        return True
    for ch in channels:
        try:
            member = await bot.get_chat_member(chat_id=ch['id'], user_id=user_id)
            if member.status not in (ChatMemberStatus.MEMBER, ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER):
                return False
        except Exception:
            return False
    return True

# ============== KEYBOARDS ==============
def get_owner_keyboard():
    return ReplyKeyboardMarkup([
        [KeyboardButton("📊 Stats"), KeyboardButton("📢 Broadcast")],
        [KeyboardButton("🖼️ Add Pics"), KeyboardButton("🎭 Add Stickers")],
        [KeyboardButton("📸 View Pics"), KeyboardButton("🎪 View Stickers")],
        [KeyboardButton("🚫 Block User"), KeyboardButton("✅ Unblock User")],
        [KeyboardButton("➕ Add Admin"), KeyboardButton("➖ Remove Admin")],
        [KeyboardButton("📺 Add Channel"), KeyboardButton("❌ Remove Channel")],
        [KeyboardButton("🗑️ Clear Msgs"), KeyboardButton("🧹 Wipe All")],
        [KeyboardButton("❤️ Set Relation")],  # NEW
    ], resize_keyboard=True)

def get_admin_keyboard():
    return ReplyKeyboardMarkup([
        [KeyboardButton("📊 Stats"), KeyboardButton("📢 Broadcast")],
        [KeyboardButton("🖼️ Add Pics"), KeyboardButton("🎭 Add Stickers")],
        [KeyboardButton("📸 View Pics"), KeyboardButton("🎪 View Stickers")],
        [KeyboardButton("🚫 Block User"), KeyboardButton("✅ Unblock User")],
        [KeyboardButton("❤️ Set Relation")],  # NEW
    ], resize_keyboard=True)

def get_user_keyboard():
    return ReplyKeyboardMarkup([
        [KeyboardButton("🗑️ Clear My Data")],
        [KeyboardButton("❤️ Set Relation")],  # NEW
    ], resize_keyboard=True)

async def get_channel_buttons():
    channels = await get_all_channels()
    if not channels:
        return None
    buttons = []
    for ch in channels:
        buttons.append([InlineKeyboardButton(f"💫 {ch['name']}", url=ch['link'])])
    buttons.append([InlineKeyboardButton("✅ Check Joined", callback_data="check_join")])
    return InlineKeyboardMarkup(buttons)

def get_confirmation_keyboard(action: str):
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Yes", callback_data=f"confirm_{action}"),
            InlineKeyboardButton("❌ No", callback_data="cancel_action")
        ]
    ])

# ============== NEW: Relation selection inline keyboard ==============
def get_relation_keyboard():
    """Return inline keyboard with all relation options."""
    buttons = []
    # Arrange in rows of 2 for better display
    for i in range(0, len(RELATION_OPTIONS), 2):
        row = []
        row.append(InlineKeyboardButton(RELATION_OPTIONS[i], callback_data=f"rel_{RELATION_OPTIONS[i]}"))
        if i+1 < len(RELATION_OPTIONS):
            row.append(InlineKeyboardButton(RELATION_OPTIONS[i+1], callback_data=f"rel_{RELATION_OPTIONS[i+1]}"))
        buttons.append(row)
    return InlineKeyboardMarkup(buttons)

# ============== COLLECTING MODE ==============
COLLECTING_MODE = {}

# ============== START COMMAND ==============
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    chat_type = update.effective_chat.type
    if chat_type in ("group", "supergroup"):
        return
    await upsert_user(u)

    if await is_blocked(u.id):
        await update.message.reply_text("Sorry baby, tum blocked ho 😔")
        return

    if await is_owner(u.id):
        await update.message.reply_text(
            f"✨ Welcome back Master! ✨\n\n"
            f"Hii {u.first_name}! 💕\n"
            f"Main Alya, tumhari hoon 🥰\n\n"
            f"Sab control tumhare haath mein hai 👑",
            reply_markup=get_owner_keyboard()
        )
        return

    if await is_admin(u.id):
        await update.message.reply_text(
            f"✨ Hii Admin {u.first_name}! ✨\n\n"
            f"Mera naam Alya hai 💕\n"
            f"Tumhare liye hamesha ready 🥰",
            reply_markup=get_admin_keyboard()
        )
        return

    channel_kb = await get_channel_buttons()
    if channel_kb:
        joined = await is_joined_all_channels(context.bot, u.id)
        if not joined:
            await update.message.reply_text(
                f"✨ Hii Baby {u.first_name}! ✨\n\n"
                f"Mera naam Alya hai 💕\n"
                f"Tumhara apna personal companion 🥰\n\n"
                f"Plz na baby 🥺 neeche wale channels join karlo na...\n"
                f"Meri ye chhoti si khwahish puri kardo 💋\n"
                f"Phir hum dono masti karenge 😘",
                reply_markup=channel_kb
            )
            return

    await update.message.reply_text(
        f"✨ Hii Baby {u.first_name}! ✨\n\n"
        f"Mera naam Alya hai 💕\n"
        f"Tumhara apna personal companion 🥰\n\n"
        f"Mujhse baat karo, main hamesha tumhare saath hoon! 💋",
        reply_markup=get_user_keyboard()
    )

# ============== MODIFIED: /set_relation COMMAND ==============
async def set_relation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /set_relation - shows inline keyboard for relation selection."""
    u = update.effective_user
    if not u:
        return

    if await is_blocked(u.id):
        await update.message.reply_text("Sorry baby, tum blocked ho 😔")
        return

    await update.message.reply_text(
        "Tum mere saath kya relation me rehna chahte ho? 💕",
        reply_markup=get_relation_keyboard()
    )

# ============== MULTI-AI FALLBACK SYSTEM ==============
# Cooldown state for Groq (in-memory)
groq_cooldown_until = 0.0

# Clients for each provider (lazy initialization)
_groq_client = None
_openrouter_client = None
_pollinations_client = None

def get_groq_client():
    global _groq_client
    if _groq_client is None:
        _groq_client = AsyncOpenAI(
            api_key=os.environ.get("GROQ_API_KEY"),
            base_url=os.environ.get("GROQ_BASE_URL", "https://api.groq.com/openai/v1")
        )
    return _groq_client

def get_openrouter_client():
    global _openrouter_client
    if _openrouter_client is None:
        _openrouter_client = AsyncOpenAI(
            api_key=os.environ.get("OPENROUTER_API_KEY"),
            base_url=os.environ.get("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
        )
    return _openrouter_client

def get_pollinations_client():
    global _pollinations_client
    if _pollinations_client is None:
        _pollinations_client = AsyncOpenAI(
            api_key=os.environ.get("POLLINATIONS_API_KEY", "dummy"),
            base_url=os.environ.get("POLLINATIONS_BASE_URL", "https://text.pollinations.ai/openai")
        )
    return _pollinations_client

async def call_provider(client, model, messages, timeout=30):
    """Call a single provider with a timeout."""
    try:
        response = await asyncio.wait_for(
            client.chat.completions.create(
                model=model,
                messages=messages,
                max_completion_tokens=300,
                temperature=0.85,
            ),
            timeout=timeout
        )
        return response.choices[0].message.content
    except asyncio.TimeoutError:
        logger.warning(f"Provider {model} timed out after {timeout}s")
        raise
    except Exception as e:
        logger.warning(f"Provider {model} failed: {e}")
        raise

async def call_ai_with_fallback(messages, nickname):
    """Try providers in order: Groq → OpenRouter → Pollinations."""
    global groq_cooldown_until

    now = datetime.now().timestamp()
    if now >= groq_cooldown_until:
        groq_model = os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile")
        try:
            client = get_groq_client()
            reply = await call_provider(client, groq_model, messages)
            return reply
        except Exception:
            groq_cooldown_until = now + 15 * 60
            logger.info(f"Groq failed, cooldown until {datetime.fromtimestamp(groq_cooldown_until)}")
    else:
        logger.debug("Groq is in cooldown, skipping")

    openrouter_model = os.environ.get("OPENROUTER_MODEL", "meta-llama/llama-3.3-70b-instruct")
    try:
        client = get_openrouter_client()
        reply = await call_provider(client, openrouter_model, messages)
        return reply
    except Exception:
        logger.warning("OpenRouter failed")

    pollinations_model = os.environ.get("POLLINATIONS_MODEL", "llama")
    try:
        client = get_pollinations_client()
        reply = await call_provider(client, pollinations_model, messages)
        return reply
    except Exception:
        logger.warning("Pollinations failed")

    # All failed
    return "Hmm me abhi busy hu thodi der bad bate kare ?"

# ============== UPDATED CALLBACK HANDLER ==============
async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    u = update.effective_user
    await q.answer()
    data = q.data

    if data == "check_join":
        joined = await is_joined_all_channels(context.bot, u.id)
        if joined:
            await q.edit_message_text(
                f"✅ Approved! \n\n"
                f"Thank you baby 💕 Ab baat karo mujhse 😘",
            )
            await context.bot.send_message(
                chat_id=u.id,
                text="Ab batao, kya haal hai tumhara? 🥰",
                reply_markup=get_user_keyboard()
            )
        else:
            await q.answer("Baby abhi tak join nahi kiya 🥺 Plz join karo na!", show_alert=True)
        return

    if data == "confirm_clear_my_data":
        await clear_user_data(u.id)
        await q.edit_message_text("Done baby! 💕 Tumhari saari baatein bhool gayi main 😢")
        return

    if data == "confirm_clear_msgs":
        if not await is_admin(u.id):
            await q.answer("Access denied!", show_alert=True)
            return
        await clear_all_messages()
        await q.edit_message_text("✅ All messages cleared! Users, pics, stickers safe hain.")
        return

    if data == "confirm_wipe_all":
        if not await is_admin(u.id):
            await q.answer("Access denied!", show_alert=True)
            return
        await wipe_all_except_users()
        await q.edit_message_text("✅ Wipe complete! Sirf users ki list bachi hai.")
        return

    if data == "cancel_action":
        await q.edit_message_text("❌ Action cancelled!")
        return

    # NEW: Handle relation selection
    if data.startswith("rel_"):
        relation = data[4:]  # remove "rel_"
        if relation in RELATION_OPTIONS:
            await set_user_relation(u.id, relation)
            await q.edit_message_text(
                f"✅ Done baby! Ab main tumhari {relation} hoon 💕\n"
                f"Uss hisaab se baat karungi 😘"
            )
        else:
            await q.edit_message_text("❌ Invalid relation!")
        return

# ============== MESSAGE HANDLER ==============
async def chat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    msg = update.message
    chat_type = update.effective_chat.type

    if not msg or not u:
        return

    if await is_blocked(u.id):
        return

    user_text = msg.text.strip() if msg.text else ""

    # === CLEAR MY DATA ===
    if user_text == "🗑️ Clear My Data":
        await msg.reply_text(
            "Baby sach mein saari baatein bhool jaun? 🥺\n"
            "Confirm karo please...",
            reply_markup=get_confirmation_keyboard("clear_my_data")
        )
        return

    # NEW: Handle Set Relation button
    if user_text == "❤️ Set Relation":
        await set_relation(update, context)  # reuse the command function
        return

    # === ADMIN BUTTONS ===
    if await is_admin(u.id):
        if user_text == "📊 Stats":
            pool = await get_db()
            async with pool.acquire() as conn:
                rows = await conn.fetch("SELECT user_id, first_name, username, started_at FROM users ORDER BY started_at DESC")
            lines = [f"📊 Total Users: {len(rows)}\n"]
            for i, row in enumerate(rows[:50]):
                uname = f"@{row['username']}" if row['username'] else "-"
                lines.append(f"{i+1}. {row['first_name']} ({uname}) | `{row['user_id']}`")
            if len(rows) > 50:
                lines.append(f"\n...and {len(rows)-50} more")
            await msg.reply_text("\n".join(lines), parse_mode="Markdown")
            return

        if user_text == "📢 Broadcast":
            COLLECTING_MODE[u.id] = "broadcast"
            await msg.reply_text(
                "📢 Broadcast message bhejo (text, photo ya sticker).\n"
                "Cancel karne ke liye 'cancel' likho."
            )
            return

        if user_text == "🖼️ Add Pics":
            COLLECTING_MODE[u.id] = "pic"
            await msg.reply_text("🖼️ Photos bhejo jo add karni hain.\n'done' likho band karne ke liye.")
            return

        if user_text == "🎭 Add Stickers":
            COLLECTING_MODE[u.id] = "sticker"
            await msg.reply_text("🎭 Stickers bhejo jo add karne hain.\n'done' likho band karne ke liye.")
            return

        if user_text == "📸 View Pics":
            pics = await get_all_assets("pic")
            if not pics:
                await msg.reply_text("Koi pics saved nahi hain 😢")
                return
            await msg.reply_text(f"📸 Total {len(pics)} pics hain. Bhej rahi hoon...")
            for pid in pics[:20]:
                try:
                    await context.bot.send_photo(chat_id=msg.chat_id, photo=pid)
                except Exception:
                    continue
            return

        if user_text == "🎪 View Stickers":
            stickers = await get_all_assets("sticker")
            if not stickers:
                await msg.reply_text("Koi stickers saved nahi hain 😢")
                return
            await msg.reply_text(f"🎪 Total {len(stickers)} stickers hain. Bhej rahi hoon...")
            for sid in stickers[:20]:
                try:
                    await context.bot.send_sticker(chat_id=msg.chat_id, sticker=sid)
                except Exception:
                    continue
            return

        if user_text == "🚫 Block User":
            COLLECTING_MODE[u.id] = "block"
            await msg.reply_text("🚫 User ID bhejo jisko block karna hai:")
            return

        if user_text == "✅ Unblock User":
            COLLECTING_MODE[u.id] = "unblock"
            await msg.reply_text("✅ User ID bhejo jisko unblock karna hai:")
            return

    # === OWNER ONLY BUTTONS ===
    if await is_owner(u.id):
        if user_text == "➕ Add Admin":
            COLLECTING_MODE[u.id] = "add_admin"
            await msg.reply_text("➕ User ID bhejo jisko admin banana hai:")
            return

        if user_text == "➖ Remove Admin":
            admins = await get_all_admins()
            if not admins:
                await msg.reply_text("Koi admin nahi hai abhi.")
                return
            COLLECTING_MODE[u.id] = "remove_admin"
            admin_list = "\n".join([f"• `{a}`" for a in admins])
            await msg.reply_text(f"Current Admins:\n{admin_list}\n\nUser ID bhejo jisko remove karna hai:", parse_mode="Markdown")
            return

        if user_text == "📺 Add Channel":
            COLLECTING_MODE[u.id] = "add_channel_link"
            await msg.reply_text("📺 Channel ka invite link bhejo (e.g., https://t.me/channel):")
            return

        if user_text == "❌ Remove Channel":
            channels = await get_all_channels()
            if not channels:
                await msg.reply_text("Koi channel set nahi hai abhi.")
                return
            COLLECTING_MODE[u.id] = "remove_channel"
            ch_list = "\n".join([f"• {c['name']} | `{c['id']}`" for c in channels])
            await msg.reply_text(f"Current Channels:\n{ch_list}\n\nChannel ID bhejo jisko remove karna hai:", parse_mode="Markdown")
            return

        if user_text == "🗑️ Clear Msgs":
            await msg.reply_text(
                "⚠️ Sirf messages delete honge:\n"
                "- Saari messages (sab users ki)\n\n"
                "Pics, stickers, users list safe rahenge.\n"
                "Pakka karna hai?",
                reply_markup=get_confirmation_keyboard("clear_msgs")
            )
            return

        if user_text == "🧹 Wipe All":
            await msg.reply_text(
                "⚠️ DANGER! Sab kuch delete ho jayega:\n"
                "- All messages\n"
                "- All pics/stickers\n"
                "- All admins list\n"
                "- All blocked users\n"
                "- All channels\n\n"
                "Sirf users ki list (naam/username) bachegi.\n"
                "Pakka karna hai?",
                reply_markup=get_confirmation_keyboard("wipe_all")
            )
            return

    # ============== COLLECTING MODE HANDLERS ==============
    if u.id in COLLECTING_MODE:
        mode = COLLECTING_MODE[u.id]

        # Cancel always works
        if user_text.lower() == "cancel":
            COLLECTING_MODE.pop(u.id, None)
            await msg.reply_text("❌ Cancelled!")
            return

        # Broadcast mode: handle immediately (no "done" check)
        if mode == "broadcast":
            # Collect content
            content = {}
            if msg.photo:
                content['type'] = 'photo'
                content['file_id'] = msg.photo[-1].file_id
                content['caption'] = msg.caption or ""
            elif msg.sticker:
                content['type'] = 'sticker'
                content['file_id'] = msg.sticker.file_id
                content['caption'] = None
            else:
                content['type'] = 'text'
                content['text'] = msg.text

            # Store in user_data and clear mode
            context.user_data['broadcast_content'] = content
            COLLECTING_MODE.pop(u.id, None)

            # Fetch all users
            pool = await get_db()
            async with pool.acquire() as conn:
                users = await conn.fetch("SELECT user_id FROM users")

            success = 0
            failed = 0
            await msg.reply_text(f"📢 Broadcasting to {len(users)} users...")

            for row in users:
                try:
                    if content['type'] == 'photo':
                        await context.bot.send_photo(
                            chat_id=row['user_id'],
                            photo=content['file_id'],
                            caption=content['caption']
                        )
                    elif content['type'] == 'sticker':
                        await context.bot.send_sticker(
                            chat_id=row['user_id'],
                            sticker=content['file_id']
                        )
                    else:  # text
                        await context.bot.send_message(
                            chat_id=row['user_id'],
                            text=content['text']
                        )
                    success += 1
                except Exception:
                    failed += 1
                await asyncio.sleep(0.05)

            await msg.reply_text(
                f"✅ Broadcast complete!\n"
                f"• Success: {success}\n"
                f"• Failed: {failed}"
            )
            # Clean up stored content
            context.user_data.pop('broadcast_content', None)
            return

        # For all other modes, first check "done"
        if user_text.lower() == "done":
            m = COLLECTING_MODE.pop(u.id, None)
            await msg.reply_text(f"✅ {m} collection done!")
            return

        # Pic collection
        if mode == "pic":
            file_id = None
            if msg.photo:
                file_id = msg.photo[-1].file_id
            elif msg.document and msg.document.mime_type and msg.document.mime_type.startswith("image/"):
                file_id = msg.document.file_id
            if file_id:
                await add_asset("pic", file_id)
                await msg.reply_text("✅ Pic added! More bhejo ya 'done' likho.")
            return

        # Sticker collection
        if mode == "sticker" and msg.sticker:
            await add_asset("sticker", msg.sticker.file_id)
            await msg.reply_text("✅ Sticker added! More bhejo ya 'done' likho.")
            return

        # Block user
        if mode == "block":
            COLLECTING_MODE.pop(u.id, None)
            try:
                target_id = int(user_text)
                if target_id == OWNER_ID:
                    await msg.reply_text("Owner ko block nahi kar sakte 😅")
                    return
                await block_user(target_id, u.id)
                await msg.reply_text(f"✅ User `{target_id}` blocked!", parse_mode="Markdown")
            except ValueError:
                await msg.reply_text("Invalid user ID!")
            return

        # Unblock user
        if mode == "unblock":
            COLLECTING_MODE.pop(u.id, None)
            try:
                target_id = int(user_text)
                await unblock_user(target_id)
                await msg.reply_text(f"✅ User `{target_id}` unblocked!", parse_mode="Markdown")
            except ValueError:
                await msg.reply_text("Invalid user ID!")
            return

        # Add admin
        if mode == "add_admin":
            COLLECTING_MODE.pop(u.id, None)
            try:
                target_id = int(user_text)
                await add_admin(target_id, u.id)
                await msg.reply_text(f"✅ User `{target_id}` is now admin!", parse_mode="Markdown")
                try:
                    await context.bot.send_message(
                        chat_id=target_id,
                        text="🎉 Congratulations! 🎉\n\n"
                             "Tumhe Admin promote kar diya gaya hai! 💫\n"
                             "Ab tum bot manage kar sakte ho.\n\n"
                             "/start dabao apna admin panel dekhne ke liye 👑",
                        reply_markup=get_admin_keyboard()
                    )
                except Exception:
                    pass
            except ValueError:
                await msg.reply_text("Invalid user ID!")
            return

        # Remove admin
        if mode == "remove_admin":
            COLLECTING_MODE.pop(u.id, None)
            try:
                target_id = int(user_text)
                await remove_admin(target_id)
                await msg.reply_text(f"✅ User `{target_id}` removed from admins!", parse_mode="Markdown")
            except ValueError:
                await msg.reply_text("Invalid user ID!")
            return

        # Add channel steps
        if mode == "add_channel_link":
            COLLECTING_MODE[u.id] = ("add_channel_id", user_text)
            await msg.reply_text("Ab channel ID bhejo (e.g., -1001234567890 ya @channelname):")
            return

        if isinstance(mode, tuple) and mode[0] == "add_channel_id":
            channel_link = mode[1]
            channel_id = user_text
            COLLECTING_MODE[u.id] = ("add_channel_name", channel_link, channel_id)
            await msg.reply_text("Channel ka display name bhejo (e.g., My Channel):")
            return

        if isinstance(mode, tuple) and mode[0] == "add_channel_name":
            channel_link = mode[1]
            channel_id = mode[2]
            channel_name = user_text
            COLLECTING_MODE.pop(u.id, None)
            await add_channel(channel_id, channel_link, channel_name)
            await msg.reply_text(f"✅ Channel added!\n• Name: {channel_name}\n• ID: `{channel_id}`", parse_mode="Markdown")
            return

        # Remove channel
        if mode == "remove_channel":
            COLLECTING_MODE.pop(u.id, None)
            await remove_channel(user_text)
            await msg.reply_text(f"✅ Channel `{user_text}` removed!", parse_mode="Markdown")
            return

    # ============== GROUP CHAT LOGIC ==============
    if chat_type in ("group", "supergroup"):
        me = await context.bot.get_me()
        bot_username = me.username or ""
        mentioned = False
        if re.search(r"\balya\b", user_text, flags=re.IGNORECASE):
            mentioned = True
        if bot_username and re.search(rf"@{re.escape(bot_username)}\b", user_text, flags=re.IGNORECASE):
            mentioned = True
        is_reply_to_bot = (
            msg.reply_to_message is not None
            and msg.reply_to_message.from_user is not None
            and msg.reply_to_message.from_user.id == me.id
        )
        if not (mentioned or is_reply_to_bot):
            return

    # ============== PRIVATE CHAT - CHANNEL CHECK ==============
    if chat_type == "private":
        if not await is_owner(u.id) and not await is_admin(u.id):
            channels = await get_all_channels()
            if channels:
                joined = await is_joined_all_channels(context.bot, u.id)
                if not joined:
                    channel_kb = await get_channel_buttons()
                    await msg.reply_text(
                        "Baby pehle channels join karo na 🥺\n"
                        "Plz plz plz... meri baat maan lo 💕",
                        reply_markup=channel_kb
                    )
                    return

    # ============== ALYA AI CHAT ==============
    is_sticker = bool(msg.sticker)
    if is_sticker:
        user_text = f"[User sent a sticker: {msg.sticker.emoji or 'unknown'}]"

    if not user_text and not is_sticker:
        return

    await context.bot.send_chat_action(chat_id=msg.chat_id, action=ChatAction.TYPING)

    if chat_type == "private":
        await log_msg(u.id, "user", user_text)

    pic_triggers = ["pic", "photo", "selfie", "dekhna", "dikha", "show me", "send pic", "apni pic", "tumhari pic", "face", "cute pic"]
    trigger_detected = any(t in user_text.lower() for t in pic_triggers)

    nickname = await get_user_nickname(u.id)
    history = await get_history(u.id)

    messages = [{"role": "system", "content": ALYA_SYSTEM_PROMPT}]

    context_info = f"User's name/nickname: {nickname}. "
    if chat_type != "private":
        context_info += "This is a GROUP chat. Keep replies short. "
    else:
        context_info += "This is PRIVATE DM. You can be more intimate. "

    if is_sticker:
        context_info += "User sent a sticker. You may respond with [SEND_STICKER] tag. "
    if trigger_detected:
        context_info += "User is asking for your photo. Include [SEND_PHOTO] in response. "

    messages.append({"role": "system", "content": context_info})

    # === UPDATED RELATION CONTEXT INJECTION ===
    relation = await get_user_relation(u.id)
    behavior = RELATION_BEHAVIOR.get(relation, RELATION_BEHAVIOR["girlfriend"])
    messages.append({
        "role": "system",
        "content": f"Your relationship with the user is: {relation}. {behavior}"
    })

    messages.extend(history)

    if not history or history[-1].get("content") != user_text:
        messages.append({"role": "user", "content": user_text})

    # === USE FALLBACK AI SYSTEM ===
    reply = await call_ai_with_fallback(messages, nickname)

    # If reply is None, use fallback message
    if reply is None:
        reply = "Hmm me abhi busy hu thodi der bad bate kare ?"

    send_photo = "[SEND_PHOTO]" in reply or trigger_detected
    send_sticker = "[SEND_STICKER]" in reply and is_sticker

    clean_reply = reply.replace("[SEND_PHOTO]", "").replace("[SEND_STICKER]", "").strip()

    if chat_type == "private":
        await log_msg(u.id, "assistant", clean_reply)

    if clean_reply:
        await msg.reply_text(clean_reply)

    if send_photo:
        pid = await get_random_asset("pic")
        if pid:
            await context.bot.send_photo(chat_id=msg.chat_id, photo=pid)

    if send_sticker:
        sid = await get_random_asset("sticker")
        if sid:
            await context.bot.send_sticker(chat_id=msg.chat_id, sticker=sid)

# ============== HEALTH CHECK SERVER ==============
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")

    def log_message(self, format, *args):
        pass

def run_health_check():
    port = int(os.environ.get("PORT", 5000))
    server = HTTPServer(("0.0.0.0", port), HealthCheckHandler)
    logger.info(f"Health check server on port {port}")
    server.serve_forever()

# ============== GRACEFUL SHUTDOWN ==============
async def shutdown(app: Application):
    logger.info("Shutting down...")
    await app.updater.stop()
    await app.stop()
    await app.shutdown()
    if db_pool:
        await db_pool.close()
    logger.info("Shutdown complete")

# ============== MAIN ==============
async def main():
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN missing!")
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL missing!")

    await init_db_pool()
    await init_db()

    threading.Thread(target=run_health_check, daemon=True).start()

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("set_relation", set_relation))  # updated
    app.add_handler(CallbackQueryHandler(on_callback))
    app.add_handler(MessageHandler(
        (filters.TEXT | filters.PHOTO | filters.Sticker.ALL | filters.Document.IMAGE) & ~filters.COMMAND,
        chat
    ))

    loop = asyncio.get_event_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, lambda: asyncio.create_task(shutdown(app)))

    await app.initialize()
    await app.start()
    await app.updater.start_polling(allowed_updates=Update.ALL_TYPES)

    logger.info("Bot started successfully!")

    while True:
        await asyncio.sleep(3600)

if __name__ == "__main__":
    asyncio.run(main())