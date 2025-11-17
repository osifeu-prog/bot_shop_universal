# db.py
import os
import logging
from contextlib import contextmanager
from typing import Optional, Any, List, Dict

import psycopg2
import psycopg2.extras

logger = logging.getLogger(__name__)

DATABASE_URL = os.environ.get("DATABASE_URL")

if not DATABASE_URL:
    logger.warning("DATABASE_URL is not set. DB functions will be no-op.")


@contextmanager
def db_cursor():
    """
    הקשר נוח לעבודה עם psycopg2.
    מחזיר (conn, cur) או (None, None) אם אין DATABASE_URL.
    """
    if not DATABASE_URL:
        yield None, None
        return

    conn = None
    cur = None
    try:
        conn = psycopg2.connect(DATABASE_URL)
        cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        yield conn, cur
        if conn:
            conn.commit()
    except Exception as e:
        logger.error("DB error: %s", e)
        if conn:
            conn.rollback()
        raise
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()


# =========================
# יצירת טבלאות (schema)
# =========================

def init_schema() -> None:
    """
    יוצר את כל הטבלאות הדרושות אם הן לא קיימות.
    לא מוחק או משנה טבלאות קיימות.
    """
    with db_cursor() as (conn, cur):
        if cur is None:
            logger.warning("init_schema called without DB.")
            return

        # users – משתמשי טלגרם
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                user_id      BIGINT PRIMARY KEY,
                username     TEXT,
                created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            """
        )

        # payments – תשלומים / אישורים
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS payments (
                id          SERIAL PRIMARY KEY,
                user_id     BIGINT NOT NULL,
                username    TEXT,
                pay_method  TEXT NOT NULL,
                status      TEXT NOT NULL DEFAULT 'pending',  -- pending/approved/rejected
                reason      TEXT,
                created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            """
        )

        # referrals – מי הפנה את מי
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS referrals (
                id               SERIAL PRIMARY KEY,
                referrer_id      BIGINT NOT NULL,
                referred_user_id BIGINT NOT NULL,
                source           TEXT,
                created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            """
        )
        # למנוע כפילויות בסיסיות
        cur.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_referrals_unique
            ON referrals(referrer_id, referred_user_id, COALESCE(source, ''));
            """
        )

        # rewards – נקודות/תגמולים (למשל SLH, NFT וכו')
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS rewards (
                id          SERIAL PRIMARY KEY,
                user_id     BIGINT NOT NULL,
                reward_type TEXT NOT NULL,              -- "SLH", "NFT", "SHARE", ...
                reason      TEXT,
                points      INT NOT NULL DEFAULT 0,
                status      TEXT NOT NULL DEFAULT 'pending',   -- pending/sent/failed
                tx_hash     TEXT,
                created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            """
        )

        # promoters – בעלי נכס דיגיטלי (שער קהילה אישי)
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS promoters (
                user_id            BIGINT PRIMARY KEY,
                bank_details       TEXT,
                personal_group_link TEXT,
                global_group_link   TEXT,
                custom_price       NUMERIC,
                created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at         TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            """
        )

        # metrics – ספירות כלליות
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS metrics (
                key   TEXT PRIMARY KEY,
                value BIGINT NOT NULL DEFAULT 0
            );
            """
        )

        logger.info("DB schema ensured (users, payments, referrals, rewards, promoters, metrics).")


# =========================
# users
# =========================

def store_user(user_id: int, username: Optional[str]) -> None:
    """
    שומר/מעדכן משתמש.
    """
    with db_cursor() as (conn, cur):
        if cur is None:
            logger.warning("store_user called without DB.")
            return
        cur.execute(
            """
            INSERT INTO users (user_id, username)
            VALUES (%s, %s)
            ON CONFLICT (user_id)
            DO UPDATE SET username = EXCLUDED.username;
            """,
            (user_id, username),
        )


# =========================
# payments
# =========================

def log_payment(user_id: int, username: Optional[str], pay_method: str) -> None:
    """
    רושם תשלום במצב 'pending' (כשהמשתמש שולח צילום אישור).
    """
    with db_cursor() as (conn, cur):
        if cur is None:
            logger.warning("log_payment called without DB.")
            return
        cur.execute(
            """
            INSERT INTO payments (user_id, username, pay_method, status)
            VALUES (%s, %s, %s, 'pending');
            """,
            (user_id, username, pay_method),
        )

def update_payment_status(user_id: int, status: str, reason: Optional[str]) -> None:
    """
    מעדכן את הסטטוס של התשלום האחרון של המשתמש.
    """
    with db_cursor() as (conn, cur):
        if cur is None:
            logger.warning("update_payment_status called without DB.")
            return
        cur.execute(
            """
            UPDATE payments
            SET status = %s,
                reason = %s,
                updated_at = NOW()
            WHERE id = (
                SELECT id
                FROM payments
                WHERE user_id = %s
                ORDER BY created_at DESC
                LIMIT 1
            );
            """,
            (status, reason, user_id),
        )


def get_monthly_payments(year: int, month: int) -> List[Dict[str, Any]]:
    """
    מחזיר אגרגציה של תשלומים לפי אמצעי תשלום וסטטוס עבור חודש נתון.
    """
    with db_cursor() as (conn, cur):
        if cur is None:
            return []
        cur.execute(
            """
            SELECT pay_method, status, COUNT(*) AS count
            FROM payments
            WHERE EXTRACT(YEAR FROM created_at) = %s
              AND EXTRACT(MONTH FROM created_at) = %s
            GROUP BY pay_method, status
            ORDER BY pay_method, status;
            """,
            (year, month),
        )
        return [dict(row) for row in cur.fetchall()]


def get_approval_stats() -> Dict[str, int]:
    """
    מחזיר סטטיסטיקות בסיסיות על תשלומים:
    total / approved / rejected / pending
    """
    with db_cursor() as (conn, cur):
        if cur is None:
            return {"total": 0, "approved": 0, "rejected": 0, "pending": 0}

        cur.execute("SELECT COUNT(*) AS total FROM payments;")
        total = int(cur.fetchone()["total"])

        def _count_status(st: str) -> int:
            cur.execute("SELECT COUNT(*) AS c FROM payments WHERE status = %s;", (st,))
            return int(cur.fetchone()["c"])

        approved = _count_status("approved")
        rejected = _count_status("rejected")
        pending = _count_status("pending")

        return {
            "total": total,
            "approved": approved,
            "rejected": rejected,
            "pending": pending,
        }


# =========================
# referrals & leaderboard
# =========================

def add_referral(referrer_id: int, referred_user_id: int, source: Optional[str] = None) -> None:
    """
    מוסיף רשומת הפניה (referral). אם כבר קיים זוג כזה – מתעלמים.
    """
    with db_cursor() as (conn, cur):
        if cur is None:
            logger.warning("add_referral called without DB.")
            return
        try:
            cur.execute(
                """
                INSERT INTO referrals (referrer_id, referred_user_id, source)
                VALUES (%s, %s, %s)
                ON CONFLICT ON CONSTRAINT idx_referrals_unique DO NOTHING;
                """,
                (referrer_id, referred_user_id, source),
            )
        except Exception as e:
            # אם האינדקס/constraint לא קיים – ננסה ללא ON CONFLICT
            logger.debug("add_referral ON CONFLICT failed, retrying without it: %s", e)
            cur.execute(
                """
                INSERT INTO referrals (referrer_id, referred_user_id, source)
                VALUES (%s, %s, %s)
                ON CONFLICT DO NOTHING;
                """,
                (referrer_id, referred_user_id, source),
            )


def get_top_referrers(limit: int = 10) -> List[Dict[str, Any]]:
    """
    מחזיר Top referrers לפי מספר הפניות + סכום נקודות rewards (אם קיימים).
    """
    with db_cursor() as (conn, cur):
        if cur is None:
            return []

        cur.execute(
            """
            SELECT
                r.referrer_id,
                u.username,
                COUNT(DISTINCT r.referred_user_id) AS total_referrals,
                COALESCE(SUM(CASE WHEN rw.points IS NULL THEN 0 ELSE rw.points END), 0) AS total_points
            FROM referrals r
            LEFT JOIN users u
                ON u.user_id = r.referrer_id
            LEFT JOIN rewards rw
                ON rw.user_id = r.referrer_id
            GROUP BY r.referrer_id, u.username
            ORDER BY total_referrals DESC, total_points DESC
            LIMIT %s;
            """,
            (limit,),
        )
        return [dict(row) for row in cur.fetchall()]


# =========================
# rewards
# =========================

def create_reward(user_id: int, reward_type: str, reason: str, points: int) -> None:
    """
    יצירת Reward – לדוגמה SLH points.
    """
    with db_cursor() as (conn, cur):
        if cur is None:
            logger.warning("create_reward called without DB.")
            return
        cur.execute(
            """
            INSERT INTO rewards (user_id, reward_type, reason, points)
            VALUES (%s, %s, %s, %s);
            """,
            (user_id, reward_type, reason, points),
        )


# =========================
# promoters – שכבת הנכס הדיגיטלי
# =========================

def ensure_promoter(user_id: int) -> None:
    """
    מוודא שקיימת רשומה ב-promoters עבור המשתמש.
    """
    with db_cursor() as (conn, cur):
        if cur is None:
            logger.warning("ensure_promoter called without DB.")
            return
        cur.execute(
            """
            INSERT INTO promoters (user_id)
            VALUES (%s)
            ON CONFLICT (user_id) DO NOTHING;
            """,
            (user_id,),
        )


def update_promoter_settings(
    user_id: int,
    bank_details: Optional[str] = None,
    personal_group_link: Optional[str] = None,
    global_group_link: Optional[str] = None,
) -> None:
    """
    עדכון פרטי promoter – רק השדות שלא None יתעדכנו.
    """
    fields = []
    params: List[Any] = []
    if bank_details is not None:
        fields.append("bank_details = %s")
        params.append(bank_details)
    if personal_group_link is not None:
        fields.append("personal_group_link = %s")
        params.append(personal_group_link)
    if global_group_link is not None:
        fields.append("global_group_link = %s")
        params.append(global_group_link)

    if not fields:
        return

    params.append(user_id)

    with db_cursor() as (conn, cur):
        if cur is None:
            logger.warning("update_promoter_settings called without DB.")
            return
        cur.execute(
            f"""
            UPDATE promoters
            SET {", ".join(fields)},
                updated_at = NOW()
            WHERE user_id = %s;
            """,
            params,
        )


def get_promoter_summary(user_id: int) -> Optional[Dict[str, Any]]:
    """
    מחזיר פרטי promoter + כמה הפניות ותשלומים אושרו.
    """
    with db_cursor() as (conn, cur):
        if cur is None:
            return None

        cur.execute(
            """
            SELECT
                p.user_id,
                p.bank_details,
                p.personal_group_link,
                p.global_group_link,
                p.custom_price,
                p.created_at,
                p.updated_at
            FROM promoters p
            WHERE p.user_id = %s;
            """,
            (user_id,),
        )
        row = cur.fetchone()
        if not row:
            return None

        promoter = dict(row)

        # כמה הפניות רשומות לו
        cur.execute(
            "SELECT COUNT(*) AS c FROM referrals WHERE referrer_id = %s;",
            (user_id,),
        )
        promoter["total_referrals"] = int(cur.fetchone()["c"])

        # כמה תשלומים אושרו למופנים שלו (אם נרצה – אפשר לשדרג את זה)
        cur.execute(
            """
            SELECT COUNT(*) AS c
            FROM payments pay
            JOIN referrals ref
              ON ref.referred_user_id = pay.user_id
            WHERE ref.referrer_id = %s
              AND pay.status = 'approved';
            """,
            (user_id,),
        )
        promoter["approved_referrals"] = int(cur.fetchone()["c"])

        return promoter


# =========================
# metrics
# =========================

def incr_metric(key: str, delta: int = 1) -> None:
    with db_cursor() as (conn, cur):
        if cur is None:
            return
        cur.execute(
            """
            INSERT INTO metrics (key, value)
            VALUES (%s, %s)
            ON CONFLICT (key)
            DO UPDATE SET value = metrics.value + EXCLUDED.value;
            """,
            (key, delta),
        )


def get_metric(key: str) -> int:
    """
    מחזיר את ערך המונה או 0 אם לא קיים.
    """
    with db_cursor() as (conn, cur):
        if cur is None:
            return 0
        cur.execute(
            "SELECT value FROM metrics WHERE key = %s;",
            (key,),
        )
        row = cur.fetchone()
        return int(row["value"]) if row else 0


# === Website / docs tables for integrated payments system ===
def ensure_website_tables():
    try:
        from psycopg2 import sql
    except ImportError:
        # psycopg2 already imported at top
        pass

    with db_cursor() as (conn, cur):
        if cur is None:
            return

        # website_payments
        cur.execute("""
            CREATE TABLE IF NOT EXISTS website_payments (
                id SERIAL PRIMARY KEY,
                user_id BIGINT NOT NULL,
                telegram_username VARCHAR(255),
                first_name VARCHAR(255) NOT NULL,
                last_name VARCHAR(255),
                payment_method VARCHAR(50) NOT NULL,
                proof_image VARCHAR(500),
                personal_link VARCHAR(500) NOT NULL,
                status VARCHAR(20) DEFAULT 'pending',
                bank_account TEXT,
                group_link TEXT,
                custom_price INTEGER DEFAULT 39,
                bsc_wallet VARCHAR(255),
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)

        # user_settings
        cur.execute("""
            CREATE TABLE IF NOT EXISTS user_settings (
                user_id BIGINT PRIMARY KEY,
                bank_account TEXT,
                group_link TEXT,
                custom_price INTEGER DEFAULT 39,
                bsc_wallet VARCHAR(255),
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)

        # site_metrics
        cur.execute("""
            CREATE TABLE IF NOT EXISTS site_metrics (
                id SERIAL PRIMARY KEY,
                date DATE UNIQUE,
                visits INTEGER DEFAULT 0,
                unique_visitors INTEGER DEFAULT 0,
                conversions INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)

ensure_website_tables()
