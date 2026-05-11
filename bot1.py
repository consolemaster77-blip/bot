"""
╔══════════════════════════════════════════════════════╗
║         TELEGRAM BOOKING BOT  •  by Claude           ║
║   Бот для записи на услуги с полной админ-панелью    ║
╚══════════════════════════════════════════════════════╝
"""

import logging
import os
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

# Загружаем .env если есть python-dotenv
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent / ".env")
except ImportError:
    pass

from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup,
    InputMediaPhoto, BotCommand,
)
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, filters, ContextTypes,
)
from telegram.constants import ParseMode

# ═══════════════════════════════════════════════════════
#                      ⚙️ CONFIG
# ═══════════════════════════════════════════════════════
# Все настройки берутся из файла .env рядом с bot.py
# Пример .env:
#   BOT_TOKEN=7123456789:AAHxxxxxxxxxxxxxxxxxxxxxxxxxxx
#   ADMIN_ID=123456789

BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
_admin_id  = os.environ.get("ADMIN_ID", "0")

if not BOT_TOKEN:
    raise SystemExit(
        "❌  BOT_TOKEN не задан.\n"
        "    Создайте файл .env рядом с bot.py и добавьте:\n"
        "    BOT_TOKEN=ваш_токен_от_BotFather"
    )

try:
    ADMIN_ID = int(_admin_id)
except ValueError:
    raise SystemExit(
        "❌  ADMIN_ID задан неверно — должно быть число.\n"
        "    Проверьте файл .env:\n"
        "    ADMIN_ID=123456789"
    )

if ADMIN_ID == 0:
    raise SystemExit(
        "❌  ADMIN_ID не задан.\n"
        "    Узнайте свой ID у @userinfobot и добавьте в .env:\n"
        "    ADMIN_ID=123456789"
    )

# База данных — всегда рядом с bot.py, в папке мастера
DB_PATH = str(Path(__file__).parent / "booking.db")

# ═══════════════════════════════════════════════════════
#                    🗄️ DATABASE
# ═══════════════════════════════════════════════════════

def db():
    return sqlite3.connect(DB_PATH)


def init_db():
    with db() as con:
        c = con.cursor()
        c.executescript("""
        CREATE TABLE IF NOT EXISTS settings (
            key   TEXT PRIMARY KEY,
            value TEXT
        );
        CREATE TABLE IF NOT EXISTS services (
            id     INTEGER PRIMARY KEY AUTOINCREMENT,
            name   TEXT    NOT NULL,
            price  INTEGER NOT NULL,
            active INTEGER DEFAULT 1
        );
        CREATE TABLE IF NOT EXISTS slots (
            id      INTEGER PRIMARY KEY AUTOINCREMENT,
            date    TEXT NOT NULL,
            time    TEXT NOT NULL,
            is_open INTEGER DEFAULT 1,
            UNIQUE(date, time)
        );
        CREATE TABLE IF NOT EXISTS appointments (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id    INTEGER NOT NULL,
            username   TEXT,
            full_name  TEXT,
            service_id INTEGER NOT NULL,
            date       TEXT NOT NULL,
            time       TEXT NOT NULL,
            comment    TEXT,
            phone      TEXT,
            status     TEXT DEFAULT 'pending',
            created_at TEXT NOT NULL,
            reminded   INTEGER DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS portfolio (
            id       INTEGER PRIMARY KEY AUTOINCREMENT,
            file_id  TEXT NOT NULL,
            caption  TEXT,
            added_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS social_links (
            id       INTEGER PRIMARY KEY AUTOINCREMENT,
            platform TEXT NOT NULL UNIQUE,
            url      TEXT NOT NULL,
            enabled  INTEGER DEFAULT 1
        );
        """)

        # Миграция: добавить phone если ещё нет (для существующих БД)
        try:
            c.execute("ALTER TABLE appointments ADD COLUMN phone TEXT")
        except Exception:
            pass

        defaults = {
            "master_name":     "Мастер",
            "master_username": "",
            "bot_description": "Запись на услуги красоты",
            "welcome_text":    "Привет! 👋\nЗдесь ты можешь записаться на любую услугу — быстро и удобно.",
            "confirm_text":    "Ждём тебя! Если появятся вопросы — пиши напрямую 🤍",
            "reminder_hours":  "24,2",
            "remind_enabled":  "1",
            "work_start":      "10:00",
            "work_end":        "20:00",
            "slot_interval":   "60",
            "portfolio_url":   "",
        }
        for k, v in defaults.items():
            c.execute("INSERT OR IGNORE INTO settings VALUES (?,?)", (k, v))

        c.execute("SELECT COUNT(*) FROM services")
        if c.fetchone()[0] == 0:
            c.executemany("INSERT INTO services (name,price) VALUES (?,?)", [
                ("Маникюр",            1500),
                ("Педикюр",            2000),
                ("Маникюр + педикюр",  3000),
                ("Наращивание ногтей", 3500),
            ])


# ─── Settings ────────────────────────────────────────

def get_s(key, default=""):
    with db() as con:
        r = con.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return r[0] if r else default


def put_s(key, value):
    with db() as con:
        con.execute("INSERT OR REPLACE INTO settings VALUES (?,?)", (key, value))


# ─── Services ────────────────────────────────────────

def get_services(all_=False):
    q = "SELECT id,name,price,active FROM services"
    if not all_: q += " WHERE active=1"
    with db() as con: return con.execute(q + " ORDER BY name").fetchall()


def get_service(sid):
    with db() as con:
        return con.execute("SELECT id,name,price FROM services WHERE id=?", (sid,)).fetchone()


def add_service(name, price):
    with db() as con:
        con.execute("INSERT INTO services (name,price) VALUES (?,?)", (name, price))


def toggle_service(sid):
    with db() as con:
        cur = con.execute("SELECT active FROM services WHERE id=?", (sid,)).fetchone()[0]
        con.execute("UPDATE services SET active=? WHERE id=?", (0 if cur else 1, sid))


def delete_service(sid):
    with db() as con:
        con.execute("DELETE FROM services WHERE id=?", (sid,))


# ─── Slots ───────────────────────────────────────────

def generate_work_slots(date):
    start_s  = get_s("work_start",    "10:00")
    end_s    = get_s("work_end",      "20:00")
    interval = int(get_s("slot_interval", "60"))
    cur      = datetime.strptime(f"{date} {start_s}", "%Y-%m-%d %H:%M")
    end      = datetime.strptime(f"{date} {end_s}",   "%Y-%m-%d %H:%M")
    with db() as con:
        while cur < end:
            con.execute(
                "INSERT OR IGNORE INTO slots (date,time,is_open) VALUES (?,?,1)",
                (date, cur.strftime("%H:%M")))
            cur += timedelta(minutes=interval)


def available_dates():
    today  = datetime.now().date()
    result = []
    with db() as con:
        for i in range(14):
            ds = (today + timedelta(days=i)).strftime("%Y-%m-%d")
            n  = con.execute("""
                SELECT COUNT(*) FROM slots s
                WHERE s.date=? AND s.is_open=1
                AND NOT EXISTS (
                    SELECT 1 FROM appointments a
                    WHERE a.date=s.date AND a.time=s.time AND a.status IN ('active','pending')
                )""", (ds,)).fetchone()[0]
            if n: result.append(ds)
    return result


def available_slots(date):
    with db() as con:
        rows = con.execute("""
            SELECT s.time FROM slots s
            WHERE s.date=? AND s.is_open=1
            AND NOT EXISTS (
                SELECT 1 FROM appointments a
                WHERE a.date=s.date AND a.time=s.time AND a.status IN ('active','pending')
            ) ORDER BY s.time""", (date,)).fetchall()
    return [r[0] for r in rows]


def all_slots_for_date(date):
    with db() as con:
        return con.execute(
            "SELECT time,is_open FROM slots WHERE date=? ORDER BY time", (date,)
        ).fetchall()


def toggle_slot(date, time):
    with db() as con:
        r = con.execute(
            "SELECT is_open FROM slots WHERE date=? AND time=?", (date, time)
        ).fetchone()
        if r:
            con.execute(
                "UPDATE slots SET is_open=? WHERE date=? AND time=?",
                (0 if r[0] else 1, date, time))
        else:
            con.execute(
                "INSERT INTO slots (date,time,is_open) VALUES (?,?,1)", (date, time))


def clear_and_regen(date):
    with db() as con:
        con.execute("""DELETE FROM slots WHERE date=? AND time NOT IN (
            SELECT time FROM appointments WHERE date=? AND status IN ('active','pending')
        )""", (date, date))
    generate_work_slots(date)


# ─── Appointments ────────────────────────────────────

def create_appt(user_id, username, full_name, service_id, date, time, comment, phone=""):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with db() as con:
        cur = con.execute("""
            INSERT INTO appointments
            (user_id,username,full_name,service_id,date,time,comment,phone,status,created_at)
            VALUES (?,?,?,?,?,?,?,?,'pending',?)""",
            (user_id, username, full_name, service_id, date, time, comment, phone, now))
        return cur.lastrowid


def user_appts(user_id):
    today = datetime.now().strftime("%Y-%m-%d")
    with db() as con:
        return con.execute("""
            SELECT a.id, s.name, s.price, a.date, a.time, a.comment, a.status
            FROM appointments a JOIN services s ON a.service_id=s.id
            WHERE a.user_id=? AND a.status IN ('active','pending') AND a.date>=?
            ORDER BY a.date, a.time""", (user_id, today)).fetchall()


def all_appts():
    today = datetime.now().strftime("%Y-%m-%d")
    with db() as con:
        return con.execute("""
            SELECT a.id, a.full_name, a.username, s.name, a.date, a.time, a.comment, a.user_id, a.phone, a.status
            FROM appointments a JOIN services s ON a.service_id=s.id
            WHERE a.status IN ('active','pending') AND a.date>=?
            ORDER BY a.status DESC, a.date, a.time""", (today,)).fetchall()


def get_appt(appt_id):
    with db() as con:
        return con.execute("""
            SELECT a.id, a.user_id, a.full_name, a.username, s.name, a.date, a.time, a.comment, a.phone, a.status
            FROM appointments a JOIN services s ON a.service_id=s.id
            WHERE a.id=?""", (appt_id,)).fetchone()


def cancel_appt(appt_id):
    with db() as con:
        con.execute("UPDATE appointments SET status='cancelled' WHERE id=?", (appt_id,))


def approve_appt(appt_id):
    with db() as con:
        con.execute("UPDATE appointments SET status='active' WHERE id=?", (appt_id,))


def pending_reminders():
    if get_s("remind_enabled", "1") != "1": return []
    hours_list = [int(h) for h in get_s("reminder_hours","24,2").split(",") if h.strip().isdigit()]
    now    = datetime.now()
    result = []
    with db() as con:
        for h in hours_list:
            target = now + timedelta(hours=h)
            td     = target.strftime("%Y-%m-%d")
            ws     = (target - timedelta(minutes=15)).strftime("%H:%M")
            we     = (target + timedelta(minutes=15)).strftime("%H:%M")
            rows   = con.execute("""
                SELECT a.id, a.user_id, s.name, a.date, a.time
                FROM appointments a JOIN services s ON a.service_id=s.id
                WHERE a.status='active' AND a.date=? AND a.time>=? AND a.time<=?
                AND (a.reminded & ?)=0""", (td, ws, we, h)).fetchall()
            for r in rows: result.append((r, h))
    return result


def mark_reminded(appt_id, h):
    with db() as con:
        con.execute("UPDATE appointments SET reminded=reminded|? WHERE id=?", (h, appt_id))


# ─── Portfolio ───────────────────────────────────────

def get_portfolio():
    with db() as con:
        return con.execute(
            "SELECT id,file_id,caption FROM portfolio ORDER BY added_at DESC"
        ).fetchall()


def add_photo(file_id, caption=""):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with db() as con:
        con.execute(
            "INSERT INTO portfolio (file_id,caption,added_at) VALUES (?,?,?)",
            (file_id, caption, now))


def del_photo(pid):
    with db() as con: con.execute("DELETE FROM portfolio WHERE id=?", (pid,))


# ─── Social ──────────────────────────────────────────

def get_socials(enabled_only=False):
    q = "SELECT id,platform,url,enabled FROM social_links"
    if enabled_only: q += " WHERE enabled=1"
    with db() as con: return con.execute(q + " ORDER BY platform").fetchall()


def upsert_social(platform, url):
    with db() as con:
        con.execute("""
            INSERT INTO social_links (platform,url,enabled) VALUES (?,?,1)
            ON CONFLICT(platform) DO UPDATE SET url=?,enabled=1""", (platform, url, url))


def toggle_social(sid):
    with db() as con:
        cur = con.execute("SELECT enabled FROM social_links WHERE id=?", (sid,)).fetchone()[0]
        con.execute("UPDATE social_links SET enabled=? WHERE id=?", (0 if cur else 1, sid))


# ═══════════════════════════════════════════════════════
#                   🎨 UI HELPERS
# ═══════════════════════════════════════════════════════

MONTHS_RU  = {1:"января",2:"февраля",3:"марта",4:"апреля",5:"мая",6:"июня",
               7:"июля",8:"августа",9:"сентября",10:"октября",11:"ноября",12:"декабря"}
DAYS_SHORT = {0:"Пн",1:"Вт",2:"Ср",3:"Чт",4:"Пт",5:"Сб",6:"Вс"}
DAYS_FULL  = {0:"Понедельник",1:"Вторник",2:"Среда",3:"Четверг",
               4:"Пятница",5:"Суббота",6:"Воскресенье"}
SOCIAL_ICON = {"Instagram":"📸","VK":"💙","TikTok":"🎵","YouTube":"▶️","Telegram":"✈️"}


def progress(step):
    marks = ["①","②","③","④"]
    bar   = []
    for i, m in enumerate(marks):
        if i + 1 < step:  bar.append("✓")
        elif i + 1 == step: bar.append(m)
        else: bar.append("·")
    return "  ".join(bar)


def fmt_date(ds, full=False):
    d   = datetime.strptime(ds, "%Y-%m-%d")
    day = DAYS_FULL[d.weekday()] if full else DAYS_SHORT[d.weekday()]
    return f"{day}, {d.day} {MONTHS_RU[d.month]}"


def fmt_price(p):
    return f"{p:,}".replace(",", " ") + " ₽"


def is_admin(uid): return uid == ADMIN_ID
def back(cb, label="← Назад"): return [[InlineKeyboardButton(label, callback_data=cb)]]
def home(): return [[InlineKeyboardButton("🏠 Главное меню", callback_data="start")]]


# ═══════════════════════════════════════════════════════
#                  👤 CLIENT HANDLERS
# ═══════════════════════════════════════════════════════

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data.clear()
    user  = update.effective_user
    name  = get_s("master_name", "Мастер")
    desc  = get_s("bot_description", "")
    wtext = get_s("welcome_text", "")

    text = f"✨ *{name}*\n_{desc}_\n\n{wtext}"
    kb   = [
        [InlineKeyboardButton("📅  Записаться", callback_data="book")],
        [InlineKeyboardButton("📋 Мои записи", callback_data="my_appts"),
         InlineKeyboardButton("🖼 Портфолио",  callback_data="portfolio")],
        [InlineKeyboardButton("🌐 Соцсети",    callback_data="social"),
         InlineKeyboardButton("💬 Связаться",  callback_data="contact")],
    ]
    if is_admin(user.id):
        kb.append([InlineKeyboardButton("⚙️ Панель управления", callback_data="admin")])

    markup = InlineKeyboardMarkup(kb)
    if update.message:
        await update.message.reply_text(text, reply_markup=markup, parse_mode=ParseMode.MARKDOWN)
    else:
        await update.callback_query.edit_message_text(text, reply_markup=markup, parse_mode=ParseMode.MARKDOWN)


# ── Шаг 1: Услуга ────────────────────────────────────

async def cb_book(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    services = get_services()
    if not services:
        return await q.edit_message_text(
            "😔 *Услуги пока не добавлены*\n\nПопробуйте позже или напишите мастеру напрямую.",
            reply_markup=InlineKeyboardMarkup(back("start")),
            parse_mode=ParseMode.MARKDOWN)

    kb = [[InlineKeyboardButton(
        f"💅  {n}  —  {fmt_price(p)}", callback_data=f"svc:{sid}")]
        for sid, n, p, _ in services]
    kb += back("start")
    await q.edit_message_text(
        f"`{progress(1)}`\n*Выберите услугу*\n─────────────────────",
        reply_markup=InlineKeyboardMarkup(kb),
        parse_mode=ParseMode.MARKDOWN)


# ── Шаг 2: Дата ──────────────────────────────────────

async def cb_svc(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    sid = int(q.data.split(":")[1])
    ctx.user_data["service_id"] = sid
    svc   = get_service(sid)
    dates = available_dates()

    if not dates:
        return await q.edit_message_text(
            "😔 *Свободных дат нет*\n\nНапишите мастеру напрямую.",
            reply_markup=InlineKeyboardMarkup(back("book")),
            parse_mode=ParseMode.MARKDOWN)

    today = datetime.now().date()
    kb, row = [], []
    for ds in dates:
        d     = datetime.strptime(ds, "%Y-%m-%d").date()
        delta = (d - today).days
        if   delta == 0: prefix = "Сегодня"
        elif delta == 1: prefix = "Завтра"
        else:            prefix = DAYS_SHORT[d.weekday()]
        label = f"{prefix}, {d.day} {MONTHS_RU[d.month]}"
        row.append(InlineKeyboardButton(label, callback_data=f"date:{ds}"))
        if len(row) == 2: kb.append(row); row = []
    if row: kb.append(row)
    kb += back("book")

    await q.edit_message_text(
        f"`{progress(2)}`\n*Выберите дату*\n─────────────────────\n"
        f"💅 _{svc[1]}_  •  *{fmt_price(svc[2])}*",
        reply_markup=InlineKeyboardMarkup(kb),
        parse_mode=ParseMode.MARKDOWN)


# ── Шаг 3: Время ─────────────────────────────────────

async def cb_date(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    date = q.data.split(":", 1)[1]
    ctx.user_data["date"] = date
    svc   = get_service(ctx.user_data["service_id"])
    slots = available_slots(date)

    if not slots:
        return await q.edit_message_text(
            f"😔 *На {fmt_date(date)} нет свободных окон*\n\nВыберите другую дату.",
            reply_markup=InlineKeyboardMarkup(back(f"svc:{ctx.user_data['service_id']}")),
            parse_mode=ParseMode.MARKDOWN)

    kb, row = [], []
    for s in slots:
        row.append(InlineKeyboardButton(f"🕐 {s}", callback_data=f"time:{s}"))
        if len(row) == 4: kb.append(row); row = []
    if row: kb.append(row)
    kb += back(f"svc:{ctx.user_data['service_id']}")

    await q.edit_message_text(
        f"`{progress(3)}`\n*Выберите время*\n─────────────────────\n"
        f"💅 _{svc[1]}_  •  *{fmt_price(svc[2])}*\n"
        f"📅 {fmt_date(date, full=True)}",
        reply_markup=InlineKeyboardMarkup(kb),
        parse_mode=ParseMode.MARKDOWN)


# ── Шаг 4: Комментарий ───────────────────────────────

async def cb_time(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    t = q.data.split(":", 1)[1]
    ctx.user_data["time"]  = t
    ctx.user_data["state"] = "comment"
    svc  = get_service(ctx.user_data["service_id"])
    date = ctx.user_data["date"]

    kb = [
        [InlineKeyboardButton("➡️  Пропустить", callback_data="skip_comment")],
        *back(f"date:{date}"),
    ]
    await q.edit_message_text(
        f"`{progress(4)}`\n*Пожелания к записи*\n─────────────────────\n"
        f"💅 _{svc[1]}_  •  *{fmt_price(svc[2])}*\n"
        f"📅 {fmt_date(date)}  🕐 {t}\n\n"
        f"Напишите любое пожелание мастеру или нажмите «Пропустить»:",
        reply_markup=InlineKeyboardMarkup(kb),
        parse_mode=ParseMode.MARKDOWN)


async def cb_skip_comment(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    ctx.user_data["comment"] = ""
    ctx.user_data["state"]   = None
    await _ask_name(q.edit_message_text, ctx)


async def _ask_name(send_fn, ctx):
    ctx.user_data["state"] = "client_name"
    svc  = get_service(ctx.user_data["service_id"])
    date = ctx.user_data["date"]
    time = ctx.user_data["time"]
    kb   = [[InlineKeyboardButton("❌  Отменить запись", callback_data="start")]]
    await send_fn(
        f"👤 *Введите ваше имя*\n─────────────────────\n"
        f"💅 _{svc[1]}_  •  *{fmt_price(svc[2])}*\n"
        f"📅 {fmt_date(date)}  🕐 {time}\n",
        reply_markup=InlineKeyboardMarkup(kb),
        parse_mode=ParseMode.MARKDOWN)


async def _show_confirm(edit_fn, ctx):
    svc   = get_service(ctx.user_data["service_id"])
    date  = ctx.user_data["date"]
    time  = ctx.user_data["time"]
    cmt   = ctx.user_data.get("comment", "")
    cname = ctx.user_data.get("client_name", "")
    phone = ctx.user_data.get("client_phone", "")
    text = (
        f"✅ *Подтвердите запись*\n"
        f"─────────────────────\n"
        f"💅  *{svc[1]}*\n"
        f"💰  {fmt_price(svc[2])}\n"
        f"📅  {fmt_date(date, full=True)}\n"
        f"🕐  {time}\n"
        f"👤  {cname}\n"
        f"📞  {phone}\n"
    )
    if cmt: text += f"💬  _{cmt}_\n"
    text += "─────────────────────"
    kb = [
        [InlineKeyboardButton("✅  Подтвердить запись", callback_data="confirm")],
        *back(f"time:{time}"),
    ]
    await edit_fn(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.MARKDOWN)


async def cb_confirm(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q    = update.callback_query; await q.answer()
    user = q.from_user
    svc  = get_service(ctx.user_data["service_id"])
    date  = ctx.user_data["date"]
    time  = ctx.user_data["time"]
    cmt   = ctx.user_data.get("comment", "")
    cname = ctx.user_data.get("client_name", user.full_name)
    phone = ctx.user_data.get("client_phone", "")

    appt_id = create_appt(
        user.id, user.username, cname,
        ctx.user_data["service_id"], date, time, cmt, phone)
    ctx.user_data.clear()

    await q.edit_message_text(
        f"⏳ *Заявка #{appt_id} отправлена!*\n"
        f"─────────────────────\n"
        f"💅  *{svc[1]}*\n"
        f"💰  {fmt_price(svc[2])}\n"
        f"📅  {fmt_date(date, full=True)}\n"
        f"🕐  {time}\n"
        f"─────────────────────\n"
        f"_Мастер подтвердит запись в ближайшее время 🤍_",
        reply_markup=InlineKeyboardMarkup(home()),
        parse_mode=ParseMode.MARKDOWN)

    uname = f"@{user.username}" if user.username else user.full_name
    await ctx.bot.send_message(
        ADMIN_ID,
        f"🔔 *Новая заявка #{appt_id}*\n"
        f"─────────────────────\n"
        f"👤  {cname}  {uname}\n"
        f"📞  {phone}\n"
        f"💅  {svc[1]}\n"
        f"💰  {fmt_price(svc[2])}\n"
        f"📅  {fmt_date(date)}  🕐  {time}\n"
        + (f"💬  _{cmt}_" if cmt else ""),
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("✅  Принять",   callback_data=f"a:appt_approve:{appt_id}"),
            InlineKeyboardButton("❌  Отклонить", callback_data=f"a:appt_reject:{appt_id}"),
        ]]),
        parse_mode=ParseMode.MARKDOWN)


# ── Мои записи ───────────────────────────────────────

async def cb_my_appts(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q    = update.callback_query; await q.answer()
    rows = user_appts(q.from_user.id)
    if not rows:
        return await q.edit_message_text(
            "📋 *Ваши записи*\n\nАктивных записей нет.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("📅  Записаться", callback_data="book")],
                *back("start"),
            ]),
            parse_mode=ParseMode.MARKDOWN)

    text = "📋 *Ваши записи*\n─────────────────────\n"
    for appt_id, sname, price, date, time, cmt, status in rows:
        pending = status == "pending"
        text += f"{'⏳' if pending else '✅'}  *{sname}*  —  {fmt_price(price)}\n"
        if pending: text += f"_Ожидает подтверждения мастером_\n"
        text += f"📅  {fmt_date(date)}  🕐  {time}\n"
        if cmt: text += f"💬  _{cmt}_\n"
        text += "\n"

    text += "_Чтобы отменить или перенести запись — напишите мастеру напрямую._"

    uname = get_s("master_username", "")
    kb    = []
    if uname:
        kb.append([InlineKeyboardButton("💬  Написать мастеру", url=f"https://t.me/{uname}")])
    kb += back("start")
    await q.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.MARKDOWN)


# ── Портфолио ─────────────────────────────────────────

async def cb_portfolio(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q   = update.callback_query; await q.answer()
    url = get_s("portfolio_url", "")
    if not url:
        return await q.edit_message_text(
            "🖼 *Портфолио*\n\nПортфолио пока не добавлено.",
            reply_markup=InlineKeyboardMarkup(back("start")),
            parse_mode=ParseMode.MARKDOWN)
    await q.edit_message_text(
        "🖼 *Портфолио*\n\nНажмите кнопку ниже, чтобы посмотреть работы 👇",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🖼  Смотреть портфолио", url=url)],
            [InlineKeyboardButton("📅  Записаться",         callback_data="book")],
            *back("start"),
        ]),
        parse_mode=ParseMode.MARKDOWN)


# ── Соцсети ───────────────────────────────────────────

async def cb_social(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    links = get_socials(enabled_only=True)
    if not links:
        return await q.edit_message_text(
            "🌐 *Соцсети*\n\nСсылки пока не добавлены.",
            reply_markup=InlineKeyboardMarkup(back("start")),
            parse_mode=ParseMode.MARKDOWN)
    kb = [[InlineKeyboardButton(f"{SOCIAL_ICON.get(p,'🔗')}  {p}", url=url)] for _, p, url, _ in links]
    kb += back("start")
    await q.edit_message_text(
        "🌐 *Мои соцсети*\n\nСледи за новыми работами!",
        reply_markup=InlineKeyboardMarkup(kb),
        parse_mode=ParseMode.MARKDOWN)


# ── Связаться ─────────────────────────────────────────

async def cb_contact(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    uname = get_s("master_username", "")
    name  = get_s("master_name", "Мастер")
    kb    = []
    if uname:
        kb.append([InlineKeyboardButton(f"✉️  Написать {name}", url=f"https://t.me/{uname}")])
    kb += back("start")
    await q.edit_message_text(
        f"💬 *Связаться с мастером*\n\n"
        f"{'Нажмите кнопку ниже 👇' if uname else '_Username мастера пока не указан._'}",
        reply_markup=InlineKeyboardMarkup(kb),
        parse_mode=ParseMode.MARKDOWN)


# ═══════════════════════════════════════════════════════
#                  ⚙️ ADMIN HANDLERS
# ═══════════════════════════════════════════════════════

def guard(fn):
    async def wrapper(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        if not is_admin(update.effective_user.id):
            if update.callback_query:
                await update.callback_query.answer("⛔ Нет доступа.", show_alert=True)
            return
        return await fn(update, ctx)
    return wrapper


@guard
async def cb_admin(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if q: await q.answer()
    kb = [
        [InlineKeyboardButton("👤  Профиль",         callback_data="a:profile")],
        [InlineKeyboardButton("💬  Тексты бота",     callback_data="a:texts")],
        [InlineKeyboardButton("💅  Услуги",          callback_data="a:services")],
        [InlineKeyboardButton("📅  Расписание",      callback_data="a:schedule")],
        [InlineKeyboardButton("🌐  Соцсети",         callback_data="a:social")],
        [InlineKeyboardButton("🖼  Портфолио",       callback_data="a:portfolio")],
        [InlineKeyboardButton("📋  Записи клиентов", callback_data="a:appts")],
        [InlineKeyboardButton("🔔  Напоминания",     callback_data="a:reminders")],
        [InlineKeyboardButton("🏠  В главное меню",  callback_data="start")],
    ]
    fn = q.edit_message_text if q else update.message.reply_text
    await fn(
        "⚙️ *Панель управления*\n\nВыберите раздел:",
        reply_markup=InlineKeyboardMarkup(kb),
        parse_mode=ParseMode.MARKDOWN)


@guard
async def a_profile(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    kb = [
        [InlineKeyboardButton("✏️  Имя мастера",    callback_data="a:edit:master_name:a:profile")],
        [InlineKeyboardButton("✏️  Username (без @)", callback_data="a:edit:master_username:a:profile")],
        [InlineKeyboardButton("✏️  Описание",       callback_data="a:edit:bot_description:a:profile")],
        *back("admin"),
    ]
    await q.edit_message_text(
        f"👤 *Профиль бота*\n─────────────────────\n"
        f"*Имя:* {get_s('master_name')}\n"
        f"*Username:* @{get_s('master_username') or '—'}\n"
        f"*Описание:* {get_s('bot_description')}",
        reply_markup=InlineKeyboardMarkup(kb),
        parse_mode=ParseMode.MARKDOWN)


@guard
async def a_texts(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    kb = [
        [InlineKeyboardButton("✏️  Приветствие",          callback_data="a:edit:welcome_text:a:texts")],
        [InlineKeyboardButton("✏️  Подтверждение записи", callback_data="a:edit:confirm_text:a:texts")],
        *back("admin"),
    ]
    await q.edit_message_text(
        f"💬 *Тексты бота*\n─────────────────────\n"
        f"*Приветствие:*\n{get_s('welcome_text')}\n\n"
        f"*Подтверждение:*\n{get_s('confirm_text')}",
        reply_markup=InlineKeyboardMarkup(kb),
        parse_mode=ParseMode.MARKDOWN)


@guard
async def a_services(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    svcs = get_services(all_=True)
    kb   = []
    for sid, name, price, active in svcs:
        icon = "✅" if active else "❌"
        kb.append([
            InlineKeyboardButton(f"{icon}  {name}  —  {fmt_price(price)}", callback_data=f"a:svc_toggle:{sid}"),
            InlineKeyboardButton("🗑", callback_data=f"a:svc_del:{sid}"),
        ])
    kb.append([InlineKeyboardButton("➕  Добавить услугу", callback_data="a:svc_add")])
    kb += back("admin")
    await q.edit_message_text(
        "💅 *Услуги*\n✅ — активна  ❌ — скрыта\n_Нажмите на услугу чтобы вкл/выкл:_",
        reply_markup=InlineKeyboardMarkup(kb),
        parse_mode=ParseMode.MARKDOWN)


@guard
async def a_svc_toggle(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    toggle_service(int(q.data.split(":")[2]))
    await a_services(update, ctx)


@guard
async def a_svc_del(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    delete_service(int(q.data.split(":")[2]))
    await a_services(update, ctx)


@guard
async def a_svc_add(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    ctx.user_data["state"]   = "svc_name"
    ctx.user_data["new_svc"] = {}
    await q.edit_message_text(
        "➕ *Новая услуга*\n\n1️⃣  Введите *название* услуги:",
        reply_markup=InlineKeyboardMarkup(back("a:services")),
        parse_mode=ParseMode.MARKDOWN)


@guard
async def a_schedule(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    today = datetime.now().date()
    ws    = get_s("work_start", "10:00")
    we    = get_s("work_end",   "20:00")
    si    = get_s("slot_interval", "60")
    kb    = []
    for i in range(14):
        d     = today + timedelta(days=i)
        ds    = d.strftime("%Y-%m-%d")
        slots = all_slots_for_date(ds)
        open_ = sum(1 for _, o in slots if o)
        if   i == 0: day_label = "Сегодня"
        elif i == 1: day_label = "Завтра"
        else:        day_label = DAYS_SHORT[d.weekday()]
        label = f"{day_label}, {d.day} {MONTHS_RU[d.month]}"
        if slots: label += f"  •  {open_}/{len(slots)}"
        kb.append([InlineKeyboardButton(label, callback_data=f"a:sched_day:{ds}")])
    kb.append([InlineKeyboardButton("⏰  Рабочие часы", callback_data="a:work_hours")])
    kb += back("admin")
    await q.edit_message_text(
        f"📅 *Расписание*\n─────────────────────\n"
        f"Рабочие часы: *{ws} — {we}*\n"
        f"Шаг записи: *{si} мин*\n\n"
        f"_Выберите день для редактирования:_",
        reply_markup=InlineKeyboardMarkup(kb),
        parse_mode=ParseMode.MARKDOWN)


@guard
async def a_work_hours(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    kb = [
        [InlineKeyboardButton("✏️  Начало рабочего дня", callback_data="a:edit:work_start:a:work_hours")],
        [InlineKeyboardButton("✏️  Конец рабочего дня",  callback_data="a:edit:work_end:a:work_hours")],
        [InlineKeyboardButton("✏️  Шаг записи (минуты)", callback_data="a:edit:slot_interval:a:work_hours")],
        *back("a:schedule"),
    ]
    await q.edit_message_text(
        f"⏰ *Рабочие часы*\n─────────────────────\n"
        f"Начало: *{get_s('work_start')}*\n"
        f"Конец:  *{get_s('work_end')}*\n"
        f"Шаг записи: *{get_s('slot_interval')} мин*\n\n"
        f"_Формат времени: ЧЧ:ММ, например_ `09:30`\n"
        f"_Шаг: 30, 45, 60, 90 или 120 минут_\n\n"
        f"После изменений нажмите «Применить часы» в нужном дне.",
        reply_markup=InlineKeyboardMarkup(kb),
        parse_mode=ParseMode.MARKDOWN)


async def _render_sched_day(q, date):
    slots = all_slots_for_date(date)
    if not slots:
        generate_work_slots(date)
        slots = all_slots_for_date(date)

    kb, row = [], []
    for t, is_open in slots:
        icon = "✅" if is_open else "❌"
        row.append(InlineKeyboardButton(f"{icon} {t}", callback_data=f"a:slot_toggle:{date}:{t}"))
        if len(row) == 4: kb.append(row); row = []
    if row: kb.append(row)
    kb.append([InlineKeyboardButton("🔄  Применить рабочие часы", callback_data=f"a:slot_regen:{date}")])
    kb += back("a:schedule")
    await q.edit_message_text(
        f"📅 *{fmt_date(date, full=True)}*\n"
        f"✅ открыт  ❌ закрыт\n_Нажмите на слот чтобы переключить:_",
        reply_markup=InlineKeyboardMarkup(kb),
        parse_mode=ParseMode.MARKDOWN)


@guard
async def a_sched_day(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    await _render_sched_day(q, q.data.split(":", 2)[2])


@guard
async def a_slot_toggle(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    _, _, date, time = q.data.split(":", 3)
    toggle_slot(date, time)
    await _render_sched_day(q, date)


@guard
async def a_slot_regen(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer("✅ Слоты обновлены")
    clear_and_regen(q.data.split(":", 2)[2])
    await _render_sched_day(q, q.data.split(":", 2)[2])


@guard
async def a_social(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    links = get_socials()
    kb    = []
    for sid, platform, url, enabled in links:
        icon = "✅" if enabled else "❌"
        kb.append([InlineKeyboardButton(
            f"{icon}  {platform}: {url[:35]}", callback_data=f"a:stoggle:{sid}")])
    existing = {l[1] for l in links}
    for p in ["Instagram","VK","TikTok","YouTube","Telegram"]:
        if p not in existing:
            kb.append([InlineKeyboardButton(f"➕  Добавить {p}", callback_data=f"a:sadd:{p}")])
    kb += back("admin")
    await q.edit_message_text(
        "🌐 *Соцсети*\n✅ — отображается  ❌ — скрыто\n_Нажмите чтобы вкл/выкл:_",
        reply_markup=InlineKeyboardMarkup(kb),
        parse_mode=ParseMode.MARKDOWN)


@guard
async def a_stoggle(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    toggle_social(int(q.data.split(":")[2]))
    await a_social(update, ctx)


@guard
async def a_sadd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    platform = q.data.split(":")[2]
    ctx.user_data["state"]           = "social_url"
    ctx.user_data["social_platform"] = platform
    await q.edit_message_text(
        f"➕ *Добавить {platform}*\n\nВведите ссылку:\n_Пример: https://instagram.com/myname_",
        reply_markup=InlineKeyboardMarkup(back("a:social")),
        parse_mode=ParseMode.MARKDOWN)


@guard
@guard
async def a_portfolio(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q   = update.callback_query; await q.answer()
    url = get_s("portfolio_url", "")
    kb  = [
        [InlineKeyboardButton("✏️  Изменить ссылку", callback_data="a:edit:portfolio_url:a:portfolio")],
        *back("admin"),
    ]
    await q.edit_message_text(
        f"🖼 *Портфолио*\n─────────────────────\n"
        f"Текущая ссылка:\n"
        f"{'`' + url + '`' if url else '_не указана_'}",
        reply_markup=InlineKeyboardMarkup(kb),
        parse_mode=ParseMode.MARKDOWN)


@guard
async def a_appts(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    rows = all_appts()
    if not rows:
        return await q.edit_message_text(
            "📋 *Записи клиентов*\n\nАктивных записей нет.",
            reply_markup=InlineKeyboardMarkup(back("admin")),
            parse_mode=ParseMode.MARKDOWN)

    text = f"📋 *Записи клиентов*  ({len(rows)})\n─────────────────────\n"
    kb   = []
    for appt_id, fname, uname, sname, date, time, cmt, uid, phone, status in rows:
        u       = f"@{uname}" if uname else fname
        pending = status == "pending"
        text += f"{'⏳' if pending else '✅'} *#{appt_id}*  {fmt_date(date)}  {time}\n"
        text += f"👤  {fname}  {u}\n"
        if phone: text += f"📞  {phone}\n"
        text += f"💅  {sname}\n"
        if cmt: text += f"💬  _{cmt}_\n"
        text += "\n"
        row = []
        if uname: row.append(InlineKeyboardButton(f"✉️ #{appt_id}", url=f"https://t.me/{uname}"))
        if pending:
            if row: kb.append(row)
            kb.append([
                InlineKeyboardButton(f"✅ Принять #{appt_id}",   callback_data=f"a:appt_approve:{appt_id}"),
                InlineKeyboardButton(f"❌ Отклонить #{appt_id}", callback_data=f"a:appt_reject:{appt_id}"),
            ])
        else:
            row.append(InlineKeyboardButton(f"❌ Отмена #{appt_id}", callback_data=f"a:appt_cancel:{appt_id}"))
            kb.append(row)
    kb += back("admin")
    if len(text) > 4000: text = text[:3980] + "\n…"
    await q.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.MARKDOWN)


@guard
async def a_appt_cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    appt_id = int(q.data.split(":")[2])
    a = get_appt(appt_id)
    cancel_appt(appt_id)
    if a:
        try:
            await ctx.bot.send_message(
                a[1],
                f"❌ *Ваша запись отменена мастером*\n\n"
                f"💅  {a[4]}\n📅  {fmt_date(a[5])}  🕐  {a[6]}\n\n"
                f"Свяжитесь с мастером для уточнений.",
                parse_mode=ParseMode.MARKDOWN)
        except Exception: pass
    await a_appts(update, ctx)


@guard
async def a_appt_approve(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer("✅ Запись подтверждена!")
    appt_id = int(q.data.split(":")[2])
    a = get_appt(appt_id)
    approve_appt(appt_id)
    if a:
        try:
            await ctx.bot.send_message(
                a[1],
                f"✅ *Ваша запись подтверждена!*\n"
                f"─────────────────────\n"
                f"💅  *{a[4]}*\n"
                f"📅  {fmt_date(a[5], full=True)}\n"
                f"🕐  {a[6]}\n"
                f"─────────────────────\n"
                f"_{get_s('confirm_text')}_",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("📋 Мои записи", callback_data="my_appts")
                ]]),
                parse_mode=ParseMode.MARKDOWN)
        except Exception: pass
    # Обновить кнопки в уведомлении мастера
    try:
        await q.edit_message_reply_markup(reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton(f"✅ Подтверждено #{appt_id}", callback_data="noop")
        ]]))
    except Exception: pass


@guard
async def a_appt_reject(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer("❌ Заявка отклонена")
    appt_id = int(q.data.split(":")[2])
    a = get_appt(appt_id)
    cancel_appt(appt_id)
    if a:
        try:
            await ctx.bot.send_message(
                a[1],
                f"❌ *Ваша заявка отклонена*\n\n"
                f"💅  {a[4]}\n"
                f"📅  {fmt_date(a[5])}  🕐  {a[6]}\n\n"
                f"Свяжитесь с мастером для уточнений.",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("💬 Написать мастеру", url=f"https://t.me/{get_s('master_username')}")
                ]] if get_s("master_username") else []),
                parse_mode=ParseMode.MARKDOWN)
        except Exception: pass
    try:
        await q.edit_message_reply_markup(reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton(f"❌ Отклонено #{appt_id}", callback_data="noop")
        ]]))
    except Exception: pass


async def cb_noop(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()


@guard
async def a_reminders(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    hours   = get_s("reminder_hours", "24,2")
    enabled = get_s("remind_enabled", "1") == "1"
    hours_fmt = " ч и ".join(hours.split(",")) + " ч"
    kb = [
        [InlineKeyboardButton(
            f"{'✅' if enabled else '❌'}  Напоминания {'включены' if enabled else 'выключены'}",
            callback_data="a:rem_toggle")],
        [InlineKeyboardButton("✏️  Изменить время", callback_data="a:rem_edit")],
        *back("admin"),
    ]
    await q.edit_message_text(
        f"🔔 *Напоминания*\n─────────────────────\n"
        f"Статус: {'✅ включены' if enabled else '❌ выключены'}\n"
        f"Отправляются за: *{hours_fmt}*\n\n"
        f"_Формат: числа через запятую_\n_Пример: `24,2` — за 24ч и за 2ч_",
        reply_markup=InlineKeyboardMarkup(kb),
        parse_mode=ParseMode.MARKDOWN)


@guard
async def a_rem_toggle(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    put_s("remind_enabled", "0" if get_s("remind_enabled","1") == "1" else "1")
    await a_reminders(update, ctx)


@guard
async def a_rem_edit(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    ctx.user_data["state"] = "reminder_hours"
    await q.edit_message_text(
        f"🔔 *Время напоминаний*\n\nТекущее: *{get_s('reminder_hours')} ч*\n\n"
        f"Введите часы через запятую:\n_(например: `24,2`)_",
        reply_markup=InlineKeyboardMarkup(back("a:reminders")),
        parse_mode=ParseMode.MARKDOWN)


@guard
async def a_edit(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Callback: a:edit:<key>:<back_cb>"""
    q = update.callback_query; await q.answer()
    parts   = q.data.split(":", 3)
    key     = parts[2]
    back_cb = parts[3] if len(parts) > 3 else "admin"
    labels  = {
        "master_name":     "имя мастера",
        "master_username": "username в Telegram (без @)",
        "bot_description": "описание",
        "welcome_text":    "приветственный текст",
        "confirm_text":    "текст подтверждения",
        "work_start":      "начало рабочего дня (ЧЧ:ММ)",
        "work_end":        "конец рабочего дня (ЧЧ:ММ)",
        "slot_interval":   "шаг записи в минутах",
    }
    ctx.user_data["state"]   = "edit_setting"
    ctx.user_data["edit_key"]  = key
    ctx.user_data["back_cb"]   = back_cb
    await q.edit_message_text(
        f"✏️ *Редактирование: {labels.get(key, key)}*\n\n"
        f"Текущее значение:\n`{get_s(key)}`\n\n"
        f"Введите новое значение:",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("← Отмена", callback_data=back_cb)]]),
        parse_mode=ParseMode.MARKDOWN)


# ═══════════════════════════════════════════════════════
#              📨 MESSAGE ROUTER
# ═══════════════════════════════════════════════════════

async def on_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    state = ctx.user_data.get("state")
    if not state: return
    text = update.message.text.strip()

    if state == "comment":
        ctx.user_data["comment"] = text
        ctx.user_data["state"]   = None
        await _ask_name(update.message.reply_text, ctx)

    elif state == "client_name":
        ctx.user_data["client_name"] = text
        ctx.user_data["state"] = "client_phone"
        kb = [[InlineKeyboardButton("❌  Отменить запись", callback_data="start")]]
        await update.message.reply_text(
            "📞 *Введите номер телефона*\n\nДля связи с вами:",
            reply_markup=InlineKeyboardMarkup(kb),
            parse_mode=ParseMode.MARKDOWN)

    elif state == "client_phone":
        ctx.user_data["client_phone"] = text
        ctx.user_data["state"] = None
        await _show_confirm(update.message.reply_text, ctx)

    elif state == "edit_setting" and is_admin(update.effective_user.id):
        key     = ctx.user_data.pop("edit_key", None)
        back_cb = ctx.user_data.pop("back_cb", "admin")
        if key: put_s(key, text)
        ctx.user_data["state"] = None
        await update.message.reply_text(
            "✅ *Сохранено!*",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("← Назад", callback_data=back_cb)]]),
            parse_mode=ParseMode.MARKDOWN)

    elif state == "social_url" and is_admin(update.effective_user.id):
        platform = ctx.user_data.pop("social_platform", "")
        ctx.user_data["state"] = None
        upsert_social(platform, text)
        await update.message.reply_text(
            f"✅ *{platform} добавлен!*",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("← К соцсетям", callback_data="a:social")]]),
            parse_mode=ParseMode.MARKDOWN)

    elif state in ("svc_name", "svc_price") and is_admin(update.effective_user.id):
        svc = ctx.user_data.setdefault("new_svc", {})
        if state == "svc_name":
            svc["name"] = text
            ctx.user_data["state"] = "svc_price"
            await update.message.reply_text(
                "2️⃣  Введите *цену* (₽, только цифры):",
                reply_markup=InlineKeyboardMarkup(back("a:services")),
                parse_mode=ParseMode.MARKDOWN)
        else:
            if not text.isdigit():
                return await update.message.reply_text("❌ Введите только цифры!")
            add_service(svc["name"], int(text))
            ctx.user_data["state"]   = None
            ctx.user_data["new_svc"] = {}
            await update.message.reply_text(
                f"✅ *Услуга добавлена!*\n\n💅 {svc['name']}  —  {fmt_price(int(text))}",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("← К услугам", callback_data="a:services")]]),
                parse_mode=ParseMode.MARKDOWN)

    elif state == "reminder_hours" and is_admin(update.effective_user.id):
        parts = [p.strip() for p in text.split(",")]
        if not all(p.isdigit() for p in parts):
            return await update.message.reply_text(
                "❌ Введите числа через запятую. Пример: `24,2`",
                parse_mode=ParseMode.MARKDOWN)
        put_s("reminder_hours", ",".join(parts))
        ctx.user_data["state"] = None
        await update.message.reply_text(
            f"✅ *Сохранено!* Напоминания за: *{', '.join(parts)} ч*",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("← К напоминаниям", callback_data="a:reminders")]]),
            parse_mode=ParseMode.MARKDOWN)



# ═══════════════════════════════════════════════════════
#              ⏰ REMINDER JOB
# ═══════════════════════════════════════════════════════

async def job_reminders(ctx: ContextTypes.DEFAULT_TYPE):
    for (appt_id, user_id, sname, date, time), hours in pending_reminders():
        try:
            if hours == 1:            h_str = "1 час"
            elif hours in (2,3,4):   h_str = f"{hours} часа"
            else:                     h_str = f"{hours} часов"
            await ctx.bot.send_message(
                user_id,
                f"⏰ *Напоминание о записи*\n─────────────────────\n"
                f"Через *{h_str}* у вас:\n\n"
                f"💅  *{sname}*\n"
                f"📅  {fmt_date(date, full=True)}\n"
                f"🕐  {time}\n\n_Ждём вас! ✨_",
                parse_mode=ParseMode.MARKDOWN)
            mark_reminded(appt_id, hours)
        except Exception as e:
            logging.warning(f"Reminder error user {user_id}: {e}")


# ═══════════════════════════════════════════════════════
#                    🚀 MAIN
# ═══════════════════════════════════════════════════════

async def post_init(app):
    await app.bot.set_my_commands([
        BotCommand("start", "Главное меню"),
        BotCommand("admin", "Панель управления"),
    ])


def main():
    logging.basicConfig(
        format="%(asctime)s [%(levelname)s] %(message)s",
        level=logging.INFO)
    init_db()

    app = Application.builder().token(BOT_TOKEN).post_init(post_init).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("admin", cb_admin))

    # Client
    app.add_handler(CallbackQueryHandler(cmd_start,       pattern="^start$"))
    app.add_handler(CallbackQueryHandler(cb_book,         pattern="^book$"))
    app.add_handler(CallbackQueryHandler(cb_svc,          pattern=r"^svc:\d+$"))
    app.add_handler(CallbackQueryHandler(cb_date,         pattern=r"^date:"))
    app.add_handler(CallbackQueryHandler(cb_time,         pattern=r"^time:"))
    app.add_handler(CallbackQueryHandler(cb_skip_comment, pattern="^skip_comment$"))
    app.add_handler(CallbackQueryHandler(cb_confirm,      pattern="^confirm$"))
    app.add_handler(CallbackQueryHandler(cb_my_appts,     pattern="^my_appts$"))
    app.add_handler(CallbackQueryHandler(cb_portfolio,    pattern="^portfolio$"))
    app.add_handler(CallbackQueryHandler(cb_social,       pattern="^social$"))
    app.add_handler(CallbackQueryHandler(cb_contact,      pattern="^contact$"))

    # Admin
    app.add_handler(CallbackQueryHandler(cb_admin,        pattern="^admin$"))
    app.add_handler(CallbackQueryHandler(a_profile,       pattern="^a:profile$"))
    app.add_handler(CallbackQueryHandler(a_texts,         pattern="^a:texts$"))
    app.add_handler(CallbackQueryHandler(a_edit,          pattern=r"^a:edit:"))
    app.add_handler(CallbackQueryHandler(a_services,      pattern="^a:services$"))
    app.add_handler(CallbackQueryHandler(a_svc_toggle,    pattern=r"^a:svc_toggle:\d+$"))
    app.add_handler(CallbackQueryHandler(a_svc_del,       pattern=r"^a:svc_del:\d+$"))
    app.add_handler(CallbackQueryHandler(a_svc_add,       pattern="^a:svc_add$"))
    app.add_handler(CallbackQueryHandler(a_schedule,      pattern="^a:schedule$"))
    app.add_handler(CallbackQueryHandler(a_work_hours,    pattern="^a:work_hours$"))
    app.add_handler(CallbackQueryHandler(a_sched_day,     pattern=r"^a:sched_day:"))
    app.add_handler(CallbackQueryHandler(a_slot_toggle,   pattern=r"^a:slot_toggle:"))
    app.add_handler(CallbackQueryHandler(a_slot_regen,    pattern=r"^a:slot_regen:"))
    app.add_handler(CallbackQueryHandler(a_social,        pattern="^a:social$"))
    app.add_handler(CallbackQueryHandler(a_stoggle,       pattern=r"^a:stoggle:\d+$"))
    app.add_handler(CallbackQueryHandler(a_sadd,          pattern=r"^a:sadd:"))
    app.add_handler(CallbackQueryHandler(a_portfolio,     pattern="^a:portfolio$"))
    app.add_handler(CallbackQueryHandler(a_appts,         pattern="^a:appts$"))
    app.add_handler(CallbackQueryHandler(a_appt_cancel,   pattern=r"^a:appt_cancel:\d+$"))
    app.add_handler(CallbackQueryHandler(a_appt_approve,  pattern=r"^a:appt_approve:\d+$"))
    app.add_handler(CallbackQueryHandler(a_appt_reject,   pattern=r"^a:appt_reject:\d+$"))
    app.add_handler(CallbackQueryHandler(cb_noop,         pattern="^noop$"))
    app.add_handler(CallbackQueryHandler(a_reminders,     pattern="^a:reminders$"))
    app.add_handler(CallbackQueryHandler(a_rem_toggle,    pattern="^a:rem_toggle$"))
    app.add_handler(CallbackQueryHandler(a_rem_edit,      pattern="^a:rem_edit$"))

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))

    app.job_queue.run_repeating(job_reminders, interval=900, first=30)

    logging.info("✅ Бот запущен!")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
