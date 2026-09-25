import os
import sqlite3
import asyncio
import random
import time
from datetime import datetime, timedelta

from rubka import Robot, Message

# ============================================================
# روبیکا گیم سنتر | نسخه تک‌فایلی
# تمام منطق ربات در همین فایل است.
# ============================================================

TOKEN = os.getenv("RUBIKA_TOKEN", "").strip()
OWNER_ID = os.getenv("OWNER_ID", "").strip()
BOT_NAME = os.getenv("BOT_NAME", "گیم‌سنتر پارسا").strip()
DB_FILE = os.getenv("DB_FILE", "game_center.db")

if not TOKEN:
    raise RuntimeError("متغیر RUBIKA_TOKEN در Railway تنظیم نشده است.")
if not OWNER_ID:
    raise RuntimeError("متغیر OWNER_ID در Railway تنظیم نشده است.")

bot = Robot(token=TOKEN)
db = sqlite3.connect(DB_FILE, check_same_thread=False)
db.row_factory = sqlite3.Row
db.execute("PRAGMA journal_mode=WAL")

db.executescript("""
CREATE TABLE IF NOT EXISTS users(
    id TEXT PRIMARY KEY,
    name TEXT DEFAULT 'کاربر',
    coins INTEGER DEFAULT 1000,
    xp INTEGER DEFAULT 0,
    level INTEGER DEFAULT 1,
    wins INTEGER DEFAULT 0,
    losses INTEGER DEFAULT 0,
    games INTEGER DEFAULT 0,
    streak INTEGER DEFAULT 0,
    last_daily TEXT DEFAULT '',
    banned INTEGER DEFAULT 0,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS rooms(
    chat_id TEXT PRIMARY KEY,
    game TEXT DEFAULT '',
    status TEXT DEFAULT 'idle',
    host_id TEXT DEFAULT '',
    data TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS achievements(
    user_id TEXT,
    code TEXT,
    PRIMARY KEY(user_id, code)
);
""")
db.commit()

# ---------- ابزارهای عمومی ----------

def q(sql, args=(), one=False):
    cur = db.execute(sql, args)
    row = cur.fetchone() if one else cur.fetchall()
    db.commit()
    return row

def user(uid, name="کاربر"):
    row = q("SELECT * FROM users WHERE id=?", (uid,), True)
    if not row:
        q("INSERT INTO users(id,name) VALUES(?,?)", (uid, name or "کاربر"))
        row = q("SELECT * FROM users WHERE id=?", (uid,), True)
    elif name and row["name"] != name:
        q("UPDATE users SET name=? WHERE id=?", (name, uid))
        row = q("SELECT * FROM users WHERE id=?", (uid,), True)
    return row

def add_xp(uid, amount):
    u = user(uid)
    xp = u["xp"] + amount
    level = max(1, xp // 500 + 1)
    q("UPDATE users SET xp=?,level=? WHERE id=?", (xp, level, uid))

def coins(uid, amount):
    q("UPDATE users SET coins=MAX(0,coins+?) WHERE id=?", (amount, uid))

def owner(uid):
    return str(uid) == str(OWNER_ID)

def name_of(message):
    return getattr(message, "sender_id", "") and (getattr(message, "sender_name", None) or "کاربر")

def text_of(message):
    return (getattr(message, "text", "") or "").strip()

def chat_of(message):
    return str(getattr(message, "chat_id", ""))

def uid_of(message):
    return str(getattr(message, "sender_id", ""))

def menu():
    return (
        "🎮 گیم‌سنتر پارسا\n\n"
        "🕹 بازی‌ها\n"
        "• /مافیا\n• /گرگینه\n• /مسابقه\n• /کلمه\n• /نبرد\n• /چیستان\n\n"
        "👤 امکانات\n"
        "• /پروفایل\n• /سکه\n• /روزانه\n• /رتبه\n• /فروشگاه\n• /دستاورد\n\n"
        "💡 داخل گپ فقط بنویس «ربات» تا صدات رو بشنوم."
    )

# ---------- بازی‌ها ----------

QUIZ = [
    ("پایتخت ایران چیست؟", ["تهران","تبریز","شیراز","اصفهان"], 0),
    ("بزرگ‌ترین سیاره منظومه شمسی کدام است؟", ["زمین","مریخ","مشتری","زهره"], 2),
    ("زبان اصلی برنامه‌نویسی این ربات چیست؟", ["Python","HTML","CSS","SQL"], 0),
    ("آب در فشار معمولی در چند درجه سانتی‌گراد می‌جوشد؟", ["50","75","100","150"], 2),
]

RIDDLES = [
    ("آن چیست که هرچه بیشتر از آن برداری، بزرگ‌تر می‌شود؟", "چاله"),
    ("آن چیست که پا ندارد ولی راه می‌رود؟", "ساعت"),
    ("چه چیزی کلید دارد ولی قفل ندارد؟", "پیانو"),
]

def mafia_help():
    return (
        "🕵️ مافیا\n\n"
        "برای شروع در گپ بنویس:\n"
        "/شروع_مافیا\n\n"
        "بعد بازیکن‌ها با /ورود وارد می‌شوند.\n"
        "حداقل ۴ بازیکن لازم است.\n"
        "برای پایان ثبت‌نام: /شروع_بازی"
    )

def room(chat):
    r = q("SELECT * FROM rooms WHERE chat_id=?", (chat,), True)
    return r

def set_room(chat, game="", status="idle", host="", data=""):
    q("""INSERT INTO rooms(chat_id,game,status,host_id,data)
         VALUES(?,?,?,?,?)
         ON CONFLICT(chat_id) DO UPDATE SET game=excluded.game,
         status=excluded.status,host_id=excluded.host_id,data=excluded.data""",
      (chat, game, status, host, data))

# ثبت‌نام بازیکنان مافیا در حافظه برنامه
MAFIA_PLAYERS = {}

def mp(chat):
    return MAFIA_PLAYERS.setdefault(chat, {"players": [], "started": False, "roles": {}})

async def start_mafia(message):
    chat = chat_of(message)
    uid = uid_of(message)
    MAFIA_PLAYERS[chat] = {"players":[uid], "started":False, "roles":{}}
    set_room(chat, "mafia", "join", uid)
    await message.reply("🕵️‍♂️ مافیا ساخته شد!\n\nبازیکن‌ها با «/ورود» وارد شوند.\nحداقل ۴ نفر لازم است.\n\n👑 سازنده: " + name_of(message))

async def join_mafia(message):
    chat, uid = chat_of(message), uid_of(message)
    r = mp(chat)
    if r["started"]:
        return await message.reply("⛔ بازی شروع شده است.")
    user(uid, name_of(message))
    if uid not in r["players"]:
        r["players"].append(uid)
    await message.reply(f"🎮 وارد شدی!\nتعداد بازیکنان: {len(r['players'])}")

async def run_mafia(message):
    chat = chat_of(message)
    r = mp(chat)
    if len(r["players"]) < 4:
        return await message.reply("❌ حداقل ۴ بازیکن لازم است.")
    r["started"] = True
    roles = ["مافیا", "دکتر", "کارآگاه"] + ["شهروند"] * (len(r["players"]) - 3)
    random.shuffle(roles)
    r["roles"] = dict(zip(r["players"], roles))
    lines = ["🕵️‍♂️ بازی مافیا شروع شد!", "", "🌙 شب اول فرا رسید.", "نقش هر بازیکن خصوصی است."]
    for uid, role in r["roles"].items():
        try:
            await bot.send_message(uid, f"🎭 نقش تو در مافیا: {role}")
        except Exception:
            pass
    await message.reply("\n".join(lines))

# ---------- هندلر اصلی ----------

@bot.on_message()
async def main_handler(bot_instance, message: Message):
    txt = text_of(message)
    uid = uid_of(message)
    chat = chat_of(message)
    if not uid:
        return

    u = user(uid, name_of(message))
    if u["banned"] and not owner(uid):
        return await message.reply("🚫 دسترسی شما به ربات مسدود است.")

    # شناخت مالک
    if txt in ("ربات", "bot", "هی ربات", "سلام ربات"):
        if owner(uid):
            return await message.reply(
                "👑 سلام پارسا!\n"
                "خودت اومدی رئیس 😎🔥\n"
                "گیم‌سنتر آماده‌ست. بگو چه کاری داری؟"
            )
        return await message.reply(
            "🤖 سلام رفیق! در خدمتم 😎🔥\n"
            "بیا یه بازی بزنیم باهم!\n\n"
            "🎮 /بازی‌ها"
        )

    if txt in ("/start", "شروع", "منو", "/بازی‌ها"):
        return await message.reply(menu())

    if txt == "/پروفایل":
        return await message.reply(
            f"👤 پروفایل {u['name']}\n\n"
            f"⭐ سطح: {u['level']}\n"
            f"✨ XP: {u['xp']}\n"
            f"🪙 سکه: {u['coins']}\n"
            f"🎮 بازی‌ها: {u['games']}\n"
            f"🏆 برد: {u['wins']}\n"
            f"💔 باخت: {u['losses']}\n"
            f"🔥 استریک: {u['streak']}"
        )

    if txt == "/سکه":
        return await message.reply(f"🪙 موجودی شما: {u['coins']:,} سکه")

    if txt == "/روزانه":
        today = datetime.now().date().isoformat()
        if u["last_daily"] == today:
            return await message.reply("🎁 جایزه امروزت رو قبلاً گرفتی.")
        streak = u["streak"] + 1
        reward = 100 + min(streak, 10) * 25
        q("UPDATE users SET coins=coins+?,streak=?,last_daily=? WHERE id=?",
          (reward, streak, today, uid))
        add_xp(uid, 25)
        return await message.reply(
            f"🎁 جایزه روزانه دریافت شد!\n"
            f"🪙 +{reward} سکه\n⭐ +25 XP\n🔥 استریک: {streak}"
        )

    if txt == "/رتبه":
        rows = q("SELECT name,level,xp,coins FROM users ORDER BY xp DESC LIMIT 10")
        out = ["🏆 جدول برترین‌ها\n"]
        for i, x in enumerate(rows, 1):
            out.append(f"{i}. {x['name']} — سطح {x['level']} | {x['xp']} XP")
        return await message.reply("\n".join(out))

    if txt == "/فروشگاه":
        return await message.reply(
            "🛒 فروشگاه\n\n"
            "فعلاً آیتم‌های تزئینی و بوسترهای داخل بازی در حال آماده‌سازی‌اند.\n"
            "🪙 موجودی با /سکه"
        )

    if txt == "/دستاورد":
        return await message.reply(
            "🏅 دستاوردها\n\n"
            "🎮 اولین بازی — بازی اولت را انجام بده\n"
            "🏆 برنده — اولین برد\n"
            "🔥 وفادار — استریک ۷ روزه"
        )

    if txt == "/مافیا":
        return await message.reply(mafia_help())

    if txt == "/شروع_مافیا":
        return await start_mafia(message)

    if txt == "/ورود":
        return await join_mafia(message)

    if txt == "/شروع_بازی":
        return await run_mafia(message)

    if txt == "/گرگینه":
        return await message.reply(
            "🐺 گرگینه\n\n"
            "حالت چندنفره گرگینه آماده توسعه است.\n"
            "برای نسخه کامل، بازیکن‌ها را در یک روم جمع می‌کنیم."
        )

    if txt == "/مسابقه":
        question, opts, ans = random.choice(QUIZ)
        return await message.reply(
            f"🧠 مسابقه\n\n{question}\n\n"
            + "\n".join(f"{i+1}) {x}" for i,x in enumerate(opts))
            + "\n\nجواب را با عدد ۱ تا ۴ بفرست."
        )

    if txt == "/چیستان":
        qtext, answer = random.choice(RIDDLES)
        return await message.reply(f"🧩 چیستان:\n\n{qtext}\n\nجواب را حدس بزن 😎")

    if txt == "/کلمه":
        word = random.choice(["کامپیوتر","روبیکا","مافیا","برنامه‌نویسی","بازی"])
        masked = " _ " * len(word)
        return await message.reply(f"🔤 حدس کلمه\n\nکلمه {len(word)} حرف دارد.\n{masked}")

    if txt == "/نبرد":
        return await message.reply(
            "⚔️ نبرد دو نفره\n\n"
            "برای شروع، یک بازیکن دیگر را به چالش بکش.\n"
            "سیستم مبارزه نوبتی در نسخه بعدی فعال می‌شود."
        )

    # دستورات مالک
    if owner(uid) and txt == "/مالک":
        return await message.reply(
            "👑 پنل مالک\n\n"
            "تو مالک اصلی این ربات هستی.\n"
            "آمار: /آمار\n"
            "پخش همگانی: /پخش متن"
        )

    if owner(uid) and txt == "/آمار":
        total = q("SELECT COUNT(*) c FROM users", one=True)["c"]
        coins_total = q("SELECT COALESCE(SUM(coins),0) c FROM users", one=True)["c"]
        return await message.reply(
            f"📊 آمار گیم‌سنتر\n\n"
            f"👥 کاربران: {total}\n"
            f"🪙 مجموع سکه‌ها: {coins_total:,}\n"
            f"🎮 روم‌های فعال: {len(MAFIA_PLAYERS)}"
        )

    # پاسخ‌های شخصیت ربات
    if txt.lower() in ("سلام", "سلام ربات", "hi", "hello"):
        return await message.reply("سلام رفیق 😎🔥 آماده‌ای یه بازی بزنیم؟ /بازی‌ها")

    if "بازی" in txt.lower() or "بازی" in txt:
        return await message.reply("🎮 پایه‌ام! مافیا، مسابقه، چیستان و چند بازی دیگه داریم. /بازی‌ها")

# اجرای ربات
if __name__ == "__main__":
    print(f"{BOT_NAME} is running...")
    bot.run()
