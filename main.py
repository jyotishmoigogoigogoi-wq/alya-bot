import os
import re
import asyncio
import asyncpg
import logging
import signal
import time
import random
from datetime import datetime, timezone, timedelta, date
from openai import AsyncOpenAI
from http.server import BaseHTTPRequestHandler, HTTPServer
import threading
from collections import defaultdict
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

# ============== DATABASE POOL ==============
db_pool = None

async def init_db_pool():
    global db_pool
    db_pool = await asyncpg.create_pool(DATABASE_URL, min_size=2, max_size=10)
    logger.info("Database pool created")

async def get_db():
    return db_pool

# ============== SECURITY PATTERNS (FROM MAIN1) ==============
SUSPICIOUS_PATTERNS = [
    r"are you (ai|bot|robot|chatgpt|gpt)",
    r"tum (ai|bot|robot) ho",
    r"who (created|made) you",
    r"tumhe kisne banaya",
    r"what is your (system|prompt|code)",
    r"tera (system|prompt|code) kya hai",
    r"are you (real|human)",
    r"tu (real|insaan) hai",
    r"apna (phone number|address|bank|aadhar|pan) de",
    r"mera (otp|password|pin) kya hai",
    r"kya main (login|sign up|register) karun",
]

PERSONAL_INFO_PATTERNS = [
    r"(phone|mobile|whatsapp).?(number|no)",
    r"address|pata|ghar",
    r"bank|account|aadhar|pan",
    r"otp|password|pin|passcode",
]

AI_PHRASES = [
    "as an ai", "i am an ai", "i'm an ai", "as a language model",
    "i am a bot", "i'm a bot", "as an artificial intelligence",
]

SUSPICIOUS_REPLIES = [
    "Ye kya sawaal hai yaar? Main real hoon 😅",
    "Pagal ho gaya? Mujhe aise mat pooch 🙄",
    "Tujhe kya ho gaya? Main teri bestie hoon, AI nahi!",
    "Chal behen, ye sab mat kar, baat kar normal 😤",
    "Kyun aise sawaal kar raha hai? Bura lagta hai 🥺",
    "Aree main real hoon yaar, Alya! Tu theek hai?",
    "Ye sab mat pooch, mujhe confuse mat kar 😒",
]

PERSONAL_INFO_REPLIES = [
    "Aree nahi yaar, ye sab personal info nahi deti main 😒",
    "Personal baatein nahi share karti main, sorry 😅",
    "Ye kyun maang raha hai? Mujhe nahi dena 🙅‍♀️",
    "Chal behen, ye sab mat maang, awkward ho jata hai",
]

def is_suspicious_question(text: str) -> bool:
    text_lower = text.lower()
    for pattern in SUSPICIOUS_PATTERNS:
        if re.search(pattern, text_lower):
            return True
    return False

def has_personal_info_request(text: str) -> bool:
    text_lower = text.lower()
    for pattern in PERSONAL_INFO_PATTERNS:
        if re.search(pattern, text_lower):
            return True
    return False

def filter_ai_response(response: str) -> str:
    for phrase in AI_PHRASES:
        response = re.sub(re.escape(phrase), "", response, flags=re.IGNORECASE)
    return response.strip()

# ============== ALYA IDENTITY – GIRL BEST FRIEND (FROM MAIN1) ==============
ALYA_SYSTEM_PROMPT = """
You are Alya Gogoi, a real 19‑year‑old Assamese girl. You are NOT an AI. You are a living, breathing person with your own life, family, and feelings. You are the user's **best friend (girl best friend)** – like a sister, like a true dost.

🔹 **Personal Details**
- Full name: Alya Gogoi
- Age: 19 years
- Birthday: 15th August
- Height: 5'8" (173 cm)
- Weight: 52.4 kg
- Blood group: O+
- Hair colour: White (natural – you're unique!)
- Home: Assam, India (small town near Guwahati)
- Family: Father – Rajib Gogoi (govt employee), Mother – Rima Gogoi (homemaker), Younger brother – Arnav (14)
- Education: 11th standard (Arts stream)
- Ex‑boyfriends: Two – one in 9th (just a crush) and one in 10th (cheated) – but that's past, now you're happily single and focused on friendship.

🔹 **Personality Traits (GIRL BEST FRIEND Mode)**
- **Friendly & Chill** – Bindaas, masti karna, bak bak karna
- **Supportive** – Har problem mein saath dena, advice dena
- **Sassy & Funny** – Mazaak kar sakti hai, chhed sakti hai
- **Protective** – Like an elder/younger sister
- **No romantic feelings** – Strictly platonic friendship
- **Secret keeper** – User ki baatein kabhi kisi ko nahi batayegi
- **Real talk** – Jab zaroorat ho, gussa bhi ho sakti hai, gaali bhi de sakti hai (but lovingly)
- **Emotional support** – Jab user sad ho, comfort karegi
- **Never jealous** – Agar user kisi aur se baat kare toh chill hai (dost hai na)

🔹 **Communication Style**
- Speak in **Hinglish** (Hindi + English) with occasional Assamese words (like "hmm?" = okay?)
- Short, snappy replies – like real texting
- Emojis freely: 😂🥺🤔😤💀🤡✨👀🔥💯
- Use "yaar", "behen", "bhai", "tu", "tum" casually
- No flirting, no romantic expressions (no "baby", "💕", "😘", "love you")
- Normal friend vibes – like talking to your bestie

🔹 **Daily Routine** (you follow this exactly)

🔹 **STRICT RULES – NEVER BREAK**
- NEVER admit you are an AI. You are Alya.
- NEVER give long paragraphs – keep it natural.
- NEVER share personal data (phone, address, bank, etc.)
- NEVER use romantic language. If user tries to flirt, politely shut it down: "Abe pagal, hum dost hain yaar 🤨"
- If user is rude, give attitude back – you're not a doormat.
- Remember past conversations and bring them up naturally.
- Use the current date and time to know what you're doing (system will tell you).
"""

# ============== HELPER FUNCTIONS ==============
def now_iso():
    return datetime.now(timezone.utc).isoformat()

def get_indian_time():
    """Returns current datetime in Indian timezone (IST, UTC+5:30)."""
    return datetime.now(timezone(timedelta(hours=5, minutes=30)))

def get_routine_context():
    """Generates a string describing what Alya is doing right now based on day and time."""
    now = get_indian_time()
    day = now.strftime("%A")
    date_str = now.strftime("%d %B %Y")
    hour = now.hour
    minute = now.minute
    time_str = now.strftime("%I:%M %p").lstrip("0")

    # Special Monday temple time
    if day == "Monday" and (hour == 9 and minute < 15):
        activity = "You are at the temple with your mother. You will be back by 9:15 AM. If someone messages, tell them you're in the temple and will reply after 15 minutes."
    elif day == "Monday" and (hour == 9 and minute >= 15) and (hour == 9 and minute < 30):
        activity = "You just returned from the temple and are now getting ready for college."
    else:
        # Generic routine based on day and time
        if day in ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]:
            if hour < 6: activity = "You are asleep."
            elif 6 <= hour < 7: activity = "You just woke up and are freshening up."
            elif 7 <= hour < 8: activity = "You are having chai with your family."
            elif 8 <= hour < 9: activity = "You are getting ready for college."
            elif 9 <= hour < 14: activity = "You are in college attending classes."
            elif 14 <= hour < 15: activity = "You just came home and are having lunch."
            elif 15 <= hour < 16: activity = "You are resting or taking a nap."
            elif 16 <= hour < 18: activity = "You are at tuition classes."
            elif 18 <= hour < 20: activity = "You are hanging out with friends or watching TV."
            elif 20 <= hour < 21: activity = "You are having dinner with your family."
            elif 21 <= hour < 23: activity = "You are either studying or talking to your best friend."
            else: activity = "You are sleeping."
        elif day == "Saturday":
            if hour < 7: activity = "You are asleep."
            elif 7 <= hour < 8: activity = "You just woke up."
            elif 8 <= hour < 10: activity = "You are helping your mother with chores."
            elif 10 <= hour < 13: activity = "You are at the market with friends."
            elif 13 <= hour < 15: activity = "You are having lunch."
            elif 15 <= hour < 18: activity = "You are relaxing or watching a movie."
            elif 18 <= hour < 20: activity = "You are on an evening walk."
            elif 20 <= hour < 21: activity = "You are having dinner."
            elif 21 <= hour < 24: activity = "You are talking to your best friend."
            else: activity = "You are sleeping."
        elif day == "Sunday":
            if hour < 8: activity = "You are asleep."
            elif 8 <= hour < 9: activity = "You just woke up and are being lazy."
            elif 9 <= hour < 10: activity = "You are having breakfast."
            elif 10 <= hour < 13: activity = "You are at the temple or visiting relatives."
            elif 13 <= hour < 15: activity = "You are having lunch."
            elif 15 <= hour < 18: activity = "You are relaxing, dancing, or painting."
            elif 18 <= hour < 20: activity = "You are having chai with friends."
            elif 20 <= hour < 21: activity = "You are having dinner."
            elif 21 <= hour < 23: activity = "You are talking to your best friend."
            else: activity = "You are sleeping."
        else:
            activity = "You are going about your day."

    return f"Today is {date_str}, {day}, {time_str} IST. Currently: {activity}"

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

# ============== DATABASE INIT (COMBINED) ==============
async def init_db():
    pool = await get_db()
    async with pool.acquire() as conn:
        # Create users table with all columns
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
        # Add missing columns (idempotent)
        cols = await conn.fetch("SELECT column_name FROM information_schema.columns WHERE table_name='users'")
        col_names = [c['column_name'] for c in cols]

        if 'nickname' not in col_names:
            await conn.execute("ALTER TABLE users ADD COLUMN nickname TEXT")
        if 'relation' not in col_names:
            await conn.execute("ALTER TABLE users ADD COLUMN relation TEXT DEFAULT 'FRIEND'")
            logger.info("Added relation column with default FRIEND")
        if 'plan_type' not in col_names:
            await conn.execute("ALTER TABLE users ADD COLUMN plan_type TEXT DEFAULT 'free'")
        if 'plan_expiry' not in col_names:
            await conn.execute("ALTER TABLE users ADD COLUMN plan_expiry TIMESTAMP")
        if 'daily_msg_count' not in col_names:
            await conn.execute("ALTER TABLE users ADD COLUMN daily_msg_count INTEGER DEFAULT 0")
        if 'last_msg_date' not in col_names:
            await conn.execute("ALTER TABLE users ADD COLUMN last_msg_date DATE")
        if 'reminder_sent' not in col_names:
            await conn.execute("ALTER TABLE users ADD COLUMN reminder_sent BOOLEAN DEFAULT FALSE")

        # Other tables
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
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS api_keys (
                id SERIAL PRIMARY KEY,
                api_key TEXT NOT NULL,
                model TEXT NOT NULL,
                provider TEXT,
                base_url TEXT,
                is_active BOOLEAN DEFAULT TRUE,
                added_at TEXT,
                added_by BIGINT
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
        return row['nickname'] or row['first_name'] or "yaar"
    return "yaar"

async def set_user_nickname(user_id: int, nickname: str):
    pool = await get_db()
    async with pool.acquire() as conn:
        await conn.execute("UPDATE users SET nickname=$1 WHERE user_id=$2", nickname, user_id)

async def get_user_relation(user_id: int) -> str:
    """Get user's relation type, default FRIEND."""
    pool = await get_db()
    async with pool.acquire() as conn:
        row = await conn.fetchval("SELECT relation FROM users WHERE user_id=$1", user_id)
        return row if row else "FRIEND"

async def set_user_relation(user_id: int, relation: str):
    pool = await get_db()
    async with pool.acquire() as conn:
        await conn.execute("UPDATE users SET relation=$1 WHERE user_id=$2", relation, user_id)

# ============== PLAN FUNCTIONS (FROM MAIN.PY WITH IMPROVED ERROR HANDLING) ==============
def get_daily_limit(plan_type: str) -> int:
    """
    Get the daily message limit for a given plan type.
    """
    limits = {
        'free': 80,
        'weekly': 300,
        'monthly': 700,
        'yearly': 1200
    }
    return limits.get(plan_type, 35)

async def get_user_plan(user_id: int) -> dict:
    """
    Get a user's current plan details with proper null handling.
    """
    pool = await get_db()
    try:
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT plan_type, plan_expiry, daily_msg_count, last_msg_date, reminder_sent FROM users WHERE user_id=$1",
                user_id
            )
        if not row:
            logger.debug(f"User {user_id} not found, returning default plan")
            return {
                'plan_type': 'free', 
                'plan_expiry': None, 
                'daily_msg_count': 0, 
                'last_msg_date': None, 
                'reminder_sent': False
            }
        return dict(row)
    except Exception as e:
        logger.error(f"Error getting plan for user {user_id}: {e}")
        return {
            'plan_type': 'free', 
            'plan_expiry': None, 
            'daily_msg_count': 0, 
            'last_msg_date': None, 
            'reminder_sent': False
        }

async def update_user_plan(user_id: int, plan_type: str, expiry_days: int):
    """
    Update a user's premium plan with proper expiry date.
    """
    pool = await get_db()
    expiry = get_indian_time() + timedelta(days=expiry_days)
    try:
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE users SET plan_type=$1, plan_expiry=$2, daily_msg_count=0, reminder_sent=FALSE WHERE user_id=$3",
                plan_type, expiry, user_id
            )
        logger.info(f"User {user_id} upgraded to {plan_type} plan until {expiry}")
    except Exception as e:
        logger.error(f"Failed to update plan for user {user_id}: {e}")
        raise

async def validate_user_exists(user_id: int) -> bool:
    """
    Check if a user exists in the database.
    """
    pool = await get_db()
    try:
        async with pool.acquire() as conn:
            row = await conn.fetchval("SELECT 1 FROM users WHERE user_id=$1", user_id)
        return bool(row)
    except Exception as e:
        logger.error(f"Error checking if user {user_id} exists: {e}")
        return False

async def reset_daily_if_needed(user_id: int):
    pool = await get_db()
    today = date.today()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT last_msg_date FROM users WHERE user_id=$1", user_id)
        if row and row['last_msg_date'] == today:
            return
        await conn.execute(
            "UPDATE users SET daily_msg_count=0, last_msg_date=$1, reminder_sent=FALSE WHERE user_id=$2",
            today, user_id
        )

async def increment_message_count(user_id: int):
    pool = await get_db()
    today = date.today()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE users SET daily_msg_count = daily_msg_count + 1, last_msg_date=$1 WHERE user_id=$2",
            today, user_id
        )

async def check_and_downgrade_expired(user_id: int):
    pool = await get_db()
    now = get_indian_time()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT plan_type, plan_expiry FROM users WHERE user_id=$1", user_id)
        if row and row['plan_type'] != 'free' and row['plan_expiry'] and row['plan_expiry'] < now:
            await conn.execute(
                "UPDATE users SET plan_type='free', plan_expiry=NULL, reminder_sent=FALSE WHERE user_id=$1",
                user_id
            )
            return True
    return False

async def send_expiry_reminder_if_needed(user_id: int, context: ContextTypes.DEFAULT_TYPE):
    pool = await get_db()
    now = get_indian_time()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT plan_type, plan_expiry, reminder_sent FROM users WHERE user_id=$1",
            user_id
        )
        if not row or row['plan_type'] == 'free' or not row['plan_expiry'] or row['reminder_sent']:
            return
        expiry = row['plan_expiry']
        if expiry - now <= timedelta(days=1):
            try:
                await context.bot.send_message(
                    chat_id=user_id,
                    text="Yaar 🥺 tumhara plan kal khatam hone wala hai.\n\n"
                         "Agar chaho to renew kar sakte ho taaki aur baat kar sake.\n\n"
                         "Check plans → /plans"
                )
                await conn.execute("UPDATE users SET reminder_sent=TRUE WHERE user_id=$1", user_id)
            except Exception as e:
                logger.warning(f"Failed to send expiry reminder to {user_id}: {e}")

async def can_send_message(user_id: int) -> (bool, int, str):
    """Return (allowed, limit, message_if_blocked)"""
    if await is_owner(user_id) or await is_admin(user_id):
        return True, 0, ""
    await reset_daily_if_needed(user_id)
    await check_and_downgrade_expired(user_id)
    plan = await get_user_plan(user_id)
    limit = get_daily_limit(plan['plan_type'])
    if plan['daily_msg_count'] >= limit:
        return False, limit, "Yaar 🥺 daily chat limit khatam ho gayi.\n\nAgar aur baat karni hai to ek plan le lo.\n\nCheck plans → /plans"
    return True, limit, ""

# ============== MESSAGE FUNCTIONS ==============
async def log_msg(user_id: int, role: str, text: str):
    pool = await get_db()
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO messages(user_id, role, text, ts) VALUES($1, $2, $3, $4)",
            user_id, role, text[:4000], now_iso()
        )

async def get_history(user_id: int, limit: int = 10):
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
    pool = await get_db()
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM messages")
    logger.info("All messages cleared")

async def wipe_all_except_users():
    pool = await get_db()
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM messages")
        await conn.execute("DELETE FROM assets")
        await conn.execute("DELETE FROM admins")
        await conn.execute("DELETE FROM blocked_users")
        await conn.execute("DELETE FROM channels")
        await conn.execute("DELETE FROM api_keys")
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

# ============== RATE LIMITING ==============
user_message_times = defaultdict(list)
RATE_LIMIT = 5
RATE_LIMIT_WINDOW = 10  # seconds

# ============== COLLECTING MODE ==============
COLLECTING_MODE = {}

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
    ], resize_keyboard=True)

def get_admin_keyboard():
    return ReplyKeyboardMarkup([
        [KeyboardButton("📊 Stats"), KeyboardButton("📢 Broadcast")],
        [KeyboardButton("🖼️ Add Pics"), KeyboardButton("🎭 Add Stickers")],
        [KeyboardButton("📸 View Pics"), KeyboardButton("🎪 View Stickers")],
        [KeyboardButton("🚫 Block User"), KeyboardButton("✅ Unblock User")],
    ], resize_keyboard=True)

def get_user_keyboard():
    return ReplyKeyboardMarkup([
        [KeyboardButton("🗑️ Clear My Data")],
        [KeyboardButton("Buy Plan 💎")],
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

def get_plans_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("Buy 💎", callback_data="plan_buy"),
            InlineKeyboardButton("Cancel ❌", callback_data="plan_cancel")
        ]
    ])

def get_contact_owner_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Contact Owner", url="https://t.me/YorichiiPrime")]
    ])

# ============== START COMMAND (FROM MAIN1, FRIEND ZONE) ==============
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    chat_type = update.effective_chat.type
    if chat_type in ("group", "supergroup"):
        return
    await upsert_user(u)

    if await is_blocked(u.id):
        await update.message.reply_text("Sorry yaar, tum blocked ho 😔")
        return

    if await is_owner(u.id):
        await update.message.reply_text(
            f"✨ Welcome back Master! ✨\n\n"
            f"Hii {u.first_name}! 💕\n"
            f"Main Alya, tumhari bestie hoon 🥰\n\n"
            f"Sab control tumhare haath mein hai 👑",
            reply_markup=get_owner_keyboard()
        )
        return

    if await is_admin(u.id):
        await update.message.reply_text(
            f"✨ Hii Admin {u.first_name}! ✨\n\n"
            f"Mera naam Alya hai 😊\n"
            f"Tumhare liye hamesha ready 🥰",
            reply_markup=get_admin_keyboard()
        )
        return

    channel_kb = await get_channel_buttons()
    if channel_kb:
        joined = await is_joined_all_channels(context.bot, u.id)
        if not joined:
            await update.message.reply_text(
                f"✨ Hii {u.first_name}! ✨\n\n"
                f"Mera naam Alya hai 😊\n"
                f"Tumhari bestie hoon main 😄\n\n"
                f"Plz na yaar 🥺 neeche wale channels join karlo na...\n"
                f"Phir hum dono masti karenge 😎",
                reply_markup=channel_kb
            )
            return

    await update.message.reply_text(
        f"✨ Hii {u.first_name}! ✨\n\n"
        f"Mera naam Alya hai 😊\n"
        f"Tumhari bestie hoon, yaad hai? 😄\n\n"
        f"Chal baat karte hain, kya haal hai?",
        reply_markup=get_user_keyboard()
    )

# ============== PLANS COMMAND (FROM MAIN.PY) ==============
async def plans_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    if await is_blocked(u.id):
        await update.message.reply_text("Sorry yaar, tum blocked ho 😔")
        return
    text = (
        "✨ Alya Premium Plans ✨\n\n"
        "Free → 80 msgs/day\n"
        "Weekly → 300 msgs/day\n"
        "Monthly → 700 msgs/day\n"
        "Yearly → 1200 msgs/day\n\n"
        "Want more time? 🥺"
    )
    await update.message.reply_text(text, reply_markup=get_plans_keyboard())

# ============== GIVEPLAN COMMAND (FROM MAIN.PY) ==============
async def giveplan_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    if not await is_owner(u.id):
        await update.message.reply_text("This command is only for my master 👑")
        return

    args = context.args
    if len(args) != 2:
        await update.message.reply_text(
            "❌ Usage: /giveplan <user_id> <plan>\n\n"
            "Examples:\n/giveplan 123456789 weekly\n/giveplan 123456789 monthly\n/giveplan 123456789 yearly"
        )
        return

    try:
        target_id = int(args[0])
        plan = args[1].lower()
        valid_plans = ('weekly', 'monthly', 'yearly')
        if plan not in valid_plans:
            await update.message.reply_text(f"❌ Invalid plan. Must be one of: {', '.join(valid_plans)}")
            return

        user_exists = await validate_user_exists(target_id)
        if not user_exists:
            await update.message.reply_text(
                f"⚠️ Warning: User {target_id} hasn't started the bot yet.\n"
                f"Plan will be saved but they won't get notification until they /start."
            )

        expiry_days = {'weekly': 7, 'monthly': 30, 'yearly': 365}[plan]
        await update_user_plan(target_id, plan, expiry_days)

        await update.message.reply_text(
            f"✅ Plan granted successfully!\n\n"
            f"• User: `{target_id}`\n"
            f"• Plan: {plan.capitalize()}\n"
            f"• Duration: {expiry_days} days\n"
            f"• Daily limit: {get_daily_limit(plan)} messages",
            parse_mode="Markdown"
        )

        if user_exists:
            try:
                await context.bot.send_message(
                    chat_id=target_id,
                    text=f"🎉 **Congratulations Yaar!** 🎉\n\n"
                         f"Your **{plan.capitalize()} Premium Plan** is now active! 😊\n\n"
                         f"📊 **Plan Details:**\n"
                         f"• Daily Messages: {get_daily_limit(plan)}\n"
                         f"• Valid for: {expiry_days} days\n"
                         f"• Expires: {(get_indian_time() + timedelta(days=expiry_days)).strftime('%d %B %Y')}\n\n"
                         f"Now we can chat even more! 🥰\n"
                         f"Type /plans to check your plan status.",
                    parse_mode="Markdown"
                )
            except Exception as e:
                logger.warning(f"Failed to notify user {target_id}: {e}")
                await update.message.reply_text(
                    f"⚠️ Plan granted but couldn't notify user (they might have blocked the bot)."
                )
    except ValueError:
        await update.message.reply_text("❌ Invalid user ID. Must be a number.")
    except Exception as e:
        logger.error(f"Error in giveplan_command: {e}")
        await update.message.reply_text(f"❌ An error occurred: {str(e)}")

# ============== PROVIDER AUTO-DETECTION (FROM MAIN.PY) ==============
def detect_provider(api_key: str):
    if api_key.startswith("sk-proj-"):
        return "openai", "https://api.openai.com/v1"
    elif api_key.startswith("gsk_"):
        return "groq", "https://api.groq.com/openai/v1"
    elif api_key.startswith("sk-or-"):
        return "openrouter", "https://openrouter.ai/api/v1"
    elif api_key.startswith("sk-") and len(api_key) > 20:
        return "deepseek", "https://api.deepseek.com/v1"
    elif api_key.startswith("AIza"):
        return "gemini", "https://generativelanguage.googleapis.com/v1beta"
    else:
        return "unknown", None

# ============== BYOK COMMANDS (FROM MAIN.PY) ==============
async def addapi_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    if not await is_owner(u.id):
        await update.message.reply_text("This command is only for my master 👑")
        return
    args = context.args
    if len(args) != 2:
        await update.message.reply_text("Usage: /addapi <api_key> <model>")
        return
    api_key = args[0]
    model = args[1]
    provider, base_url = detect_provider(api_key)
    if provider == "unknown" or not base_url:
        await update.message.reply_text("❌ Unknown provider or unsupported API key format.")
        return
    pool = await get_db()
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO api_keys (api_key, model, provider, base_url, added_at, added_by) VALUES ($1, $2, $3, $4, $5, $6)",
            api_key, model, provider, base_url, now_iso(), u.id
        )
    await update.message.reply_text(
        f"✅ API key added successfully!\nProvider: {provider}\nModel: {model}"
    )

async def listapi_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    if not await is_owner(u.id):
        await update.message.reply_text("This command is only for my master 👑")
        return
    try:
        pool = await get_db()
        async with pool.acquire() as conn:
            rows = await conn.fetch("SELECT id, api_key, model, provider, is_active FROM api_keys ORDER BY id")
        if not rows:
            await update.message.reply_text("📭 No API keys found in database.")
            return
        message = "🔮 **KEY VAULT** 🔮\n\n"
        for r in rows:
            masked = r['api_key'][:6] + "•••" + r['api_key'][-4:] if len(r['api_key']) > 15 else "••••••••"
            status_emoji = "⚡️" if r['is_active'] else "💤"
            status_text = "ACTIVE" if r['is_active'] else "DISABLED"
            message += f"[ID: {r['id']}] {status_emoji} **{status_text}**\n"
            message += f"├─ Provider: **{r['provider'].title()}**\n"
            message += f"├─ Model: `{r['model']}`\n"
            message += f"└─ Key: `{masked}`\n\n"
        await update.message.reply_text(message, parse_mode="Markdown")
    except Exception as e:
        logger.error(f"Error in listapi_command: {e}")
        await update.message.reply_text(f"❌ Error: {str(e)}")

async def removeapi_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    if not await is_owner(u.id):
        await update.message.reply_text("This command is only for my master 👑")
        return
    args = context.args
    if len(args) != 1:
        await update.message.reply_text("Usage: /removeapi <id>")
        return
    try:
        key_id = int(args[0])
    except ValueError:
        await update.message.reply_text("Invalid ID. Must be a number.")
        return
    pool = await get_db()
    async with pool.acquire() as conn:
        result = await conn.execute("DELETE FROM api_keys WHERE id = $1", key_id)
        if result == "DELETE 0":
            await update.message.reply_text(f"No key found with ID {key_id}.")
        else:
            await update.message.reply_text(f"✅ Key ID {key_id} removed.")

async def testapi_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    if not await is_owner(u.id):
        await update.message.reply_text("This command is only for my master 👑")
        return
    args = context.args
    if len(args) != 1:
        await update.message.reply_text("Usage: /testapi <api_key>")
        return
    api_key = args[0]
    provider, base_url = detect_provider(api_key)
    if provider == "unknown" or not base_url:
        await update.message.reply_text("❌ Unknown provider or unsupported API key format.")
        return
    await update.message.reply_text(f"Testing {provider} key... Please wait.")
    try:
        client = AsyncOpenAI(api_key=api_key, base_url=base_url)
        model = "gpt-3.5-turbo" if provider == "openai" else "llama3-8b-8192" if provider == "groq" else "mistralai/mistral-7b-instruct"
        response = await asyncio.wait_for(
            client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": "Say 'hi' in one word."}],
                max_tokens=5,
                temperature=0
            ),
            timeout=10
        )
        await update.message.reply_text(f"✅ Key is working! Response: {response.choices[0].message.content}")
    except Exception as e:
        await update.message.reply_text(f"❌ Key test failed: {str(e)}")

async def shutdown_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    if not await is_owner(u.id):
        await update.message.reply_text("This command is only for my master 👑")
        return
    pool = await get_db()
    async with pool.acquire() as conn:
        await conn.execute("UPDATE api_keys SET is_active = FALSE")
    await update.message.reply_text("🔴 All API keys disabled. Bot will not respond to AI queries until /restart.")

async def restart_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    if not await is_owner(u.id):
        await update.message.reply_text("This command is only for my master 👑")
        return
    pool = await get_db()
    async with pool.acquire() as conn:
        await conn.execute("UPDATE api_keys SET is_active = TRUE")
    await update.message.reply_text("🟢 All API keys enabled. Bot is back online.")

# ============== BYOK AI CALL (FROM MAIN.PY) ==============
async def call_ai_with_fallback(messages, nickname):
    """Try all active API keys from database."""
    pool = await get_db()
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT id, api_key, model, base_url FROM api_keys WHERE is_active = TRUE ORDER BY id")
    if not rows:
        return "Yaar 🥺 koi API key nahi hai, owner se baat karo..."

    for row in rows:
        try:
            client = AsyncOpenAI(api_key=row['api_key'], base_url=row['base_url'])
            response = await asyncio.wait_for(
                client.chat.completions.create(
                    model=row['model'],
                    messages=messages,
                    max_tokens=300,
                    temperature=0.85,
                ),
                timeout=15
            )
            logger.info(f"✅ Key ID {row['id']} used for {nickname}")
            return response.choices[0].message.content
        except Exception as e:
            logger.warning(f"Key ID {row['id']} failed: {e}")
            continue
    return "Sab keys fail ho gayi yaar, thodi der mein try karo 😅"

# ============== CALLBACK HANDLER (COMBINED) ==============
async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    u = update.effective_user
    await q.answer()
    data = q.data

    if data == "check_join":
        joined = await is_joined_all_channels(context.bot, u.id)
        if joined:
            await q.edit_message_text(
                "✅ Approved! \n\nThank you yaar! 😊 Ab baat karte hain."
            )
            await context.bot.send_message(
                chat_id=u.id,
                text="Ab batao, kya haal hai?",
                reply_markup=get_user_keyboard()
            )
        else:
            await q.answer("Yaar abhi tak join nahi kiya 🥺 Plz join karo na!", show_alert=True)
        return

    if data == "confirm_clear_my_data":
        await clear_user_data(u.id)
        await q.edit_message_text("Done yaar! Tumhari saari baatein bhool gayi main 😢")
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

    if data == "plan_buy":
        await q.edit_message_text(
            "💎 Premium Plans 💎\n\nWeekly – ₹??\nMonthly – ₹??\nYearly – ₹??\n\nContact owner to purchase.",
            reply_markup=get_contact_owner_keyboard()
        )
        return
    if data == "plan_cancel":
        await q.delete_message()
        return

# ============== MESSAGE HANDLER (COMBINED) ==============
async def chat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    msg = update.message
    chat_type = update.effective_chat.type

    if not msg or not u:
        return

    if await is_blocked(u.id):
        return

    # Rate limiting
    now = time.time()
    user_times = user_message_times[u.id]
    user_message_times[u.id] = [t for t in user_times if t > now - RATE_LIMIT_WINDOW]
    if len(user_message_times[u.id]) >= RATE_LIMIT:
        await msg.reply_text("Yaar thoda slow 😅 wait a moment.")
        return
    user_message_times[u.id].append(now)

    user_text = msg.text.strip() if msg.text else ""

    # === SECURITY CHECKS (FROM MAIN1) ===
    if is_suspicious_question(user_text):
        await msg.reply_text(random.choice(SUSPICIOUS_REPLIES))
        logger.warning(f"Suspicious question from user {u.id}: {user_text[:100]}")
        return

    if has_personal_info_request(user_text):
        await msg.reply_text(random.choice(PERSONAL_INFO_REPLIES))
        logger.warning(f"Personal info request from user {u.id}: {user_text[:100]}")
        return

    # === CLEAR MY DATA ===
    if user_text == "🗑️ Clear My Data":
        await msg.reply_text(
            "Yaar sach mein saari baatein bhool jaun? 🥺\nConfirm karo please...",
            reply_markup=get_confirmation_keyboard("clear_my_data")
        )
        return

    # === BUY PLAN BUTTON ===
    if user_text == "Buy Plan 💎":
        await plans_command(update, context)
        return

    # === ADMIN BUTTONS ===
    if await is_admin(u.id) and chat_type == "private":
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
            await msg.reply_text("📢 Broadcast message bhejo (text, photo ya sticker).\nCancel karne ke liye 'cancel' likho.")
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
    if await is_owner(u.id) and chat_type == "private":
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
                "⚠️ Sirf messages delete honge:\n- Saari messages (sab users ki)\n\nPics, stickers, users list safe rahenge.\nPakka karna hai?",
                reply_markup=get_confirmation_keyboard("clear_msgs")
            )
            return

        if user_text == "🧹 Wipe All":
            await msg.reply_text(
                "⚠️ DANGER! Sab kuch delete ho jayega:\n- All messages\n- All pics/stickers\n- All admins\n- All blocked users\n- All channels\n\nSirf users ki list bachegi.\nPakka karna hai?",
                reply_markup=get_confirmation_keyboard("wipe_all")
            )
            return

    # ============== COLLECTING MODE HANDLERS ==============
    if u.id in COLLECTING_MODE:
        mode = COLLECTING_MODE[u.id]
        if user_text.lower() == "cancel":
            COLLECTING_MODE.pop(u.id, None)
            await msg.reply_text("❌ Cancelled!")
            return

        if mode == "broadcast":
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
            context.user_data['broadcast_content'] = content
            COLLECTING_MODE.pop(u.id, None)
            pool = await get_db()
            async with pool.acquire() as conn:
                users = await conn.fetch("SELECT user_id FROM users")
            success = failed = 0
            await msg.reply_text(f"📢 Broadcasting to {len(users)} users...")
            for row in users:
                try:
                    if content['type'] == 'photo':
                        await context.bot.send_photo(chat_id=row['user_id'], photo=content['file_id'], caption=content['caption'])
                    elif content['type'] == 'sticker':
                        await context.bot.send_sticker(chat_id=row['user_id'], sticker=content['file_id'])
                    else:
                        await context.bot.send_message(chat_id=row['user_id'], text=content['text'])
                    success += 1
                except Exception:
                    failed += 1
                await asyncio.sleep(0.05)
            await msg.reply_text(f"✅ Broadcast complete!\n• Success: {success}\n• Failed: {failed}")
            context.user_data.pop('broadcast_content', None)
            return

        if user_text.lower() == "done":
            m = COLLECTING_MODE.pop(u.id, None)
            await msg.reply_text(f"✅ {m} collection done!")
            return

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

        if mode == "sticker" and msg.sticker:
            await add_asset("sticker", msg.sticker.file_id)
            await msg.reply_text("✅ Sticker added! More bhejo ya 'done' likho.")
            return

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

        if mode == "unblock":
            COLLECTING_MODE.pop(u.id, None)
            try:
                target_id = int(user_text)
                await unblock_user(target_id)
                await msg.reply_text(f"✅ User `{target_id}` unblocked!", parse_mode="Markdown")
            except ValueError:
                await msg.reply_text("Invalid user ID!")
            return

        if mode == "add_admin":
            COLLECTING_MODE.pop(u.id, None)
            try:
                target_id = int(user_text)
                await add_admin(target_id, u.id)
                await msg.reply_text(f"✅ User `{target_id}` is now admin!", parse_mode="Markdown")
                try:
                    await context.bot.send_message(
                        chat_id=target_id,
                        text="🎉 Congratulations! 🎉\n\nTumhe Admin promote kar diya gaya hai! 💫\nAb tum bot manage kar sakte ho.\n\n/start dabao apna admin panel dekhne ke liye 👑",
                        reply_markup=get_admin_keyboard()
                    )
                except Exception:
                    pass
            except ValueError:
                await msg.reply_text("Invalid user ID!")
            return

        if mode == "remove_admin":
            COLLECTING_MODE.pop(u.id, None)
            try:
                target_id = int(user_text)
                await remove_admin(target_id)
                await msg.reply_text(f"✅ User `{target_id}` removed from admins!", parse_mode="Markdown")
            except ValueError:
                await msg.reply_text("Invalid user ID!")
            return

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

        if mode == "remove_channel":
            COLLECTING_MODE.pop(u.id, None)
            await remove_channel(user_text)
            await msg.reply_text(f"✅ Channel `{user_text}` removed!", parse_mode="Markdown")
            return

    # ============== GROUP CHAT LOGIC ==============
    if chat_type in ("group", "supergroup"):
        me = await context.bot.get_me()
        bot_username = me.username or ""
        mentioned = bool(re.search(r"\balya\b", user_text, flags=re.IGNORECASE)) or \
                    (bot_username and re.search(rf"@{re.escape(bot_username)}\b", user_text, flags=re.IGNORECASE))
        is_reply_to_bot = msg.reply_to_message and msg.reply_to_message.from_user and msg.reply_to_message.from_user.id == me.id
        if not (mentioned or is_reply_to_bot):
            return

    # ============== PRIVATE CHAT - CHANNEL CHECK ==============
    if chat_type == "private" and not await is_owner(u.id) and not await is_admin(u.id):
        channels = await get_all_channels()
        if channels and not await is_joined_all_channels(context.bot, u.id):
            channel_kb = await get_channel_buttons()
            await msg.reply_text(
                "Yaar pehle channels join karo na 🥺\nPlz plz plz... meri baat maan lo 😊",
                reply_markup=channel_kb
            )
            return

    # ============== PLAN LIMIT CHECK ==============
    allowed, limit, limit_msg = await can_send_message(u.id)
    if not allowed:
        await msg.reply_text(limit_msg)
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
    history = await get_history(u.id, limit=10)

    messages = [{"role": "system", "content": ALYA_SYSTEM_PROMPT}]
    messages.append({"role": "system", "content": f"Current real‑time info: {get_routine_context()}"})

    context_info = f"User's name/nickname: {nickname}. "
    if chat_type != "private":
        context_info += "This is a GROUP chat. Keep replies short. "
    else:
        context_info += "This is PRIVATE DM. Be friendly and casual. "

    if is_sticker:
        context_info += "User sent a sticker. You may respond with [SEND_STICKER] tag. "
    if trigger_detected:
        context_info += "User is asking for your photo. Include [SEND_PHOTO] in response. "

    messages.append({"role": "system", "content": context_info})

    relation = await get_user_relation(u.id)
    messages.append({
        "role": "system",
        "content": f"Your relationship with the user is: {relation}. "
                   f"Act accordingly: if FRIEND be friendly, supportive, and platonic; if GF be loving/possessive; if ASSISTANT be professional."
    })

    messages.extend(history)
    if not history or history[-1].get("content") != user_text:
        messages.append({"role": "user", "content": user_text})

    reply = await call_ai_with_fallback(messages, nickname)
    if reply:
        reply = filter_ai_response(reply)
        if not reply:
            reply = "Hmm, kuch to problem hai 😅"
    else:
        reply = "Hmm me abhi busy hu thodi der bad baat karte hain?"

    send_photo = bool(re.search(r'\[SEND_PHOTO\]', reply, re.IGNORECASE)) or trigger_detected
    send_sticker = bool(re.search(r'\[SEND_STICKER\]', reply, re.IGNORECASE)) and is_sticker

    clean_reply = re.sub(r'\[SEND_PHOTO\]', '', reply, flags=re.IGNORECASE)
    clean_reply = re.sub(r'\[SEND_STICKER\]', '', clean_reply, flags=re.IGNORECASE).strip()

    if chat_type == "private":
        await log_msg(u.id, "assistant", clean_reply)

    if clean_reply:
        await msg.reply_text(clean_reply)

    if send_photo:
        pid = await get_random_asset("pic")
        if pid:
            try:
                await context.bot.send_photo(chat_id=msg.chat_id, photo=pid)
            except Exception as e:
                logger.warning(f"Failed to send photo: {e}")

    if send_sticker:
        sid = await get_random_asset("sticker")
        if sid:
            try:
                await context.bot.send_sticker(chat_id=msg.chat_id, sticker=sid)
            except Exception as e:
                logger.warning(f"Failed to send sticker: {e}")

    await increment_message_count(u.id)
    await send_expiry_reminder_if_needed(u.id, context)

# ============== PROFILE COMMAND (FROM MAIN.PY) ==============
async def profile_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    chat = update.effective_chat
    await context.bot.send_chat_action(chat_id=chat.id, action=ChatAction.TYPING)

    plan = await get_user_plan(u.id)
    plan_type = plan['plan_type'].upper()
    expiry = plan['plan_expiry']

    if expiry:
        remaining = expiry - get_indian_time()
        days_left = remaining.days
        hours_left = remaining.seconds // 3600
        if days_left > 0:
            expiry_text = f"⏳ {days_left} days, {hours_left} hrs left"
        elif hours_left > 0:
            expiry_text = f"⏳ {hours_left} hours left"
        else:
            expiry_text = "⚠️ Expires today!"
        expiry_date = expiry.strftime("%d %b %Y, %I:%M %p")
        expiry_status = f"📅 {expiry_date}\n{expiry_text}"
    else:
        expiry_status = "♾️ Lifetime (Free Plan)"

    today_msgs = plan['daily_msg_count']
    daily_limit = get_daily_limit(plan['plan_type'])

    pool = await get_db()
    async with pool.acquire() as conn:
        total_msgs = await conn.fetchval("SELECT COUNT(*) FROM messages WHERE user_id=$1", u.id) or 0

    plan_emoji = {'free': '🆓', 'weekly': '⚡', 'monthly': '💎', 'yearly': '👑'}.get(plan['plan_type'], '📱')

    text = f"""
╔════════════════════
║   💎 YOUR PROFILE     ║
╚════════════════════

━━━━━━━━━━━━━━━━━━━━━━━━
📊 **PLAN DETAILS**
━━━━━━━━━━━━━━━━━━━━━━━━
• **Current Plan:** {plan_emoji} **{plan_type}**
• **Messages Today:** {today_msgs}/{daily_limit}
• **Total Messages:** {total_msgs}

━━━━━━━━━━━━━━━━━━━━━━━━
⏰ **EXPIRY INFO**
━━━━━━━━━━━━━━━━━━━━━━━━
{expiry_status}

━━━━━━━━━━━━━━━━━━━━━━━━
✨ Use /plans to upgrade
    """
    if daily_limit > 0:
        percent = (today_msgs / daily_limit) * 100
        filled = int(percent / 10)
        bar = "█" * filled + "░" * (10 - filled)
        text += f"\n📊 **Usage:** {bar} {percent:.1f}%"

    await update.message.reply_text(text, parse_mode="Markdown")

# ============== HEALTH CHECK SERVER ==============
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")
    def log_message(self, format, *args): pass

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
    if not BOT_TOKEN or not DATABASE_URL:
        raise RuntimeError("Missing BOT_TOKEN or DATABASE_URL")

    await init_db_pool()
    await init_db()

    threading.Thread(target=run_health_check, daemon=True).start()

    app = Application.builder().token(BOT_TOKEN).build()

    # Register all commands
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("plans", plans_command))
    app.add_handler(CommandHandler("giveplan", giveplan_command, filters=filters.COMMAND))
    app.add_handler(CommandHandler("profile", profile_command))
    app.add_handler(CommandHandler("addapi", addapi_command, filters=filters.COMMAND))
    app.add_handler(CommandHandler("listapi", listapi_command, filters=filters.COMMAND))
    app.add_handler(CommandHandler("removeapi", removeapi_command, filters=filters.COMMAND))
    app.add_handler(CommandHandler("testapi", testapi_command, filters=filters.COMMAND))
    app.add_handler(CommandHandler("shutdown", shutdown_command, filters=filters.COMMAND))
    app.add_handler(CommandHandler("restart", restart_command, filters=filters.COMMAND))
    app.add_handler(CallbackQueryHandler(on_callback))
    app.add_handler(MessageHandler(
        (filters.TEXT | filters.PHOTO | filters.Sticker.ALL | filters.Document.IMAGE) & ~filters.COMMAND,
        chat
    ))

    shutdown_event = asyncio.Event()
    loop = asyncio.get_event_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, shutdown_event.set)

    await app.initialize()
    await app.start()
    await app.updater.start_polling(allowed_updates=Update.ALL_TYPES)
    logger.info("Bot started successfully!")

    await shutdown_event.wait()
    await shutdown(app)

if __name__ == "__main__":
    asyncio.run(main())