import os
from typing import List, Optional, Dict, Any
from fastapi import APIRouter, Query
from pydantic import BaseModel

DATABASE_URL = os.getenv("DATABASE_URL")
try:
    import psycopg2  # type: ignore
except Exception:
    psycopg2 = None  # type: ignore

router = APIRouter()


def _get_conn():
    if not DATABASE_URL or psycopg2 is None:
        return None
    return psycopg2.connect(DATABASE_URL)


def _ensure_tables(conn):
    with conn:
        with conn.cursor() as cur:
            cur.execute(
                \"\"\"
                CREATE TABLE IF NOT EXISTS slh_posts (
                    id SERIAL PRIMARY KEY,
                    user_id BIGINT,
                    username TEXT,
                    title TEXT NOT NULL,
                    content TEXT NOT NULL,
                    share_url TEXT,
                    created_at TIMESTAMPTZ DEFAULT NOW(),
                    is_published BOOLEAN DEFAULT TRUE
                );
                \"\"\"
            )
            cur.execute(
                \"\"\"
                CREATE TABLE IF NOT EXISTS slh_token_sales (
                    id SERIAL PRIMARY KEY,
                    user_id BIGINT,
                    username TEXT,
                    wallet_address TEXT,
                    amount_slh NUMERIC(36, 18),
                    price_nis NUMERIC(18, 2),
                    status TEXT DEFAULT 'pending',
                    tx_hash TEXT,
                    created_at TIMESTAMPTZ DEFAULT NOW()
                );
                \"\"\"
            )
            cur.execute(
                \"\"\"
                CREATE TABLE IF NOT EXISTS slh_staking_positions (
                    id SERIAL PRIMARY KEY,
                    user_id BIGINT,
                    wallet_address TEXT,
                    plan_code TEXT,
                    amount_slh NUMERIC(36, 18),
                    start_at TIMESTAMPTZ DEFAULT NOW(),
                    lock_days INT,
                    apy_target NUMERIC(10, 4),
                    status TEXT DEFAULT 'active'
                );
                \"\"\"
            )


class PublicConfig(BaseModel):
    project_name: str
    bot_link: str
    group_invite: Optional[str]
    slh_nis: float
    token_contract: str
    chain_id: int
    network_name: str
    rpc_url: str
    block_explorer: str


class TokenPrice(BaseModel):
    symbol: str
    official_price_nis: float
    source: str = "static"


class PostOut(BaseModel):
    id: int
    user_id: Optional[int]
    username: Optional[str]
    title: str
    content: str
    share_url: Optional[str]
    created_at: Optional[str]


class TokenSaleOut(BaseModel):
    id: int
    user_id: Optional[int]
    username: Optional[str]
    wallet_address: Optional[str]
    amount_slh: Optional[float]
    price_nis: Optional[float]
    status: str
    tx_hash: Optional[str]
    created_at: Optional[str]


class StakingPlan(BaseModel):
    code: str
    name: str
    lock_days: int
    apy_target: float
    description: str


class StakingSummary(BaseModel):
    total_locked_slh: float
    total_investors: int
    avg_apy_target: float
    plans: List[StakingPlan]


SLH_NIS_DEFAULT = float(os.getenv("SLH_NIS", "444"))
BOT_USERNAME = os.getenv("BOT_USERNAME", "Buy_My_Shop_bot")
GROUP_INVITE = os.getenv("GROUP_STATIC_INVITE")


@router.get("/config/public", response_model=PublicConfig)
def get_public_config():
    return PublicConfig(
        project_name="SLHNET  הרשת העסקית סביב SLH",
        bot_link=f"https://t.me/{BOT_USERNAME}",
        group_invite=GROUP_INVITE,
        slh_nis=SLH_NIS_DEFAULT,
        token_contract="0xACb0A09414CEA1C879c67bB7A877E4e19480f022",
        chain_id=56,
        network_name="BNB Smart Chain",
        rpc_url="https://bsc-dataseed.binance.org/",
        block_explorer="https://bscscan.com",
    )


@router.get("/api/token/price", response_model=TokenPrice)
def get_token_price():
    return TokenPrice(symbol="SLH", official_price_nis=SLH_NIS_DEFAULT)


@router.get("/api/posts", response_model=List[PostOut])
def api_list_posts(limit: int = Query(20, ge=1, le=100)):
    conn = _get_conn()
    if conn is None:
        return []
    _ensure_tables(conn)
    rows: List[Dict[str, Any]] = []
    with conn:
        with conn.cursor() as cur:
            cur.execute(
                \"\"\"
                SELECT id, user_id, username, title, content, share_url, created_at
                FROM slh_posts
                WHERE is_published = TRUE
                ORDER BY created_at DESC
                LIMIT %s;
                \"\"\",
                (limit,),
            )
            for r in cur.fetchall():
                rows.append(
                    dict(
                        id=r[0],
                        user_id=r[1],
                        username=r[2],
                        title=r[3],
                        content=r[4],
                        share_url=r[5],
                        created_at=r[6].isoformat() if r[6] else None,
                    )
                )
    return [PostOut(**r) for r in rows]


@router.get("/api/token/sales", response_model=List[TokenSaleOut])
def api_list_token_sales(limit: int = Query(50, ge=1, le=200)):
    conn = _get_conn()
    if conn is None:
        return []
    _ensure_tables(conn)
    rows: List[Dict[str, Any]] = []
    with conn:
        with conn.cursor() as cur:
            cur.execute(
                \"\"\"
                SELECT id, user_id, username, wallet_address,
                       amount_slh, price_nis, status, tx_hash, created_at
                FROM slh_token_sales
                ORDER BY created_at DESC
                LIMIT %s;
                \"\"\",
                (limit,),
            )
            for r in cur.fetchall():
                rows.append(
                    dict(
                        id=r[0],
                        user_id=r[1],
                        username=r[2],
                        wallet_address=r[3],
                        amount_slh=float(r[4]) if r[4] is not None else None,
                        price_nis=float(r[5]) if r[5] is not None else None,
                        status=r[6],
                        tx_hash=r[7],
                        created_at=r[8].isoformat() if r[8] else None,
                    )
                )
    return [TokenSaleOut(**r) for r in rows]


@router.get("/api/staking/plans", response_model=List[StakingPlan])
def api_staking_plans():
    return [
        StakingPlan(
            code="starter",
            name="Starter",
            lock_days=30,
            apy_target=0.10,
            description="סטייקינג בסיסי  מיועד למשתמשים חדשים. תשואה מטרה שנתית עד ~10% בהתאם לרווחי המערכת.",
        ),
        StakingPlan(
            code="business",
            name="Business",
            lock_days=90,
            apy_target=0.18,
            description="סטייקינג לעסקים ושותפים. תשואה מטרה עד ~18% לשנה, נגזרת מעמלות, שירותים וסטייקינג בלוקצ'יין.",
        ),
        StakingPlan(
            code="pro",
            name="Pro",
            lock_days=180,
            apy_target=0.24,
            description="למשקיעים רציניים בלבד  דורש בדיקת התאמה. חלק מהרווחים חוזרים לרזרבה.",
        ),
    ]


@router.get("/api/staking/summary", response_model=StakingSummary)
def api_staking_summary():
    conn = _get_conn()
    total_locked = 0.0
    investors = 0
    if conn is not None:
        _ensure_tables(conn)
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    \"\"\"
                    SELECT COALESCE(SUM(amount_slh), 0), COUNT(DISTINCT user_id)
                    FROM slh_staking_positions
                    WHERE status = 'active';
                    \"\"\"
                )
                res = cur.fetchone()
                if res:
                    total_locked = float(res[0] or 0)
                    investors = int(res[1] or 0)

    plans = api_staking_plans()
    avg_apy = 0.0
    if plans:
        avg_apy = sum(p.apy_target for p in plans) / len(plans)

    return StakingSummary(
        total_locked_slh=total_locked,
        total_investors=investors,
        avg_apy_target=avg_apy,
        plans=plans,
    )
