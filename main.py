# main.py
import os
import logging
from collections import deque
from contextlib import asynccontextmanager
from datetime import datetime
from http import HTTPStatus
from typing import Deque, Set, Literal, Optional, Dict, Any, List

from fastapi import FastAPI, Request, Response, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from telegram import (
    Update,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

# =========================
# לוגינג בסיסי
# =========================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("gateway-bot")

# =========================
# DB אופציונלי (db.py)
# =========================
try:
    from db import (
        init_schema,
        log_payment,
        update_payment_status,
        store_user,
        add_referral,
        get_top_referrers,
        get_monthly_payments,
        get_approval_stats,
        create_reward,
        ensure_promoter,
        update_promoter_settings,
        get_promoter_summary,
        incr_metric,
        get_metric,
    )
    DB_AVAILABLE = True
    logger.info("DB module loaded successfully, DB logging enabled.")
except Exception as e:
    logger.warning("DB not available (missing db.py or error loading it): %s", e)
    DB_AVAILABLE = False

# =========================
# משתני סביבה חיוניים
# =========================
BOT_TOKEN = os.environ.get("BOT_TOKEN")
WEBHOOK_URL = os.environ.get("WEBHOOK_URL")
BOT_USERNAME = os.environ.get("BOT_USERNAME")

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN environment variable is not set")

if not WEBHOOK_URL:
    raise RuntimeError("WEBHOOK_URL environment variable is not set")

logger.info("Starting bot with WEBHOOK_URL=%s", WEBHOOK_URL)

# =========================
# בדיקת BOT_TOKEN - עכשיו אחרי שהוא הוגדר!
# =========================
import requests

def validate_bot_token(token: str) -> bool:
    """בודק אם הטוקן תקין"""
    try:
        test_url = f"https://api.telegram.org/bot{token}/getMe"
        response = requests.get(test_url, timeout=10)
        if response.status_code == 200:
            logger.info("✅ Bot token is valid")
            return True
        else:
            logger.warning(f"⚠️ BOT_TOKEN may be invalid. Telegram API returned: {response.status_code}")
            logger.warning(f"🔍 Response: {response.text}")
            return False
    except Exception as e:
        logger.warning(f"⚠️ Failed to validate BOT_TOKEN: {e}")
        return False

# הרץ את הבדיקה
if BOT_TOKEN:
    is_valid = validate_bot_token(BOT_TOKEN)
    if not is_valid:
        logger.error("❌ Invalid BOT_TOKEN. The bot will not work properly.")
else:
    logger.error("❌ BOT_TOKEN is not set")

# =========================
# קבועים של המערכת
# =========================
COMMUNITY_GROUP_LINK = os.environ.get("COMMUNITY_GROUP_LINK", "https://t.me/+HIzvM8sEgh1kNWY0")
SUPPORT_GROUP_LINK = os.environ.get("SUPPORT_GROUP_LINK", "https://t.me/+1ANn25HeVBoxNmRk")
DEVELOPER_USER_ID = 224223270
PAYMENTS_LOG_CHAT_ID = -1001748319682

def build_personal_share_link(user_id: int) -> str:
    base_username = BOT_USERNAME or "Buy_My_Shop_bot"
    return f"https://t.me/{base_username}?start=ref_{user_id}"

# לינקי תשלום
PAYBOX_URL = os.environ.get("PAYBOX_URL", "https://links.payboxapp.com/1SNfaJ6XcYb")
BIT_URL = os.environ.get("BIT_URL", "https://www.bitpay.co.il/app/share-info?i=190693822888_19l4oyvE")
PAYPAL_URL = os.environ.get("PAYPAL_URL", "https://paypal.me/osifdu")
LANDING_URL = os.environ.get("LANDING_URL", "https://slh-nft.com/")
ADMIN_DASH_TOKEN = os.environ.get("ADMIN_DASH_TOKEN")
START_IMAGE_PATH = os.environ.get("START_IMAGE_PATH", "assets/start_banner.jpg")

# פרטי תשלום
BANK_DETAILS = (
    "🏦 *תשלום בהעברה בנקאית*\n\n"
    "בנק הפועלים\n"
    "סניף כפר גנים (153)\n"
    "חשבון 73462\n"
    "המוטב: קאופמן צביקה\n\n"
    "סכום: *39 ש\"ח*\n"
)

ADMIN_IDS = {DEVELOPER_USER_ID}
PayMethod = Literal["bank", "paybox", "ton"]

# =========================
# Dedup – מניעת כפילות
# =========================
_processed_ids: Deque[int] = deque(maxlen=1000)
_processed_set: Set[int] = set()

def is_duplicate_update(update: Update) -> bool:
    if update is None:
        return False
    uid = update.update_id
    if uid in _processed_set:
        return True
    _processed_set.add(uid)
    _processed_ids.append(uid)
    if len(_processed_set) > len(_processed_ids) + 10:
        valid = set(_processed_ids)
        _processed_set.intersection_update(valid)
    return False

# =========================
# זיכרון פשוט לתשלומים
# =========================
def get_payments_store(context: ContextTypes.DEFAULT_TYPE) -> Dict[int, Dict[str, Any]]:
    store = context.application.bot_data.get("payments")
    if store is None:
        store = {}
        context.application.bot_data["payments"] = store
    return store

def get_pending_rejects(context: ContextTypes.DEFAULT_TYPE) -> Dict[int, int]:
    store = context.application.bot_data.get("pending_rejects")
    if store is None:
        store = {}
        context.application.bot_data["pending_rejects"] = store
    return store

# =========================
# אפליקציית Telegram
# =========================
ptb_app: Application = (
    Application.builder()
    .updater(None)
    .token(BOT_TOKEN)
    .build()
)

# =========================
# FastAPI + lifespan
# =========================

@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    בזמן עליית השרת:
    1. מגדירים webhook ב-Telegram ל-WEBHOOK_URL
    2. מפעילים את אפליקציית ה-Telegram
    3. אם יש DB – מרימים schema
    """
    logger.info("Setting Telegram webhook to %s", WEBHOOK_URL)
    await ptb_app.bot.setWebhook(url=WEBHOOK_URL, allowed_updates=Update.ALL_TYPES)

    # init DB schema אם זמין
    if DB_AVAILABLE:
        try:
            init_schema()
            logger.info("DB schema initialized.")
        except Exception as e:
            logger.error("Failed to init DB schema: %s", e)

    async with ptb_app:
        logger.info("Starting Telegram Application")
        await ptb_app.start()
        yield
        logger.info("Stopping Telegram Application")
        await ptb_app.stop()

app = FastAPI(lifespan=lifespan)

# =========================
# API Routes for Website
# =========================

@app.get("/")
async def serve_site():
    """מגיש את אתר האינטרנט"""
    return FileResponse("docs/index.html")

@app.get("/site")
async def serve_site_alt():
    """מגיש את אתר האינטרנט (alias)"""
    return FileResponse("docs/index.html")

@app.get("/api/posts")
async def get_posts(limit: int = 20):
    """API לפוסטים חברתיים"""
    if not DB_AVAILABLE:
        return {"items": []}
    
    try:
        from db import get_social_posts
        posts = get_social_posts(limit)
        return {"items": posts}
    except Exception as e:
        logger.error("Failed to get posts: %s", e)
        return {"items": []}

@app.get("/api/token/sales")
async def get_token_sales(limit: int = 50):
    """API למכירות טוקנים"""
    if not DB_AVAILABLE:
        return {"items": []}
    
    try:
        from db import get_token_sales
        sales = get_token_sales(limit)
        return {"items": sales}
    except Exception as e:
        logger.error("Failed to get token sales: %s", e)
        return {"items": []}

@app.get("/api/token/price")
async def get_token_price():
    """API לשער הטוקן"""
    return {
        "official_price_nis": 444,
        "currency": "ILS",
        "updated_at": datetime.utcnow().isoformat()
    }

@app.get("/config/public")
async def get_public_config():
    """API להגדרות ציבוריות"""
    return {
        "slh_nis": 39,
        "business_group_link": os.environ.get("COMMUNITY_GROUP_LINK", "https://t.me/+HIzvM8sEgh1kNWY0"),
        "paybox_url": os.environ.get("PAYBOX_URL"),
        "bit_url": os.environ.get("BIT_URL"),
        "paypal_url": os.environ.get("PAYPAL_URL")
    }

@app.get("/admin/dashboard")
async def admin_dashboard(token: str = ""):
    """דשבורד ניהול HTML"""
    if not ADMIN_DASH_TOKEN or token != ADMIN_DASH_TOKEN:
        raise HTTPException(status_code=401, detail="Unauthorized")
    
    html_content = """
    <!DOCTYPE html>
    <html dir="rtl">
    <head>
        <title>Admin Dashboard - Buy My Shop</title>
        <meta charset="UTF-8">
        <style>
            body { font-family: Arial; margin: 20px; }
            .card { border: 1px solid #ddd; padding: 15px; margin: 10px 0; border-radius: 8px; }
            .stats { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 15px; }
        </style>
    </head>
    <body>
        <h1>Admin Dashboard - Buy My Shop</h1>
        <div id="stats"></div>
        <script>
            fetch('/admin/stats?token=' + new URLSearchParams(window.location.search).get('token'))
                .then(r => r.json())
                .then(data => {
                    document.getElementById('stats').innerHTML = `
                        <div class="stats">
                            <div class="card">משתמשים: ${data.payments_stats?.total || 0}</div>
                            <div class="card">אושרו: ${data.payments_stats?.approved || 0}</div>
                            <div class="card">ממתינים: ${data.payments_stats?.pending || 0}</div>
                        </div>
                    `;
                });
        </script>
    </body>
    </html>
    """
    return HTMLResponse(html_content)

@app.post("/api/telegram-login")
async def handle_telegram_login(user_data: dict):
    """מטפל בהתחברות מטלגרם"""
    try:
        print(f"🔐 Telegram login: {user_data}")
        
        # כאן תוכל לשמור את המשתמש ב-DB
        if DB_AVAILABLE:
            try:
                from db import store_user
                store_user(
                    user_id=user_data['id'],
                    username=user_data.get('username'),
                    first_name=user_data.get('first_name'),
                    last_name=user_data.get('last_name')
                )
            except Exception as e:
                logger.error(f"Failed to store Telegram user: {e}")
        
        return {
            "status": "success", 
            "message": "Login successful",
            "user_id": user_data['id']
        }
        
    except Exception as e:
        logger.error(f"Telegram login error: {e}")
        return {"status": "error", "message": str(e)}

# =========================
# Routes – Webhook + Health + Admin Stats API
# =========================

@app.post("/webhook")
async def telegram_webhook(request: Request) -> Response:
    """נקודת ה-webhook שטלגרם קורא אליה"""
    data = await request.json()
    update = Update.de_json(data, ptb_app.bot)

    if is_duplicate_update(update):
        logger.warning("Duplicate update_id=%s – ignoring", update.update_id)
        return Response(status_code=HTTPStatus.OK.value)

    await ptb_app.process_update(update)
    return Response(status_code=HTTPStatus.OK.value)

@app.get("/health")
async def health():
    """Healthcheck ל-Railway / ניטור"""
    return {
        "status": "ok",
        "service": "telegram-gateway-community-bot",
        "db": "enabled" if DB_AVAILABLE else "disabled",
    }

@app.get("/admin/stats")
async def admin_stats(token: str = ""):
    """
    דשבורד API קטן לקריאה בלבד.
    להשתמש ב-ADMIN_DASH_TOKEN ב-ENV.
    """
    if not ADMIN_DASH_TOKEN or token != ADMIN_DASH_TOKEN:
        raise HTTPException(status_code=401, detail="Unauthorized")

    if not DB_AVAILABLE:
        return {"db": "disabled"}

    try:
        stats = get_approval_stats()
        monthly = get_monthly_payments(datetime.utcnow().year, datetime.utcnow().month)
        top_ref = get_top_referrers(5)
    except Exception as e:
        logger.error("Failed to get admin stats: %s", e)
        raise HTTPException(status_code=500, detail="DB error")

    return {
        "db": "enabled",
        "payments_stats": stats,
        "monthly_breakdown": monthly,
        "top_referrers": top_ref,
    }

# =========================
# עזרי UI (מקשים)
# =========================

def main_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🚀 הצטרפות לקהילת העסקים (39 ₪)", callback_data="join"),
        ],
        [
            InlineKeyboardButton("💎 מה זה הנכס הדיגיטלי?", callback_data="digital_asset_info"),
        ],
        [
            InlineKeyboardButton("🔗 שתף את שער הקהילה", callback_data="share"),
        ],
        [
            InlineKeyboardButton("🌟 חזון SLH", callback_data="vision"),
        ],
        [
            InlineKeyboardButton("👤 האזור האישי שלי", callback_data="my_area"),
        ],
        [
            InlineKeyboardButton("🆘 תמיכה", callback_data="support"),
        ],
    ])

def payment_methods_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🏦 העברה בנקאית", callback_data="pay_bank"),
        ],
        [
            InlineKeyboardButton("📲 ביט / פייבוקס / PayPal", callback_data="pay_paybox"),
        ],
        [
            InlineKeyboardButton("💎 טלגרם (TON)", callback_data="pay_ton"),
        ],
        [
            InlineKeyboardButton("⬅ חזרה", callback_data="back_main"),
        ],
    ])

def payment_links_keyboard() -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton("📲 תשלום בפייבוקס", url=PAYBOX_URL)],
        [InlineKeyboardButton("📲 תשלום בביט", url=BIT_URL)],
        [InlineKeyboardButton("💳 תשלום ב-PayPal", url=PAYPAL_URL)],
        [InlineKeyboardButton("⬅ חזרה", callback_data="back_main")],
    ]
    return InlineKeyboardMarkup(buttons)

def my_area_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🏦 הגדר פרטי בנק", callback_data="set_bank"),
        ],
        [
            InlineKeyboardButton("👥 הגדר קבוצות", callback_data="set_groups"),
        ],
        [
            InlineKeyboardButton("📊 הצג נכס דיגיטלי", callback_data="show_asset"),
        ],
        [
            InlineKeyboardButton("⬅ חזרה", callback_data="back_main"),
        ],
    ])

def support_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("קבוצת תמיכה", url=SUPPORT_GROUP_LINK),
        ],
        [
            InlineKeyboardButton("פניה למתכנת", url=f"tg://user?id={DEVELOPER_USER_ID}"),
        ],
        [
            InlineKeyboardButton("⬅ חזרה", callback_data="back_main"),
        ],
    ])

def admin_approval_keyboard(user_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ אשר תשלום", callback_data=f"adm_approve:{user_id}"),
            InlineKeyboardButton("❌ דחה תשלום", callback_data=f"adm_reject:{user_id}"),
        ],
    ])

# =========================
# Handlers – לוגיקת הבוט
# =========================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.message or update.effective_message
    if not message:
        return

    user = update.effective_user

    # לוג ל-DB ולקבוצת הלוגים
    if DB_AVAILABLE and user:
        try:
            store_user(user.id, user.username)
            incr_metric("total_starts")
        except Exception as e:
            logger.error("Failed to store user: %s", e)

    # טיפול ב-referral
    if message.text and message.text.startswith("/start") and user:
        parts = message.text.split()
        if len(parts) > 1 and parts[1].startswith("ref_"):
            try:
                referrer_id = int(parts[1].split("ref_")[1])
                if DB_AVAILABLE and referrer_id != user.id:
                    add_referral(referrer_id, user.id, source="bot_start")
                    logger.info("Referral added: %s -> %s", referrer_id, user.id)
            except Exception as e:
                logger.error("Failed to add referral: %s", e)

    # לוג לקבוצת התשלומים
    if PAYMENTS_LOG_CHAT_ID and update.effective_user:
        try:
            user = update.effective_user
            username_str = f"@{user.username}" if user.username else "(ללא username)"
            log_text = (
                "🚀 *הפעלת בוט חדשה - Buy_My_Shop*\n\n"
                f"👤 user_id: `{user.id}`\n"
                f"📛 username: {username_str}\n"
                f"💬 chat_id: `{update.effective_chat.id}`\n"
                f"🕐 זמן: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
            )
            await context.bot.send_message(
                chat_id=PAYMENTS_LOG_CHAT_ID,
                text=log_text,
                parse_mode="Markdown",
            )
        except Exception as e:
            logger.error("Failed to send /start log to payments group: %s", e)

    # שליחת הודעת ברוכים הבאים
    text = (
        "🎉 *ברוך הבא לנכס הדיגיטלי המניב שלך!*\n\n"
        
        "💎 *מה זה הנכס הדיגיטלי?*\n"
        "זהו שער כניסה אישי לקהילת עסקים פעילה. לאחר רכישה תקבל:\n"
        "• לינק אישי להפצה\n"
        "• אפשרות למכור את הנכס הלאה\n"
        "• גישה לקבוצת משחק כללית\n"
        "• מערכת הפניות מתגמלת\n\n"
        
        "🔄 *איך זה עובד?*\n"
        "1. רוכשים נכס ב-39₪\n"
        "2. מקבלים לינק אישי\n"
        "3. מפיצים - כל רכישה דרך הלינק שלך מתועדת\n"
        "4. מרוויחים מהפצות נוספות\n\n"
        
        "🚀 *מה תקבל?*\n"
        "✅ גישה לקהילת עסקים\n"
        "✅ נכס דיגיטלי אישי\n"
        "✅ לינק הפצה ייחודי\n"
        "✅ אפשרות מכירה חוזרת\n"
        "✅ מערכת הפניות שקופה\n\n"
        
        "💼 *הנכס שלך - העסק שלך!*"
    )

    await message.reply_text(
        text,
        parse_mode="Markdown",
        reply_markup=main_menu_keyboard(),
    )

async def digital_asset_info(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    text = (
        "💎 *הנכס הדיגיטלי - ההזדמנות העסקית שלך!*\n\n"
        
        "🏗 *מה זה בעצם?*\n"
        "נכס דיגיטלי הוא 'שער כניסה' אישי שאתה קונה פעם אחת ב-39₪ ומקבל:\n"
        "• לינק אישי משלך\n"
        "• זכות למכור נכסים נוספים\n"
        "• גישה למערכת שלמה\n\n"
        
        "💸 *איך מרוויחים?*\n"
        "1. אתה רוכש נכס ב-39₪\n"
        "2. מקבל לינק אישי להפצה\n"
        "3 *כל אדם* שקונה דרך הלינק שלך - הרכישה מתועדת לזכותך\n"
        "4. הנכס שלך ממשיך להניב הכנסות\n\n"
        
        "🔄 *מודל מכירה חוזרת:*\n"
        "אתה לא רק 'משתמש' - אתה 'בעל נכס'!\n"
        "יכול למכור נכסים נוספים לאחרים\n"
        "כל רכישה נוספת מתועדת בשרשרת ההפניה\n\n"
        
        "📈 *יתרונות:*\n"
        "• הכנסה פסיבית מהפצות\n"
        "• נכס ששווה יותר עם הזמן\n"
        "• קהילה תומכת\n"
        "• שקיפות מלאה\n\n"
        
        "🎯 *המטרה:* ליצור רשת עסקית where everyone wins!"
    )

    await query.edit_message_text(
        text,
        parse_mode="Markdown",
        reply_markup=main_menu_keyboard(),
    )

async def join_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    text = (
        "🔑 *רכישת הנכס הדיגיטלי - 39₪*\n\n"
        "בתמורה ל-39₪ תקבל:\n"
        "• נכס דיגיטלי אישי\n"
        "• לינק הפצה ייחודי\n"
        "• גישה לקהילת עסקים\n"
        "• אפשרות למכור נכסים נוספים\n\n"
        
        "🔄 *איך התהליך עובד?*\n"
        "1. בוחרים אמצעי תשלום\n"
        "2. משלמים 39₪\n"
        "3. שולחים אישור תשלום\n"
        "4. מקבלים אישור + לינק אישי\n"
        "5. מתחילים להפיץ!\n\n"
        
        "💼 *זכור:* אתה קונה *נכס* - לא רק 'גישה'!"
    )

    await query.edit_message_text(
        text,
        parse_mode="Markdown",
        reply_markup=payment_methods_keyboard(),
    )

async def my_area_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    user = update.effective_user
    if not user:
        return

    if DB_AVAILABLE:
        summary = get_promoter_summary(user.id)
        if summary:
            personal_link = build_personal_share_link(user.id)
            bank = summary.get("bank_details") or "לא הוגדר"
            p_group = summary.get("personal_group_link") or "לא הוגדר"
            total_ref = summary.get("total_referrals", 0)
            
            text = (
                "👤 *האזור האישי שלך*\n\n"
                f"🔗 *לינק אישי:*\n`{personal_link}`\n\n"
                f"🏦 *פרטי בנק:*\n{bank}\n\n"
                f"👥 *קבוצה אישית:*\n{p_group}\n\n"
                f"📊 *הפניות:* {total_ref}\n\n"
                "*ניהול נכס:*"
            )
        else:
            text = (
                "👤 *האזור האישי שלך*\n\n"
                "עדיין אין לך נכס דיגיטלי.\n"
                "רכש נכס כדי לקבל:\n"
                "• לינק אישי להפצה\n"
                "• אפשרות למכור נכסים\n"
                "• גישה למערכת המלאה"
            )
    else:
        text = "מערכת הזמנית לא זמינת. נסה שוב מאוחר יותר."

    await query.edit_message_text(
        text,
        parse_mode="Markdown",
        reply_markup=my_area_keyboard(),
    )

async def set_bank_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    text = (
        "🏦 *הגדרת פרטי בנק*\n\n"
        "לאחר אישור התשלום, תוכל להגדיר כאן את פרטי הבנק שלך.\n"
        "פרטים אלה ישמשו לקבלת תשלומים מהפצות שלך.\n\n"
        "*פורמט מומלץ:*\n"
        "בנק XXX, סניף XXX, חשבון XXX, שם המוטב"
    )

    await query.edit_message_text(
        text,
        parse_mode="Markdown",
        reply_markup=my_area_keyboard(),
    )

async def set_groups_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    text = (
        "👥 *הגדרת קבוצות*\n\n"
        "כבעל נכס דיגיטלי, תוכל להגדיר:\n"
        "• קבוצה אישית ללקוחות שלך\n"
        "• קבוצת משחק/קהילה\n\n"
        "הקבוצות יוצגו בנכס הדיגיטלי שלך."
    )

    await query.edit_message_text(
        text,
        parse_mode="Markdown",
        reply_markup=my_area_keyboard(),
    )

async def payment_method_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    data = query.data

    method_text = ""
    if data == "pay_bank":
        method_text = BANK_DETAILS
    elif data == "pay_paybox":
        method_text = "📲 *תשלום בביט / פייבוקס / PayPal*"
    elif data == "pay_ton":
        method_text = "💎 *תשלום ב-TON*"

    text = (
        f"{method_text}\n\n"
        "💎 *לאחר התשלום:*\n"
        "1. שלח צילום מסך של האישור\n"
        "2. נאשר בתוך זמן קצר\n"
        "3. תקבל את הנכס הדיגיטלי שלך\n"
        "4. תוכל להתחיל להפיץ ולהרוויח!\n\n"
        "*זכור:* אתה רוכש *נכס* - לא רק גישה!"
    )

    await query.edit_message_text(
        text,
        parse_mode="Markdown",
        reply_markup=payment_links_keyboard(),
    )

async def handle_payment_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.message
    if not message or not message.photo:
        return

    user = update.effective_user
    chat_id = message.chat_id
    username = f"@{user.username}" if user and user.username else "(ללא username)"

    pay_method = context.user_data.get("last_pay_method", "unknown")
    pay_method_text = {
        "bank": "העברה בנקאית",
        "paybox": "ביט / פייבוקס / PayPal",
        "ton": "טלגרם (TON)",
        "unknown": "לא ידוע",
    }.get(pay_method, "לא ידוע")

    # לוג ל-DB
    if DB_AVAILABLE:
        try:
            log_payment(user.id, username, pay_method_text)
        except Exception as e:
            logger.error("Failed to log payment to DB: %s", e)

    # שליחת אישור לקבוצת הלוגים
    photo = message.photo[-1]
    file_id = photo.file_id

    payments = get_payments_store(context)
    payments[user.id] = {
        "file_id": file_id,
        "pay_method": pay_method_text,
        "username": username,
        "chat_id": chat_id,
    }

    caption_log = (
        "💰 *אישור תשלום חדש התקבל!*\n\n"
        f"👤 user_id: `{user.id}`\n"
        f"📛 username: {username}\n"
        f"💳 שיטת תשלום: {pay_method_text}\n"
        f"🕐 זמן: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
        "*פעולות:*"
    )

    try:
        await context.bot.send_photo(
            chat_id=PAYMENTS_LOG_CHAT_ID,
            photo=file_id,
            caption=caption_log,
            parse_mode="Markdown",
            reply_markup=admin_approval_keyboard(user.id),
        )
    except Exception as e:
        logger.error("Failed to send payment to log group: %s", e)

    await message.reply_text(
        "✅ *אישור התשלום התקבל!*\n\n"
        "האישור נשלח לצוות שלנו לאימות.\n"
        "תקבל הודעה עם הנכס הדיגיטלי שלך בתוך זמן קצר.\n\n"
        "💎 *מה תקבל לאחר אישור:*\n"
        "• לינק אישי להפצה\n"
        "• גישה לקהילה\n"
        "• אפשרות למכור נכסים נוספים",
        parse_mode="Markdown",
    )

async def do_approve(target_id: int, context: ContextTypes.DEFAULT_TYPE, source_message) -> None:
    personal_link = build_personal_share_link(target_id)
    
    # הודעת אישור למשתמש
    approval_text = (
        "🎉 *התשלום אושר! ברוך הבא לבעלי הנכסים!*\n\n"
        
        "💎 *הנכס הדיגיטלי שלך מוכן:*\n"
        f"🔗 *לינק אישי:* `{personal_link}`\n\n"
        
        "🚀 *מה עכשיו?*\n"
        "1. שתף את הלינק עם אחרים\n"
        "2. כל רכישה דרך הלינק שלך מתועדת\n"
        "3. תוכל למכור נכסים נוספים\n"
        "4. צבור הכנסה מהפצות\n\n"
        
        "👥 *גישה לקהילה:*\n"
        f"{COMMUNITY_GROUP_LINK}\n\n"
        
        "💼 *ניהול הנכס:*\n"
        "השתמש בכפתור '👤 האזור האישי שלי'\n"
        "כדי להגדיר פרטי בנק וקבוצות"
    )

    try:
        await context.bot.send_message(chat_id=target_id, text=approval_text, parse_mode="Markdown")
        
        # עדכון DB
        if DB_AVAILABLE:
            try:
                update_payment_status(target_id, "approved", None)
                ensure_promoter(target_id)
                incr_metric("approved_payments")
            except Exception as e:
                logger.error("Failed to update DB: %s", e)

        if source_message:
            await source_message.reply_text(f"✅ אושר למשתמש {target_id} - נשלח נכס דיגיטלי")
            
    except Exception as e:
        logger.error("Failed to send approval: %s", e)

async def do_reject(target_id: int, reason: str, context: ContextTypes.DEFAULT_TYPE, source_message) -> None:
    rejection_text = (
        "❌ *אישור התשלום נדחה*\n\n"
        f"*סיבה:* {reason}\n\n"
        "אם לדעתך מדובר בטעות, פנה לתמיכה."
    )
    
    try:
        await context.bot.send_message(chat_id=target_id, text=rejection_text, parse_mode="Markdown")
        
        if DB_AVAILABLE:
            try:
                update_payment_status(target_id, "rejected", reason)
            except Exception as e:
                logger.error("Failed to update DB: %s", e)
                
        if source_message:
            await source_message.reply_text(f"❌ נדחה למשתמש {target_id}")
            
    except Exception as e:
        logger.error("Failed to send rejection: %s", e)

# =========================
# Admin handlers
# =========================

async def admin_approve_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    admin = query.from_user

    if admin.id not in ADMIN_IDS:
        await query.answer("אין הרשאה", show_alert=True)
        return

    data = query.data or ""
    try:
        _, user_id_str = data.split(":", 1)
        target_id = int(user_id_str)
    except Exception:
        await query.answer("שגיאה", show_alert=True)
        return

    await do_approve(target_id, context, query.message)

async def admin_reject_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    admin = query.from_user

    if admin.id not in ADMIN_IDS:
        await query.answer("אין הרשאה", show_alert=True)
        return

    data = query.data or ""
    try:
        _, user_id_str = data.split(":", 1)
        target_id = int(user_id_str)
    except Exception:
        await query.answer("שגיאה", show_alert=True)
        return

    pending = get_pending_rejects(context)
    pending[admin.id] = target_id

    await query.message.reply_text(
        f"❌ דחייה למשתמש {target_id}\nשלח סיבה:"
    )

async def admin_reject_reason_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if user is None or user.id not in ADMIN_IDS:
        return

    pending = get_pending_rejects(context)
    if user.id not in pending:
        return

    target_id = pending.pop(user.id)
    reason = update.message.text.strip()
    await do_reject(target_id, reason, context, update.effective_message)

# =========================
# Back handlers
# =========================

async def back_main_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    fake_update = Update(update_id=update.update_id, message=query.message)
    await start(fake_update, context)

async def support_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    text = (
        "🆘 *תמיכה ועזרה*\n\n"
        "בכל שלב אפשר לקבל עזרה באחד הערוצים הבאים:\n\n"
        f"• קבוצת תמיכה: {SUPPORT_GROUP_LINK}\n"
        f"• פניה ישירה למתכנת המערכת: `tg://user?id={DEVELOPER_USER_ID}`\n\n"
        "או חזור לתפריט הראשי:"
    )

    await query.edit_message_text(
        text,
        parse_mode="Markdown",
        reply_markup=support_keyboard(),
    )

async def share_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    user = update.effective_user
    if not user:
        return

    # בדיקה אם יש למשתמש כבר נכס
    has_asset = False
    if DB_AVAILABLE:
        try:
            from db import get_promoter_summary
            summary = get_promoter_summary(user.id)
            has_asset = summary is not None
        except:
            has_asset = False

    if has_asset:
        # אם יש לו נכס - הלינק האישי שלו
        personal_link = build_personal_share_link(user.id)
        text = (
            "🔗 *שתף את שער הקהילה*\n\n"
            "הלינק האישי שלך להפצה:\n"
            f"`{personal_link}`\n\n"
            "מומלץ לשתף בסטורי / סטטוס / קבוצות, ולהוסיף כמה מילים אישיות משלך.\n"
            "כל מי שייכנס דרך הלינק וילחץ על Start בבוט – יעבור דרך שער הקהילה שלך."
        )
    else:
        # אם אין לו נכס - הלינק הכללי + הסבר על 39 שיתופים
        text = (
            "🔗 *שתף את שער הקהילה*\n\n"
            "כדי להזמין חברים לקהילה, אפשר לשלוח להם את הקישור הבא:\n"
            f"{LANDING_URL}\n\n"
            
            "💝 *אפשרות צדקה - 39 שיתופים*\n"
            "לאחר 39 שיתופים איכותיים של הקישור, תוכל לקבל גישה מלאה לקהילה ללא תשלום!\n"
            "זו הזדמנות גם למי שידו אינה משגת להצטרף ולצמוח איתנו.\n\n"
            
            "📢 *איך לשתף:*\n"
            "מומלץ לשתף בסטורי / סטטוס / קבוצות\n"
            "ולהוסיף כמה מילים אישיות משלך.\n\n"
            
            "*כל מי שייכנס דרך הלינק וילחץ על Start בבוט - יעבור דרך שער הקהילה.*"
        )

    await query.message.reply_text(
        text,
        parse_mode="Markdown",
    )

async def vision_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    text = (
        "🌟 *Human Capital Protocol - SLH*\n\n"
        
        "💫 *מה זה SLH במשפט אחד?*\n"
        "SLH הוא פרוטוקול הון אנושי שמחבר בין משפחות, קהילות ומומחים לרשת כלכלית אחת "
        "– עם בוטים, חנויות, טוקן SLH, אקדמיה, משחק, ו־Exchange – כך שכל אדם יכול להפוך "
        "לעסק, למומחה ולצומת כלכלי, מתוך הטלפון שלו.\n\n"
        
        "🎯 *החזון ארוך־טווח:*\n"
        "• להפוך כל אדם ומשפחה ליחידת כלכלה עצמאית\n"
        "• לבנות רשת מסחר גלובלית מבוזרת\n"
        "• ליצור Meta-Economy: שכבת־על טכנולוגית\n"
        "• להפוך את SLH לסטנדרט עולמי למדידת מומחיות\n\n"
        
        "🏗 *האקו־סיסטם המלא:*\n"
        "• 🤖 Bots Layer - בוטי טלגרם\n"
        "• 🛒 Commerce Layer - חנויות ומרקטפלייס\n"
        "• ⛓️ Blockchain Layer - BSC + TON\n"
        "• 🎓 Expertise Layer - Pi Index\n"
        "• 🎮 Academy Layer - למידה ומשחק\n"
        "• 💱 Exchange Layer - מסחר ונזילות\n\n"
        
        "🚀 *Human Capital Protocol*\n"
        "SLH אינו עוד 'אפליקציה' אלא Meta-Protocol: כמו HTTP / Email לכלכלת משפחה וקהילה. "
        "אנשים הם האלגוריתם, המערכת רק מודדת ומתגמלת.\n\n"
        "*ידע = הון | משפחות = נכסים | קהילות = רשתות | אנשים = פרוטוקול*"
    )

    await query.edit_message_text(
        text,
        parse_mode="Markdown",
        reply_markup=main_menu_keyboard(),
    )

# =========================
# Additional command handlers
# =========================

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """עזרה בסיסית"""
    message = update.message or update.effective_message
    if not message:
        return

    text = (
        "/start – התחלה מחדש ותפריט ראשי\n"
        "/help – עזרה\n\n"
        "אחרי ביצוע תשלום – שלח צילום מסך של האישור לבוט.\n\n"
        "לשיתוף שער הקהילה: כפתור '🔗 שתף את שער הקהילה' בתפריט הראשי.\n\n"
        "למארגנים / אדמינים:\n"
        "/admin – תפריט אדמין\n"
        "/leaderboard – לוח מפנים (Top 10)\n"
        "/payments_stats – סטטיסטיקות תשלומים\n"
        "/reward_slh <user_id> <points> <reason> – יצירת Reward ל-SLH\n"
        "/approve <user_id> – אישור תשלום\n"
        "/reject <user_id> <סיבה> – דחיית תשלום\n"
        "או שימוש בכפתורי האישור/דחייה ליד כל תשלום בלוגים."
    )

    await message.reply_text(text)

async def admin_menu_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """פקודת /admin – תפריט אדמין"""
    if update.effective_user is None or update.effective_user.id not in ADMIN_IDS:
        await update.effective_message.reply_text(
            "אין לך הרשאה לתפריט אדמין.\n"
            "אם אתה צריך גישה – דבר עם המתכנת: @OsifEU"
        )
        return

    text = (
        "🛠 *תפריט אדמין – Buy My Shop*\n\n"
        "בחר אחת מהאפשרויות:\n"
        "• סטטוס מערכת (DB, Webhook, לינקים)\n"
        "• מוני תמונת שער (כמה פעמים הוצגה/נשלחה)\n"
        "• רעיונות לפיצ'רים עתידיים לבוט\n\n"
        "פקודות נוספות:\n"
        "/leaderboard – לוח מפנים\n"
        "/payments_stats – דוח תשלומים\n"
        "/reward_slh – יצירת Reward SLH\n"
    )

    await update.effective_message.reply_text(
        text,
        parse_mode="Markdown",
    )

async def approve_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """אישור תשלום למשתמש: /approve <user_id>"""
    if update.effective_user is None or update.effective_user.id not in ADMIN_IDS:
        await update.effective_message.reply_text(
            "אין לך הרשאה לבצע פעולה זו.\n"
            "אם אתה חושב שזו טעות – דבר עם המתכנת: @OsifEU"
        )
        return

    if not context.args:
        await update.effective_message.reply_text("שימוש: /approve <user_id>")
        return

    try:
        target_id = int(context.args[0])
    except ValueError:
        await update.effective_message.reply_text("user_id חייב להיות מספרי.")
        return

    await do_approve(target_id, context, update.effective_message)

async def reject_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """דחיית תשלום למשתמש: /reject <user_id> <סיבה>"""
    if update.effective_user is None or update.effective_user.id not in ADMIN_IDS:
        await update.effective_message.reply_text(
            "אין לך הרשאה לבצע פעולה זו.\n"
            "אם אתה חושב שזו טעות – דבר עם המתכנת: @OsifEU"
        )
        return

    if len(context.args) < 2:
        await update.effective_message.reply_text("שימוש: /reject <user_id> <סיבה>")
        return

    try:
        target_id = int(context.args[0])
    except ValueError:
        await update.effective_message.reply_text("user_id חייב להיות מספרי.")
        return

    reason = " ".join(context.args[1:])
    await do_reject(target_id, reason, context, update.effective_message)

async def admin_leaderboard_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """לוח מפנים – /leaderboard"""
    if update.effective_user is None or update.effective_user.id not in ADMIN_IDS:
        await update.effective_message.reply_text(
            "אין לך הרשאה לצפות בלוח המפנים.\n"
            "אם אתה חושב שזו טעות – דבר עם המתכנת: @OsifEU"
        )
        return

    if not DB_AVAILABLE:
        await update.effective_message.reply_text("DB לא פעיל כרגע.")
        return

    try:
        rows = get_top_referrers(10)
    except Exception as e:
        logger.error("Failed to get top referrers: %s", e)
        await update.effective_message.reply_text("שגיאה בקריאת נתוני הפניות.")
        return

    if not rows:
        await update.effective_message.reply_text("אין עדיין נתוני הפניות.")
        return

    lines = ["🏆 *לוח מפנים – Top 10* \n"]
    rank = 1
    for row in rows:
        rid = row["referrer_id"]
        uname = row["username"] or f"ID {rid}"
        total = row["total_referrals"]
        lines.append(f"{rank}. {uname} – {total} הפניות")
        rank += 1

    await update.effective_message.reply_text(
        "\n".join(lines),
        parse_mode="Markdown",
    )

async def admin_payments_stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """דוח תשלומים – /payments_stats"""
    if update.effective_user is None or update.effective_user.id not in ADMIN_IDS:
        await update.effective_message.reply_text(
            "אין לך הרשאה לצפות בסטטיסטיקות.\n"
            "אם אתה צריך גישה – דבר עם המתכנת: @OsifEU"
        )
        return

    if not DB_AVAILABLE:
        await update.effective_message.reply_text("DB לא פעיל כרגע.")
        return

    now = datetime.utcnow()
    year = now.year
    month = now.month

    try:
        stats = get_approval_stats()
    except Exception as e:
        logger.error("Failed to get payment stats: %s", e)
        await update.effective_message.reply_text("שגיאה בקריאת נתוני תשלום.")
        return

    lines = [f"📊 *דוח תשלומים – {month:02d}/{year}* \n"]

    if stats and stats.get("total", 0) > 0:
        total = stats["total"]
        approved = stats["approved"]
        rejected = stats["rejected"]
        pending = stats["pending"]
        approval_rate = round(approved * 100 / total, 1) if total else 0.0
        lines.append("\n*סטטוס כללי:*")
        lines.append(f"- אושרו: {approved}")
        lines.append(f"- נדחו: {rejected}")
        lines.append(f"- ממתינים: {pending}")
        lines.append(f"- אחוז אישור: {approval_rate}%")
    else:
        lines.append("\nאין עדיין נתונים כלליים.")

    await update.effective_message.reply_text(
        "\n".join(lines),
        parse_mode="Markdown",
    )

async def admin_reward_slh_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    יצירת Reward ידני למשתמש – לדוגמה:
    /reward_slh <user_id> <points> <reason...>
    """
    if update.effective_user is None or update.effective_user.id not in ADMIN_IDS:
        await update.effective_message.reply_text(
            "אין לך הרשאה ליצור Rewards.\n"
            "אם אתה צריך גישה – דבר עם המתכנת: @OsifEU"
        )
        return

    if not DB_AVAILABLE:
        await update.effective_message.reply_text("DB לא פעיל כרגע.")
        return

    if len(context.args) < 3:
        await update.effective_message.reply_text(
            "שימוש: /reward_slh <user_id> <points> <reason...>"
        )
        return

    try:
        target_id = int(context.args[0])
        points = int(context.args[1])
    except ValueError:
        await update.effective_message.reply_text("user_id ו-points חייבים להיות מספריים.")
        return

    reason = " ".join(context.args[2:])

    try:
        create_reward(target_id, "SLH", reason, points)
    except Exception as e:
        logger.error("Failed to create reward: %s", e)
        await update.effective_message.reply_text("שגיאה ביצירת Reward.")
        return

    # הודעה למשתמש (עדיין ללא mint אמיתי – לוגי)
    try:
        await update.effective_message.reply_text(
            f"נוצר Reward SLH למשתמש {target_id} ({points} נק׳): {reason}"
        )

        await ptb_app.bot.send_message(
            chat_id=target_id,
            text=(
                "🎁 קיבלת Reward על הפעילות שלך בקהילה!\n\n"
                f"סוג: *SLH* ({points} נק׳)\n"
                f"סיבה: {reason}\n\n"
                "Reward זה יאסף למאזן שלך ויאפשר הנפקת מטבעות/נכסים "
                "דיגיטליים לפי המדיניות שתפורסם בקהילה."
            ),
            parse_mode="Markdown",
        )
    except Exception as e:
        logger.error("Failed to notify user about reward: %s", e)

async def my_bot_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    מציג למשתמש מידע על הנכס הדיגיטלי שלו (אם קיים).
    """
    user = update.effective_user
    if user is None:
        return

    if not DB_AVAILABLE:
        await update.effective_message.reply_text("DB לא פעיל כרגע, נסה מאוחר יותר.")
        return

    summary = get_promoter_summary(user.id)
    personal_link = build_personal_share_link(user.id)

    if not summary:
        await update.effective_message.reply_text(
            "כרגע עדיין לא רשום לך נכס דיגיטלי כמקדם.\n"
            "אם ביצעת תשלום והתקבל אישור – נסה שוב בעוד מספר דקות."
        )
        return

    bank = summary.get("bank_details") or "לא הוגדר"
    p_group = summary.get("personal_group_link") or "לא הוגדר"
    g_group = summary.get("global_group_link") or "לא הוגדר"
    total_ref = summary.get("total_referrals", 0)
    approved_ref = summary.get("approved_referrals", 0)

    text = (
        "📌 *הנכס הדיגיטלי שלך – שער קהילה אישי*\n\n"
        f"🔗 *קישור אישי להפצה:*\n{personal_link}\n\n"
        f"🏦 *פרטי בנק לקבלת תשלום:*\n"
        f"{bank}\n\n"
        f"👥 *קבוצת לקוחות פרטית:*\n"
        f"{p_group}\n\n"
        f"👥 *קבוצת משחק/קהילה כללית:*\n"
        f"{g_group}\n\n"
        f"📊 *סטטוס פעילות:*\n"
        f"- סה\"כ הפניות רשומות: {total_ref}\n"
        f"- מהן אושרו עם תשלום: {approved_ref}\n\n"
        "אפשר לעדכן פרטים בכל רגע עם:\n"
        "/set_bank – עדכון פרטי בנק\n"
        "/set_groups – עדכון קישורי קבוצות"
    )

    await update.effective_message.reply_text(text, parse_mode="Markdown")

async def set_bank_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    עדכון פרטי הבנק של המקדם. שימוש:
    /set_bank <טקסט חופשי עם פרטי החשבון>
    """
    user = update.effective_user
    if user is None:
        return

    if not DB_AVAILABLE:
        await update.effective_message.reply_text("DB לא פעיל כרגע, נסה מאוחר יותר.")
        return

    if not context.args:
        await update.effective_message.reply_text(
            "שלח את הפקודה כך:\n"
            "/set_bank בנק הפועלים, סניף 153, חשבון 73462, המוטב: קאופמן צביקה"
        )
        return

    bank_details = " ".join(context.args).strip()

    # נוודא שקיימת רשומת promoter
    ensure_promoter(user.id)
    update_promoter_settings(user.id, bank_details=bank_details)

    await update.effective_message.reply_text("פרטי הבנק עודכנו בהצלחה ✅")

async def set_groups_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    עדכון קישורי קבוצות. שימוש:
    /set_groups <קישור לקבוצה שלך> <קישור לקבוצת המשחק הכללית (אופציונלי)>
    """
    user = update.effective_user
    if user is None:
        return

    if not DB_AVAILABLE:
        await update.effective_message.reply_text("DB לא פעיל כרגע, נסה מאוחר יותר.")
        return

    if not context.args:
        await update.effective_message.reply_text(
            "שלח את הפקודה כך:\n"
            "/set_groups <קישור לקבוצת הלקוחות שלך> <קישור לקבוצת המשחק הכללית (אופציונלי)>"
        )
        return

    personal_group_link = context.args[0]
    global_group_link = context.args[1] if len(context.args) > 1 else None

    ensure_promoter(user.id)
    update_promoter_settings(
        user.id,
        personal_group_link=personal_group_link,
        global_group_link=global_group_link,
    )

    await update.effective_message.reply_text("קישורי הקבוצות עודכנו בהצלחה ✅")

# =========================
# רישום handlers
# =========================

ptb_app.add_handler(CommandHandler("start", start))
ptb_app.add_handler(CommandHandler("help", help_command))
ptb_app.add_handler(CommandHandler("admin", admin_menu_command))
ptb_app.add_handler(CommandHandler("approve", approve_command))
ptb_app.add_handler(CommandHandler("reject", reject_command))
ptb_app.add_handler(CommandHandler("leaderboard", admin_leaderboard_command))
ptb_app.add_handler(CommandHandler("payments_stats", admin_payments_stats_command))
ptb_app.add_handler(CommandHandler("reward_slh", admin_reward_slh_command))
ptb_app.add_handler(CommandHandler("my_bot", my_bot_command))
ptb_app.add_handler(CommandHandler("set_bank", set_bank_command))
ptb_app.add_handler(CommandHandler("set_groups", set_groups_command))

ptb_app.add_handler(CallbackQueryHandler(digital_asset_info, pattern="^digital_asset_info$"))
ptb_app.add_handler(CallbackQueryHandler(join_callback, pattern="^join$"))
ptb_app.add_handler(CallbackQueryHandler(support_callback, pattern="^support$"))
ptb_app.add_handler(CallbackQueryHandler(share_callback, pattern="^share$"))
ptb_app.add_handler(CallbackQueryHandler(vision_callback, pattern="^vision$"))
ptb_app.add_handler(CallbackQueryHandler(back_main_callback, pattern="^back_main$"))
ptb_app.add_handler(CallbackQueryHandler(payment_method_callback, pattern="^pay_"))
ptb_app.add_handler(CallbackQueryHandler(my_area_callback, pattern="^my_area$"))
ptb_app.add_handler(CallbackQueryHandler(set_bank_callback, pattern="^set_bank$"))
ptb_app.add_handler(CallbackQueryHandler(set_groups_callback, pattern="^set_groups$"))
ptb_app.add_handler(CallbackQueryHandler(admin_approve_callback, pattern="^adm_approve:"))
ptb_app.add_handler(CallbackQueryHandler(admin_reject_callback, pattern="^adm_reject:"))

# כל תמונה בפרטי – נניח כאישור תשלום
ptb_app.add_handler(MessageHandler(filters.PHOTO & filters.ChatType.PRIVATE, handle_payment_photo))

# הודעת טקסט מאדמין – אם יש דחייה ממתינה
ptb_app.add_handler(MessageHandler(filters.TEXT & filters.User(list(ADMIN_IDS)), admin_reject_reason_handler))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
