import os
import math
import sqlite3
import logging
from datetime import datetime

import requests
from telegram import (
    Update, ReplyKeyboardMarkup, KeyboardButton,
    InlineKeyboardMarkup, InlineKeyboardButton
)
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    ContextTypes, ConversationHandler, filters
)

BOT_TOKEN = os.environ["BOT_TOKEN"]
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))

BASE_FARE = float(os.getenv("BASE_FARE", "2.0"))
PRICE_PER_KM = float(os.getenv("PRICE_PER_KM", "0.80"))
MIN_FARE = float(os.getenv("MIN_FARE", "3.0"))

# Render persistent disk path. Locally it falls back to ./data.
DB_PATH = os.getenv("DB_PATH", "./data/yalla_go.db")
OSRM_URL = os.getenv(
    "OSRM_URL",
    "https://router.project-osrm.org/route/v1/driving"
)

os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO
)
log = logging.getLogger("yalla-go")

PICKUP, DESTINATION, DRIVER_NAME = range(3)

SEARCHING = "searching"
ACCEPTED = "accepted"
ARRIVING = "arriving"
STARTED = "started"
COMPLETED = "completed"
CANCELLED = "cancelled"


def connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def now():
    return datetime.utcnow().isoformat(timespec="seconds")


def init_db():
    conn = connection()
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        username TEXT,
        first_name TEXT,
        role TEXT DEFAULT 'customer',
        created_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS drivers (
        user_id INTEGER PRIMARY KEY,
        name TEXT NOT NULL,
        phone TEXT,
        online INTEGER DEFAULT 0,
        lat REAL,
        lon REAL,
        created_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS rides (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        customer_id INTEGER NOT NULL,
        driver_id INTEGER,
        pickup_lat REAL NOT NULL,
        pickup_lon REAL NOT NULL,
        dest_lat REAL NOT NULL,
        dest_lon REAL NOT NULL,
        distance_km REAL NOT NULL,
        fare REAL NOT NULL,
        status TEXT NOT NULL,
        created_at TEXT NOT NULL,
        accepted_at TEXT,
        started_at TEXT,
        completed_at TEXT
    );

    CREATE TABLE IF NOT EXISTS driver_locations (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ride_id INTEGER NOT NULL,
        driver_id INTEGER NOT NULL,
        lat REAL NOT NULL,
        lon REAL NOT NULL,
        created_at TEXT NOT NULL
    );
    """)
    conn.commit()
    conn.close()


def save_user(user, role="customer"):
    conn = connection()
    conn.execute("""
        INSERT INTO users(user_id, username, first_name, role, created_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET
            username=excluded.username,
            first_name=excluded.first_name
    """, (user.id, user.username, user.first_name, role, now()))
    conn.commit()
    conn.close()


def haversine(lat1, lon1, lat2, lon2):
    r = 6371.0088
    p1 = math.radians(lat1)
    p2 = math.radians(lat2)
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)

    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(p1) * math.cos(p2) * math.sin(dlon / 2) ** 2
    )
    return 2 * r * math.asin(math.sqrt(a))


def route_distance_km(lat1, lon1, lat2, lon2):
    try:
        url = f"{OSRM_URL}/{lon1},{lat1};{lon2},{lat2}"
        response = requests.get(
            url,
            params={"overview": "false"},
            timeout=8
        )
        response.raise_for_status()
        data = response.json()

        if data.get("routes"):
            return float(data["routes"][0]["distance"]) / 1000
    except Exception as exc:
        log.warning("OSRM unavailable: %s", exc)

    # Fallback approximation.
    return haversine(lat1, lon1, lat2, lon2) * 1.20


def calculate_fare(distance_km):
    return max(
        MIN_FARE,
        BASE_FARE + distance_km * PRICE_PER_KM
    )


def customer_keyboard():
    return ReplyKeyboardMarkup(
        [
            [
                KeyboardButton("🚕 طلب رحلة"),
                KeyboardButton("📋 رحلتي")
            ],
            [
                KeyboardButton("👤 حسابي"),
                KeyboardButton("❓ المساعدة")
            ]
        ],
        resize_keyboard=True
    )


def driver_keyboard(online):
    return ReplyKeyboardMarkup(
        [
            [KeyboardButton("🔴 إيقاف استقبال الرحلات" if online
                            else "🟢 استقبال الرحلات")],
            [
                KeyboardButton("📋 رحلتي"),
                KeyboardButton("📍 إرسال موقعي")
            ]
        ],
        resize_keyboard=True
    )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    save_user(update.effective_user)

    await update.message.reply_text(
        "🚕 أهلاً بك في يلا غو\n\n"
        "اطلب رحلة، أرسل موقع الانطلاق والوجهة، "
        "وسيتم حساب المسافة والسعر تلقائياً.",
        reply_markup=customer_keyboard()
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "❓ مساعدة يلا غو\n\n"
        "🚕 طلب رحلة — إنشاء رحلة جديدة\n"
        "📋 رحلتي — عرض آخر رحلة\n"
        "👤 حسابي — معلومات الحساب\n"
        "/driver — وضع السائق\n"
        "/register_driver — تسجيل سائق\n"
        "/cancel — إلغاء رحلة"
    )


async def account(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id

    conn = connection()
    driver = conn.execute(
        "SELECT * FROM drivers WHERE user_id=?",
        (uid,)
    ).fetchone()
    conn.close()

    role = "👨‍✈️ سائق" if driver else "👤 عميل"

    await update.message.reply_text(
        f"👤 حسابك\n\n"
        f"الاسم: {update.effective_user.first_name}\n"
        f"Telegram ID: {uid}\n"
        f"النوع: {role}"
    )


async def start_ride(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()

    keyboard = ReplyKeyboardMarkup(
        [
            [
                KeyboardButton(
                    "📍 إرسال موقع الانطلاق",
                    request_location=True
                )
            ],
            [KeyboardButton("❌ إلغاء")]
        ],
        resize_keyboard=True
    )

    await update.message.reply_text(
        "📍 أرسل موقع الانطلاق.",
        reply_markup=keyboard
    )

    return PICKUP


async def pickup_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.location:
        await update.message.reply_text("أرسل الموقع باستخدام زر الموقع.")
        return PICKUP

    location = update.message.location

    context.user_data["pickup"] = (
        location.latitude,
        location.longitude
    )

    keyboard = ReplyKeyboardMarkup(
        [
            [
                KeyboardButton(
                    "🎯 إرسال الوجهة",
                    request_location=True
                )
            ],
            [KeyboardButton("❌ إلغاء")]
        ],
        resize_keyboard=True
    )

    await update.message.reply_text(
        "✅ تم حفظ نقطة الانطلاق.\n\n"
        "الآن أرسل موقع الوجهة.",
        reply_markup=keyboard
    )

    return DESTINATION


async def destination_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.location:
        await update.message.reply_text("أرسل الموقع باستخدام زر الموقع.")
        return DESTINATION

    location = update.message.location

    context.user_data["destination"] = (
        location.latitude,
        location.longitude
    )

    p_lat, p_lon = context.user_data["pickup"]

    await update.message.reply_text("⏳ جاري حساب المسافة والسعر...")

    distance = route_distance_km(
        p_lat,
        p_lon,
        location.latitude,
        location.longitude
    )

    fare = calculate_fare(distance)

    context.user_data["distance"] = distance
    context.user_data["fare"] = fare

    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "✅ تأكيد الرحلة",
                    callback_data="ride_confirm"
                )
            ],
            [
                InlineKeyboardButton(
                    "❌ إلغاء",
                    callback_data="ride_cancel"
                )
            ]
        ]
    )

    await update.message.reply_text(
        f"🚕 تفاصيل الرحلة\n\n"
        f"📏 المسافة: {distance:.2f} كم\n"
        f"💰 السعر التقديري: {fare:.2f}\n\n"
        "هل تريد تأكيد الرحلة؟",
        reply_markup=keyboard
    )

    return ConversationHandler.END


async def ride_confirmation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "ride_cancel":
        context.user_data.clear()
        await query.edit_message_text("❌ تم إلغاء الطلب.")
        return

    pickup = context.user_data.get("pickup")
    destination = context.user_data.get("destination")
    distance = context.user_data.get("distance")
    fare = context.user_data.get("fare")

    if not all([
        pickup,
        destination,
        distance is not None,
        fare is not None
    ]):
        await query.edit_message_text(
            "❌ انتهت جلسة الطلب. ابدأ طلباً جديداً."
        )
        return

    conn = connection()

    cursor = conn.execute(
        """
        INSERT INTO rides (
            customer_id,
            pickup_lat,
            pickup_lon,
            dest_lat,
            dest_lon,
            distance_km,
            fare,
            status,
            created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            query.from_user.id,
            pickup[0],
            pickup[1],
            destination[0],
            destination[1],
            distance,
            fare,
            SEARCHING,
            now()
        )
    )

    ride_id = cursor.lastrowid

    conn.commit()
    conn.close()

    context.user_data["ride_id"] = ride_id

    await query.edit_message_text(
        f"✅ تم إنشاء الرحلة #{ride_id}\n\n"
        f"📏 المسافة: {distance:.2f} كم\n"
        f"💰 السعر: {fare:.2f}\n\n"
        "🔎 نبحث عن سائق متاح..."
    )

    await notify_drivers(ride_id, context)


async def notify_drivers(ride_id, context):
    conn = connection()

    ride = conn.execute(
        "SELECT * FROM rides WHERE id=?",
        (ride_id,)
    ).fetchone()

    drivers = conn.execute(
        """
        SELECT * FROM drivers
        WHERE online=1 AND user_id != ?
        """,
        (ride["customer_id"],)
    ).fetchall()

    conn.close()

    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "🚕 قبول الرحلة",
                    callback_data=f"accept:{ride_id}"
                )
            ]
        ]
    )

    message = (
        f"🚨 رحلة جديدة #{ride_id}\n\n"
        f"📏 المسافة: {ride['distance_km']:.2f} كم\n"
        f"💰 الأجرة: {ride['fare']:.2f}\n\n"
        "اضغط قبول إذا كنت متاحاً."
    )

    for driver in drivers:
        try:
            await context.bot.send_message(
                driver["user_id"],
                message,
                reply_markup=keyboard
            )

            await context.bot.send_location(
                driver["user_id"],
                ride["pickup_lat"],
                ride["pickup_lon"]
            )
        except Exception as exc:
            log.warning(
                "Cannot notify driver %s: %s",
                driver["user_id"],
                exc
            )


async def accept_ride(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    ride_id = int(query.data.split(":")[1])
    driver_id = query.from_user.id

    conn = connection()

    ride = conn.execute(
        "SELECT * FROM rides WHERE id=?",
        (ride_id,)
    ).fetchone()

    driver = conn.execute(
        "SELECT * FROM drivers WHERE user_id=?",
        (driver_id,)
    ).fetchone()

    if not ride:
        conn.close()
        await query.edit_message_text("❌ الرحلة غير موجودة.")
        return

    if ride["status"] != SEARCHING or ride["driver_id"]:
        conn.close()
        await query.edit_message_text(
            "⚠️ تم قبول الرحلة من سائق آخر."
        )
        return

    if not driver or not driver["online"]:
        conn.close()
        await query.edit_message_text(
            "❌ يجب أن تكون متصلاً كسائق."
        )
        return

    conn.execute(
        """
        UPDATE rides
        SET driver_id=?, status=?, accepted_at=?
        WHERE id=?
        """,
        (
            driver_id,
            ACCEPTED,
            now(),
            ride_id
        )
    )

    conn.commit()
    conn.close()

    await query.edit_message_text(
        f"✅ تم قبول الرحلة #{ride_id}\n\n"
        "أرسل موقعك الحالي للعميل."
    )

    await context.bot.send_message(
        ride["customer_id"],
        f"🚕 تم قبول رحلتك #{ride_id}!\n\n"
        "السائق في طريقه إليك."
    )


async def my_ride(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id

    conn = connection()

    ride = conn.execute(
        """
        SELECT * FROM rides
        WHERE customer_id=? OR driver_id=?
        ORDER BY id DESC
        LIMIT 1
        """,
        (uid, uid)
    ).fetchone()

    conn.close()

    if not ride:
        await update.message.reply_text(
            "لا توجد رحلة حالياً."
        )
        return

    names = {
        SEARCHING: "🔎 البحث عن سائق",
        ACCEPTED: "🚕 تم قبول الرحلة",
        ARRIVING: "📍 السائق في الطريق",
        STARTED: "🟢 الرحلة بدأت",
        COMPLETED: "🏁 مكتملة",
        CANCELLED: "❌ ملغاة"
    }

    await update.message.reply_text(
        f"🚕 الرحلة #{ride['id']}\n\n"
        f"الحالة: {names.get(ride['status'], ride['status'])}\n"
        f"📏 المسافة: {ride['distance_km']:.2f} كم\n"
        f"💰 السعر: {ride['fare']:.2f}"
    )


async def register_driver(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👨‍✈️ تسجيل سائق\n\n"
        "أرسل اسمك الكامل:"
    )

    return DRIVER_NAME


async def save_driver(update: Update, context: ContextTypes.DEFAULT_TYPE):
    name = update.message.text.strip()

    if len(name) < 2:
        await update.message.reply_text(
            "أرسل اسماً صحيحاً."
        )
        return DRIVER_NAME

    uid = update.effective_user.id

    conn = connection()

    conn.execute(
        """
        INSERT INTO drivers(
            user_id,
            name,
            online,
            created_at
        )
        VALUES (?, ?, 0, ?)
        ON CONFLICT(user_id)
        DO UPDATE SET name=excluded.name
        """,
        (uid, name, now())
    )

    conn.commit()
    conn.close()

    save_user(update.effective_user, "driver")

    await update.message.reply_text(
        "✅ تم تسجيلك كسائق.\n\n"
        "استخدم /driver للدخول إلى وضع السائق."
    )

    return ConversationHandler.END


async def driver_mode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id

    conn = connection()

    driver = conn.execute(
        "SELECT * FROM drivers WHERE user_id=?",
        (uid,)
    ).fetchone()

    conn.close()

    if not driver:
        await update.message.reply_text(
            "❌ أنت غير مسجل كسائق.\n"
            "استخدم /register_driver أولاً."
        )
        return

    await update.message.reply_text(
        f"👨‍✈️ وضع السائق\n\n"
        f"الاسم: {driver['name']}\n"
        f"الحالة: {'🟢 متصل' if driver['online'] else '🔴 غير متصل'}",
        reply_markup=driver_keyboard(bool(driver["online"]))
    )


async def toggle_driver(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id

    conn = connection()

    driver = conn.execute(
        "SELECT * FROM drivers WHERE user_id=?",
        (uid,)
    ).fetchone()

    if not driver:
        conn.close()
        await update.message.reply_text(
            "❌ سجل كسائق أولاً باستخدام /register_driver"
        )
        return

    new_status = 0 if driver["online"] else 1

    conn.execute(
        "UPDATE drivers SET online=? WHERE user_id=?",
        (new_status, uid)
    )

    conn.commit()
    conn.close()

    await update.message.reply_text(
        "🟢 أصبحت متصلاً وتستقبل الرحلات."
        if new_status
        else
        "🔴 تم إيقاف استقبال الرحلات.",
        reply_markup=driver_keyboard(bool(new_status))
    )


async def driver_location(update: Update, context: ContextTypes.DEFAULT_TYPE):
    location = update.message.location
    uid = update.effective_user.id

    conn = connection()

    driver = conn.execute(
        "SELECT * FROM drivers WHERE user_id=?",
        (uid,)
    ).fetchone()

    ride = conn.execute(
        """
        SELECT * FROM rides
        WHERE driver_id=?
          AND status IN (?, ?, ?)
        ORDER BY id DESC
        LIMIT 1
        """,
        (uid, ACCEPTED, ARRIVING, STARTED)
    ).fetchone()

    if driver:
        conn.execute(
            """
            UPDATE drivers
            SET lat=?, lon=?
            WHERE user_id=?
            """,
            (
                location.latitude,
                location.longitude,
                uid
            )
        )

    if ride:
        conn.execute(
            """
            INSERT INTO driver_locations(
                ride_id,
                driver_id,
                lat,
                lon,
                created_at
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                ride["id"],
                uid,
                location.latitude,
                location.longitude,
                now()
            )
        )

    conn.commit()
    conn.close()

    if ride:
        try:
            await context.bot.send_location(
                ride["customer_id"],
                location.latitude,
                location.longitude
            )

            await update.message.reply_text(
                "📍 تم إرسال موقعك للعميل."
            )
        except Exception:
            await update.message.reply_text(
                "📍 تم حفظ موقعك."
            )
    else:
        await update.message.reply_text(
            "📍 تم حفظ موقعك."
        )


async def driver_ride(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id

    conn = connection()

    ride = conn.execute(
        """
        SELECT * FROM rides
        WHERE driver_id=?
          AND status IN (?, ?, ?)
        ORDER BY id DESC
        LIMIT 1
        """,
        (uid, ACCEPTED, ARRIVING, STARTED)
    ).fetchone()

    conn.close()

    if not ride:
        await update.message.reply_text(
            "لا توجد رحلة نشطة."
        )
        return

    buttons = []

    if ride["status"] == ACCEPTED:
        buttons.append(
            [
                InlineKeyboardButton(
                    "📍 أنا في الطريق",
                    callback_data=f"status:{ride['id']}:{ARRIVING}"
                )
            ]
        )

    elif ride["status"] == ARRIVING:
        buttons.append(
            [
                InlineKeyboardButton(
                    "🟢 بدء الرحلة",
                    callback_data=f"status:{ride['id']}:{STARTED}"
                )
            ]
        )

    elif ride["status"] == STARTED:
        buttons.append(
            [
                InlineKeyboardButton(
                    "🏁 إنهاء الرحلة",
                    callback_data=f"status:{ride['id']}:{COMPLETED}"
                )
            ]
        )

    await update.message.reply_text(
        f"🚕 الرحلة #{ride['id']}\n\n"
        f"الحالة: {ride['status']}\n"
        f"💰 الأجرة: {ride['fare']:.2f}",
        reply_markup=InlineKeyboardMarkup(buttons)
    )


async def change_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    _, ride_id, new_status = query.data.split(":")
    ride_id = int(ride_id)

    uid = query.from_user.id

    conn = connection()

    ride = conn.execute(
        "SELECT * FROM rides WHERE id=?",
        (ride_id,)
    ).fetchone()

    if not ride or ride["driver_id"] != uid:
        conn.close()
        await query.edit_message_text(
            "❌ غير مصرح."
        )
        return

    allowed = {
        ARRIVING: ACCEPTED,
        STARTED: ARRIVING,
        COMPLETED: STARTED
    }

    if allowed.get(new_status) != ride["status"]:
        conn.close()
        await query.edit_message_text(
            "⚠️ انتقال حالة غير صالح."
        )
        return

    timestamp = {
        STARTED: "started_at",
        COMPLETED: "completed_at"
    }.get(new_status)

    if timestamp:
        conn.execute(
            f"""
            UPDATE rides
            SET status=?, {timestamp}=?
            WHERE id=?
            """,
            (new_status, now(), ride_id)
        )
    else:
        conn.execute(
            "UPDATE rides SET status=? WHERE id=?",
            (new_status, ride_id)
        )

    conn.commit()
    conn.close()

    messages = {
        ARRIVING: "📍 السائق في الطريق إليك.",
        STARTED: "🟢 بدأت الرحلة.",
        COMPLETED: "🏁 انتهت الرحلة."
    }

    await query.edit_message_text(
        f"الرحلة #{ride_id}: {messages[new_status]}"
    )

    await context.bot.send_message(
        ride["customer_id"],
        messages[new_status]
    )


async def cancel_ride(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id

    conn = connection()

    ride = conn.execute(
        """
        SELECT * FROM rides
        WHERE (customer_id=? OR driver_id=?)
          AND status NOT IN (?, ?)
        ORDER BY id DESC
        LIMIT 1
        """,
        (uid, uid, COMPLETED, CANCELLED)
    ).fetchone()

    if not ride:
        conn.close()
        await update.message.reply_text(
            "لا توجد رحلة قابلة للإلغاء."
        )
        return

    conn.execute(
        "UPDATE rides SET status=? WHERE id=?",
        (CANCELLED, ride["id"])
    )

    conn.commit()
    conn.close()

    other = (
        ride["driver_id"]
        if uid == ride["customer_id"]
        else ride["customer_id"]
    )

    if other:
        try:
            await context.bot.send_message(
                other,
                f"❌ تم إلغاء الرحلة #{ride['id']}."
            )
        except Exception:
            pass

    await update.message.reply_text(
        "❌ تم إلغاء الرحلة."
    )


async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return

    conn = connection()

    users = conn.execute(
        "SELECT COUNT(*) c FROM users"
    ).fetchone()["c"]

    drivers = conn.execute(
        "SELECT COUNT(*) c FROM drivers"
    ).fetchone()["c"]

    rides = conn.execute(
        "SELECT COUNT(*) c FROM rides"
    ).fetchone()["c"]

    active = conn.execute(
        """
        SELECT COUNT(*) c
        FROM rides
        WHERE status NOT IN (?, ?)
        """,
        (COMPLETED, CANCELLED)
    ).fetchone()["c"]

    conn.close()

    await update.message.reply_text(
        "📊 إحصائيات يلا غو\n\n"
        f"👥 المستخدمون: {users}\n"
        f"👨‍✈️ السائقون: {drivers}\n"
        f"🚕 الرحلات: {rides}\n"
        f"🔄 النشطة: {active}"
    )


async def text_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text

    if text == "🚕 طلب رحلة":
        await start_ride(update, context)

    elif text == "📋 رحلتي":
        await my_ride(update, context)

    elif text == "👤 حسابي":
        await account(update, context)

    elif text == "❓ المساعدة":
        await help_command(update, context)

    elif text in ("🟢 استقبال الرحلات", "🔴 إيقاف استقبال الرحلات"):
        await toggle_driver(update, context)

    elif text == "📍 إرسال موقعي":
        await update.message.reply_text(
            "استخدم زر الموقع أو أرسل Location من Telegram."
        )


def create_application():
    init_db()

    application = Application.builder().token(BOT_TOKEN).build()

    ride_handler = ConversationHandler(
        entry_points=[
            MessageHandler(
                filters.Regex("^🚕 طلب رحلة$"),
                start_ride
            )
        ],
        states={
            PICKUP: [
                MessageHandler(
                    filters.LOCATION,
                    pickup_received
                )
            ],
            DESTINATION: [
                MessageHandler(
                    filters.LOCATION,
                    destination_received
                )
            ]
        },
        fallbacks=[
            CommandHandler("cancel", cancel_ride)
        ]
    )

    driver_handler = ConversationHandler(
        entry_points=[
            CommandHandler("register_driver", register_driver)
        ],
        states={
            DRIVER_NAME: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    save_driver
                )
            ]
        },
        fallbacks=[
            CommandHandler("cancel", cancel_ride)
        ]
    )

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("driver", driver_mode))
    application.add_handler(CommandHandler("register_driver", register_driver))
    application.add_handler(CommandHandler("cancel", cancel_ride))
    application.add_handler(CommandHandler("stats", stats))

    application.add_handler(ride_handler)
    application.add_handler(driver_handler)

    application.add_handler(
        CallbackQueryHandler(
            ride_confirmation,
            pattern=r"^ride_(confirm|cancel)$"
        )
    )

    application.add_handler(
        CallbackQueryHandler(
            accept_ride,
            pattern=r"^accept:\d+$"
        )
    )

    application.add_handler(
        CallbackQueryHandler(
            change_status,
            pattern=r"^status:\d+:(arriving|started|completed)$"
        )
    )

    application.add_handler(
        MessageHandler(
            filters.LOCATION,
            driver_location
        )
    )

    application.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            text_router
        )
    )

    return application


if __name__ == "__main__":
    app = create_application()
    log.info("Yalla Go Telegram Bot started.")
    app.run_polling(
        allowed_updates=Update.ALL_TYPES
    )
