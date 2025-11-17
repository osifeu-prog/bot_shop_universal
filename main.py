from telegram.ext import MessageHandler, filters
import os
import json
import logging
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, InputFile
from pathlib import Path
from typing import Optional, Dict, Any

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from pydantic import BaseModel

from telegram import Update, InputFile
from telegram.ext import CommandHandler, ContextTypes
from telegram.ext import Application, CommandHandler, ContextTypes

from slh_public_api import router as public_router
from social_api import router as social_router
from slh_core_api import router as core_router  # API ליבה לרפרלים

# =========================
# בסיס לוגינג
# =========================
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("slhnet")

# =========================
# FastAPI app
# =========================
app = FastAPI(title="SLHNET Gateway Bot")

BASE_DIR = Path(__file__).resolve().parent

# סטטיק וטמפלטס
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

# רואטרים של API ציבורי + פיד חברתי + ליבת רפרלים
app.include_router(public_router)
app.include_router(social_router)
app.include_router(core_router)

# =========================
# קובץ referral פשוט (אפשר להעביר ל-DB בהמשך)
# =========================
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)
REF_FILE = DATA_DIR / "referrals.json"


def load_referrals() -> Dict[str, Any]:
    if not REF_FILE.exists():
        return {"users": {}}
    try:
        return json.loads(REF_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {"users": {}}


def save_referrals(data: Dict[str, Any]) -> None:
    REF_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def register_referral(user_id: int, referrer_id: Optional[int]) -> None:
    data = load_referrals()
    suid = str(user_id)
    if suid in data["users"]:
        return  # כבר רשום
    data["users"][suid] = {
        "referrer": str(referrer_id) if referrer_id else None,
    }
    save_referrals(data)


# =========================
# קריאת טקסטים של /start ו-/investor מתוך docs/bot_messages_slhnet.txt
# =========================

DOCS_MSG_FILE = BASE_DIR / "docs" / "bot_messages_slhnet.txt"


class BotTexts(BaseModel):
    start: str
    investor: str


def load_bot_texts() -> BotTexts:
    default_start = (
        "ברוך הבא לשער הכניסה ל-SLHNET \n"
        "קהילת עסקים, טוקן SLH, חנויות דיגיטליות ושיווק חכם."
    )
    default_investor = (
        "מידע למשקיעים: SLHNET בונה אקו-סיסטם חברתי-פיננסי שקוף, "
        "עם מודל הפניות מדורג וצמיחה אורגנית."
    )

    if not DOCS_MSG_FILE.exists():
        return BotTexts(start=default_start, investor=default_investor)

    content = DOCS_MSG_FILE.read_text(encoding="utf-8")
    start_block = []
    investor_block = []
    current = None

    for line in content.splitlines():
        stripped = line.strip()
        if stripped == "[START]":
            current = "start"
            continue
        if stripped == "[INVESTOR]":
            current = "investor"
            continue
        if current == "start":
            start_block.append(line)
        elif current == "investor":
            investor_block.append(line)

    start_text = "\n".join(start_block).strip() or default_start
    investor_text = "\n".join(investor_block).strip() or default_investor
    return BotTexts(start=start_text, investor=investor_text)


BOT_TEXTS = load_bot_texts()

# =========================
# Telegram Application (Webhook mode)
# =========================

telegram_app: Optional[Application] = None


async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    chat_id = update.effective_chat.id if update.effective_chat else None
    if not chat_id or not user:
        return

    # הפניה (deep-link): /start ref_<user_id>
    referrer_id: Optional[int] = None
    if context.args:
        arg = context.args[0]
        if arg.startswith("ref_"):
            try:
                referrer_id = int(arg.replace("ref_", "").strip())
            except ValueError:
                referrer_id = None

    register_referral(user.id, referrer_id)

    # שליחת תמונת שער
    banner_path = BASE_DIR / "assets" / "start_banner.jpg"
    if banner_path.exists():
        try:
            with banner_path.open("rb") as f:
                await context.bot.send_photo(
                    chat_id=chat_id,
                    photo=InputFile(f),
                    caption=" שער הכניסה ל-SLHNET",
                )
        except Exception as e:
            logger.warning("Failed to send start banner: %s", e)

    text = (
        f"{BOT_TEXTS.start}\n\n"
        " תשלום 39  וגישה מלאה  דרך כפתור/קישור שתראה בדף הנחיתה\n"
        " /investor  מידע למשקיעים\n"
        " /whoami  פרטי החיבור שלך (להרחבה בהמשך)"
    )
    await context.bot.send_message(chat_id=chat_id, text=text)


async def investor_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id if update.effective_chat else None
    if not chat_id:
        return

    phone = "058-420-3384"
    tg_link = "https://t.me/Osif83"
    text = (
        f"{BOT_TEXTS.investor}\n\n"
        " יצירת קשר ישירה עם המייסד:\n"
        f"טלפון: {phone}\n"
        f"טלגרם: {tg_link}\n\n"
        "כאן בונים יחד מודל ריפרל שקוף, סטייקינג ופתרונות תשואה על בסיס\n"
        "אקו-סיסטם אמיתי של עסקים, לא על אוויר."
    )
    await context.bot.send_message(chat_id=chat_id, text=text)


async def whoami_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    chat_id = update.effective_chat.id if update.effective_chat else None
    if not chat_id or not user:
        return

    data = load_referrals()
    u = data["users"].get(str(user.id))
    ref = u["referrer"] if u else None

    msg = [
        " פרטי המשתמש שלך:",
        f"user_id: {user.id}",
        f"username: @{user.username}" if user.username else "username: (ללא)",
    ]
    if ref:
        msg.append(f"הופנית ע\"י משתמש: {ref}")
    else:
        msg.append("לא רשום מפנה  ייתכן שאתה השורש או שנכנסת ישירות.")

    await context.bot.send_message(chat_id=chat_id, text="\n".join(msg))


async def init_telegram_app() -> None:
    global telegram_app
    bot_token = os.getenv("BOT_TOKEN")
    ADMIN_ALERT_CHAT_ID = int(os.getenv("ADMIN_ALERT_CHAT_ID", "0") or "0")
    webhook_url = os.getenv("WEBHOOK_URL")

    if not bot_token:
        logger.error("BOT_TOKEN not set  bot will not run")
        return

    telegram_app = Application.builder().token(bot_token).build()
    telegram_app.add_handler(CommandHandler("start", start_slhnet))
    telegram_app.add_handler(CommandHandler("chatid", chatid_handler))
    telegram_app.add_handler(CommandHandler("chatinfo", chatid_handler))
    telegram_app.add_handler(
        MessageHandler(
            filters.COMMAND & filters.Regex(r"^/start(\s|$)"),
            notify_admin_new_user_on_start,
        )
    )
    telegram_app.add_handler(CommandHandler("investor", investor_handler))
    telegram_app.add_handler(CommandHandler("whoami", whoami_handler))

    await telegram_app.initialize()

    if webhook_url:
        try:
            await telegram_app.bot.set_webhook(webhook_url)
            logger.info("Webhook set to %s", webhook_url)
        except Exception as e:
            logger.error("Failed to set webhook: %s", e)
    else:
        logger.warning("WEBHOOK_URL not set  please configure it on Railway.")


@app.on_event("startup")
async def on_startup() -> None:
    logger.info("Starting SLHNET gateway service...")
    await init_telegram_app()
    logger.info("Startup complete.")


@app.get("/health")
async def health() -> Dict[str, Any]:
    db_status = os.getenv("DATABASE_URL")
    return {
        "status": "ok",
        "service": "telegram-gateway-community-bot",
        "db": "enabled" if db_status else "disabled",
    }


@app.post("/webhook")
async def telegram_webhook(request: Request):
    global telegram_app
    if telegram_app is None:
        raise HTTPException(status_code=503, detail="Telegram app not initialized")

    data = await request.json()
    update = Update.de_json(data, telegram_app.bot)
    await telegram_app.process_update(update)
    return JSONResponse({"ok": True})


@app.get("/", response_class=HTMLResponse)
async def landing(request: Request):
    slh_price = float(os.getenv("SLH_NIS", "444"))
    return templates.TemplateResponse(
        "landing.html",
        {
            "request": request,
            "slh_price": slh_price,
        },
    )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat

    title = BOT_TEXTS.get("start_title", "שער הכניסה ל-SLHNET")
    body = BOT_TEXTS.get("start_body", "")

    banner_path = BASE_DIR / START_IMAGE_PATH
    if banner_path.exists():
        try:
            with banner_path.open("rb") as f:
                await context.bot.send_photo(
                    chat_id=chat.id,
                    photo=InputFile(f),
                    caption=title,
                )
        except Exception as e:
            log.warning("failed to send start banner: %s", e)
            await chat.send_message(text=title)
    else:
        await chat.send_message(text=title)

    pay_url = PAYBOX_URL or (LANDING_URL + "#join39")
    more_info_url = LANDING_URL
    group_url = BUSINESS_GROUP_URL or LANDING_URL

    keyboard = [
        [InlineKeyboardButton(" תשלום 39  וגישה מלאה", url=pay_url)],
        [InlineKeyboardButton("ℹ לפרטים נוספים", url=more_info_url)],
        [InlineKeyboardButton(" הצטרפות לקבוצת העסקים", url=group_url)],
        [InlineKeyboardButton(" מידע למשקיעים", callback_data="open_investor")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await chat.send_message(
        text=body
        + "\\n\\n"
        "פקודות נוספות:\\n"
        " /whoami  פרטי החיבור שלך\\n"
        " /investor  מידע למשקיעים\\n"
        " /staking  סטייקינג SLH (פאזה ראשונה)\\n",
        reply_markup=reply_markup,
    )

async def investor(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    body = BOT_TEXTS.get("investor_body", "")

    text = (
        body
        + "\\n\\n"
        "יצירת קשר ישירה עם המייסד:\\n"
        "טלפון: 058-420-3384\\n"
        "טלגרם: https://t.me/Osif83\\n\\n"
        "את כל המבנה האסטרטגי אפשר לראות גם באתר:\\n"
        f"{LANDING_URL}"
    )

    keyboard = [
        [InlineKeyboardButton(" דף נחיתה SLHNET", url=LANDING_URL)],
        [InlineKeyboardButton(" כניסה לבוט", url="https://t.me/Buy_My_Shop_bot")],
    ]
    await chat.send_message(text=text, reply_markup=InlineKeyboardMarkup(keyboard))

async def staking(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    text = (
        " סטייקינג SLH  פאזה 1\\n\\n"
        "אנחנו בונים מנגנון סטייקינג שיתחיל מניקוד על בסיס פעילות וריפרלים,\\n"
        "ויתחבר בהמשך לסטייקינג ישיר על הטוקן SLH ב-BSC.\\n\\n"
        "בשלב זה:\\n"
        " כל הצטרפות דרך הלינק שלך נרשמת ברשת\\n"
        " המידע יוזן למודל סטייקינג/תגמולים שיפורסם בלוח ייעודי\\n\\n"
        f"ברגע שיתחיל הסטייקינג בפועל  הלינק וההסבר המלא יופיעו כאן ובאתר:\\n{LANDING_URL}"
    )
    await chat.send_message(text=text)




async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    שער הכניסה ל-SLHNET – מסך פתיחה שיווקי + כפתורי פעולה.
    לא נוגעים בשאר הלוגיקות של הבוט, רק בכניסה.
    """
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    import os

    chat = update.effective_chat

    landing_url = os.getenv("LANDING_URL", "https://slh-nft.com/")
    paybox_url = os.getenv(
        "PAYBOX_URL",
        "https://links.payboxapp.com/1SNfaJ6XcYb"  # אפשר לעדכן ב-Railway
    )
    business_group_url = os.getenv(
        "BUSINESS_GROUP_URL",
        "https://t.me/+HIzvM8sEgh1kNWY0"
    )
    bot_url = "https://t.me/Buy_My_Shop_bot"

    text = (
        "שער הכניסה ל-SLHNET\\n\\n"
        "ברוך הבא לשער הכניסה ל-SLHNET 🌐\\n"
        "קהילת עסקים, חנויות דיגיטליות וטוקן SLH על Binance Smart Chain.\\n\\n"
        "💎 מה מקבלים בתשלום חד-פעמי של 39 ₪?\\n"
        "• גישה לקבוצת העסקים הסגורה\\n"
        "• נכס דיגיטלי ראשוני (חנות / שער אישי שיורחב בהמשך)\\n"
        "• לינק הפצה אישי שתוכל להרוויח ממנו\\n"
        "• קדימות להטבות, איירדרופים ומודלי סטייקינג עתידיים\\n\\n"
        "🧭 איך מצטרפים?\\n"
        "1. לוחצים על הכפתור 'תשלום 39 ₪ וגישה מלאה'\\n"
        "2. מבצעים תשלום באחד הערוצים הנתמכים (פייבוקס/בנק וכו')\\n"
        "3. שולחים צילום מסך או אישור תשלום לבוט (בהמשך נוסיף אוטומציה מלאה)\\n"
        "4. לאחר אישור – מקבלים קישורים לחנות ולחומרי ההפצה האישיים שלך.\\n\\n"
        "פקודות שימושיות:\\n"
        "• /whoami – פרטי החיבור שלך והאם יש לך מפנה\\n"
        "• /investor – מידע למשקיעים ולשותפים אסטרטגיים\\n"
        "• /staking – מידע על מודל הסטייקינג שנבנה סביב SLHNET\\n"
    )

    keyboard = [
        [InlineKeyboardButton("🔑 תשלום 39 ₪ וגישה מלאה", url=paybox_url)],
        [InlineKeyboardButton("ℹ️ לפרטים נוספים באתר", url=landing_url)],
        [InlineKeyboardButton("💬 הצטרפות לקבוצת העסקים", url=business_group_url)],
        [InlineKeyboardButton("🤖 פתיחת הבוט SLHNET", url=bot_url)],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await chat.send_message(text=text, reply_markup=reply_markup)

async def start_slhnet(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Rich /start entry point for SLHNET:
    - Explains the 39 offer
    - Shows main CTA buttons
    - Serves as the main marketing entry for new users.
    """
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    import os

    chat = update.effective_chat

    landing_url = os.getenv("LANDING_URL", "https://slh-nft.com/")
    paybox_url = os.getenv("PAYBOX_URL", "https://links.payboxapp.com/1SNfaJ6XcYb")
    business_group_url = os.getenv("BUSINESS_GROUP_URL", "https://t.me/+HIzvM8sEgh1kNWY0")
    bot_url = "https://t.me/Buy_My_Shop_bot"

    text = (
        "שער הכניסה ל-SLHNET\\n\\n"
        "מכאן מתחילים: רשת עסקים, טוקן SLH על BSC, חנות דיגיטלית משלך ומודל ריפרל מדורג.\\n\\n"
        "מה מקבלים אחרי תשלום חדפעמי של 39 ?\\n"
        "• קישור אישי לשיתוף והפצה\\n"
        "• פתיחת נכס דיגיטלי ראשון (חנות / פרופיל עסקי)\\n"
        "• גישה לקבוצת העסקים הסגורה\\n"
        "• בסיס לרשת הפניות שמתחילה ממך\\n\\n"
        "איך ממשיכים?\\n"
        "1. לוחצים על 'תשלום 39  וגישה מלאה'\\n"
        "2. מבצעים תשלום באחד הערוצים הזמינים\\n"
        "3. שולחים צילום מסך/אישור תשלום לבוט\\n"
        "4. מקבלים גישה + קישורים אישיים + הוראות הפעלה.\\n\\n"
        "פקודות חשובות:\\n"
        "/whoami  פרטי החיבור שלך\\n"
        "/investor  מידע למשקיעים\\n"
        "/staking  סטטוס סטייקינג ונתוני תשואה (בפיתוח)\\n"
    )

    keyboard = [
        [InlineKeyboardButton("תשלום 39  וגישה מלאה", url=paybox_url)],
        [InlineKeyboardButton("דף נחיתה / פרטים נוספים", url=landing_url)],
        [InlineKeyboardButton("הצטרפות לקבוצת העסקים", url=business_group_url)],
        [InlineKeyboardButton("פתיחת הבוט מחדש", url=bot_url)],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await chat.send_message(text=text, reply_markup=reply_markup)

async def chatid_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    מחזיר פרטי צ'אט  עובד גם בפרטי וגם בקבוצה.
    """
    chat = update.effective_chat
    user = update.effective_user

    text = (
        "📡 פרטי הצ'אט הזה:\n"
        f"chat_id: {chat.id}\n"
        f"type: {chat.type}\n"
        f"title: {chat.title or '-'}\n"
        f"username: @{chat.username or '-'}\n\n"
        f"👤 user_id שלך: {user.id}"
    )

    await update.effective_message.reply_text(text)


async def notify_admin_new_user(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    שליחת התראה לקבוצת אדמינים על משתמש חדש שנכנס לבוט.
    דורש ADMIN_ALERT_CHAT_ID (int) כמשתנה סביבה.
    """
    if not ADMIN_ALERT_CHAT_ID:
        return

    user = update.effective_user
    chat = update.effective_chat

    lines = [
        "👤 משתמש חדש נכנס לבוט Buy_My_Shop",
        "",
        f"user_id: {user.id}",
        f"username: @{user.username}" if user.username else "username: —",
        f"name: {user.full_name}",
        f"from chat_id: {chat.id} ({chat.type})",
    ]

    text = "\n".join(lines)

    try:
        await context.bot.send_message(
            chat_id=ADMIN_ALERT_CHAT_ID,
            text=text,
        )
    except Exception:
        # לא מפילים את הבוט על שגיאה בלוג התראות
        pass


async def notify_admin_new_user_on_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    עוטף את notify_admin_new_user כך שנוכל לחבר אותו ל-MessageHandler של /start
    בלי להפריע ל-CommandHandler("start") הקיים.
    """
    await notify_admin_new_user(update, context)

# === Routers registration (added by PowerShell script) ===
app.include_router(public_api_router)
app.include_router(social_router)
app.include_router(slhnet_extra_router)
app.include_router(slh_core_router)

