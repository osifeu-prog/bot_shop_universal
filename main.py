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
    except Exception as e:
        logger.error(f"Failed to load referrals.json: {e}")
        return {"users": {}}


def save_referrals(data: Dict[str, Any]) -> None:
    try:
        REF_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        logger.error(f"Failed to save referrals.json: {e}")


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
        if stripped == "[/START]":
            current = None
            continue
        if stripped == "[INVESTOR]":
            current = "investor"
            continue
        if stripped == "[/INVESTOR]":
            current = None
            continue
        if current == "start":
            start_block.append(line)
        elif current == "investor":
            investor_block.append(line)

    start_text = "\n".join(start_block).strip() or default_start
    investor_text = "\n".join(investor_block).strip() or default_investor
    return BotTexts(start=start_text, investor=investor_text)


BOT_TEXTS = load_bot_texts().dict()


# =========================
# Telegram Bot integration (Application + Webhook)
# =========================

telegram_app: Optional[Application] = None


async def start_slhnet(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /start הראשי – שער כניסה ל-SLHNET
    לוקח טקסט מתוך docs/bot_messages_slhnet.txt (חלק [START])
    ומוסיף כפתורים לדף נחיתה, הצטרפות לקבוצה ותשלום.
    """
    user = update.effective_user
    chat = update.effective_chat
    if not chat:
        return

    # טיפול ברפררלים מסוג /start ref_123
    args = context.args
    referrer_id: Optional[int] = None
    if args:
        token = args[0]
        if token.startswith("ref_"):
            try:
                referrer_id = int(token.replace("ref_", ""))
            except ValueError:
                referrer_id = None

    if user:
        register_referral(user.id, referrer_id)

    body = BOT_TEXTS.get("start", "")

    landing_url = os.getenv("LANDING_URL", "https://slh-nft.com/")
    paybox_url = os.getenv("PAYBOX_URL", "https://links.payboxapp.com/1SNfaJ6XcYb")
    business_group_url = os.getenv("BUSINESS_GROUP_URL", "https://t.me/+HIzvM8sEgh1kNWY0")
    bot_url = "https://t.me/Buy_My_Shop_bot"

    text = (
        body
        + "\n\n"
        "מה מקבלים אחרי תשלום חד-פעמי של 39₪?\n"
        "• קישור אישי לשיתוף והפצה\n"
        "• פתיחת נכס דיגיטלי ראשון (חנות / פרופיל עסקי)\n"
        "• גישה לקבוצת העסקים הסגורה\n"
        "• בסיס לרשת הפניות שמתחילה ממך\n\n"
        "איך ממשיכים?\n"
        "1. לוחצים על 'תשלום 39₪ וגישה מלאה'\n"
        "2. מבצעים תשלום באחד הערוצים הזמינים\n"
        "3. שולחים צילום מסך/אישור תשלום לבוט\n"
        "4. מקבלים גישה + קישורים אישיים + הוראות הפעלה.\n\n"
        "פקודות חשובות:\n"
        "/whoami – פרטי החיבור שלך\n"
        "/investor – מידע למשקיעים\n"
        "/staking – סטטוס סטייקינג ונתוני תשואה (בפיתוח)\n"
    )

    keyboard = [
        [InlineKeyboardButton("תשלום 39₪ וגישה מלאה", url=paybox_url)],
        [InlineKeyboardButton("דף נחיתה / פרטים נוספים", url=landing_url)],
        [InlineKeyboardButton("הצטרפות לקבוצת העסקים", url=business_group_url)],
        [InlineKeyboardButton("פתיחת הבוט מחדש", url=bot_url)],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await chat.send_message(text=text, reply_markup=reply_markup)


async def investor_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /investor – גרסת handler שנשענת על הטקסט מתוך docs/bot_messages_slhnet.txt
    """
    chat = update.effective_chat
    if not chat:
        return

    body = BOT_TEXTS.get("investor", "")
    landing_url = os.getenv("LANDING_URL", "https://slh-nft.com/")

    text = (
        body
        + "\n\n"
        "יצירת קשר ישירה עם המייסד:\n"
        "טלפון: 058-420-3384\n"
        "טלגרם: https://t.me/Osif83\n\n"
        "את כל המבנה האסטרטגי אפשר לראות גם באתר:\n"
        f"{landing_url}"
    )

    keyboard = [
        [InlineKeyboardButton(" דף נחיתה SLHNET", url=landing_url)],
        [InlineKeyboardButton(" כניסה לבוט", url="https://t.me/Buy_My_Shop_bot")],
    ]
    await chat.send_message(text=text, reply_markup=InlineKeyboardMarkup(keyboard))


async def staking(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    if not chat:
        return

    text = (
        "סטייקינג SLH – פאזה 1\n\n"
        "אנחנו בונים מנגנון סטייקינג שיתחיל מניקוד על בסיס פעילות וריפרלים,\n"
        "ויתחבר בהמשך לסטייקינג ישיר על הטוקן SLH ב-BSC.\n\n"
        "בינתיים, אפשר לצבור נקודות על פעילות, הזמנת חברים ויצירת חנויות.\n"
    )
    await chat.send_message(text=text)


async def whoami_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    chat_id = update.effective_chat.id if update.effective_chat else None
    if not chat_id or not user:
        return

    data = load_referrals()
    u = data["users"].get(str(user.id))
    ref = u["referrer"] if u else None

    msg = [
        "פרטי המשתמש שלך:",
        f"user_id: {user.id}",
        f"username: @{user.username}" if user.username else "username: (ללא)",
    ]
    if ref:
        msg.append(f"הופנית ע\"י משתמש: {ref}")
    else:
        msg.append("לא רשום מפנה – ייתכן שאתה השורש או שנכנסת ישירות.")

    await context.bot.send_message(chat_id=chat_id, text="\n".join(msg))


async def init_telegram_app() -> None:
    global telegram_app
    bot_token = os.getenv("BOT_TOKEN")
    webhook_url = os.getenv("WEBHOOK_URL")

    if not bot_token:
        logger.error("BOT_TOKEN is not set – Telegram bot will not start.")
        return

    application = Application.builder().token(bot_token).build()

    # Handlers
    application.add_handler(CommandHandler("start", start_slhnet))
    application.add_handler(CommandHandler("investor", investor_handler))
    application.add_handler(CommandHandler("staking", staking))
    application.add_handler(CommandHandler("whoami", whoami_handler))

    # הודעה לכל /start לטובת לוג בקבוצת אדמין (לא מחליף את ההנדלר הקיים)
    async def notify_admin_new_user(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        admin_chat_id = int(os.getenv("TELEGRAM_LOG_CHAT", "0") or "0")
        user = update.effective_user
        if not admin_chat_id or not user:
            return

        text = (
            "🚀 משתמש חדש נכנס דרך /start\n"
            f"user_id = {user.id}\n"
            f"username = @{user.username}\n"
        )
        try:
            await context.bot.send_message(chat_id=admin_chat_id, text=text)
        except Exception as e:
            logger.error(f"Failed to notify admin on /start: {e}")

    application.add_handler(
        MessageHandler(filters.Regex(r"^/start"), notify_admin_new_user),
        group=1,
    )

    # שמירת האפליקציה הגלובלית
    telegram_app = application

    # אם יש WEBHOOK_URL – ננסה לכוון לשם
    if webhook_url:
        try:
            await application.bot.set_webhook(url=webhook_url)
            logger.info(f"Webhook set to {webhook_url}")
        except Exception as e:
            logger.error(f"Failed to set webhook: {e}")
    else:
        logger.warning("WEBHOOK_URL is not set – webhook will not be configured.")


# =========================
# FastAPI endpoints
# =========================

class HealthResponse(BaseModel):
    status: str
    service: str
    version: str


@app.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    return HealthResponse(status="ok", service="slhnet-gateway", version="1.0.0")


@app.get("/", response_class=HTMLResponse)
async def root(request: Request):
    """
    דף נחיתה בסיסי – אפשר להחליף ל-index.html מתיקיית templates
    """
    try:
        return templates.TemplateResponse(
            "index.html",
            {
                "request": request,
                "title": "SLHNET – Gateway",
            },
        )
    except Exception:
        # fallback אם אין טמפלט
        html = """
        <html>
          <head><title>SLHNET Gateway</title></head>
          <body>
            <h1>SLHNET Gateway</h1>
            <p>המערכת רצה. ניתן להתחבר לבוט בטלגרם ול-API.</p>
          </body>
        </html>
        """
        return HTMLResponse(content=html)


@app.post("/webhook")
async def telegram_webhook(request: Request):
    """
    נקודת Webhook שמקבלת עדכונים מטלגרם ומעבירה אותם ל-telegram_app
    """
    global telegram_app
    if telegram_app is None:
        # אתחול Lazy אם עוד לא אתחלנו
        await init_telegram_app()
        if telegram_app is None:
            raise HTTPException(status_code=500, detail="Telegram application not initialized")

    try:
        data = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    update = Update.de_json(data, telegram_app.bot)
    await telegram_app.process_update(update)
    return JSONResponse({"ok": True})


# =========================
# Run (development)
# =========================

if __name__ == "__main__":
    import uvicorn

    # הרצה מקומית לפיתוח
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
