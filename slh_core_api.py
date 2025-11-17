from __future__ import annotations

import os
import time
from datetime import datetime
from typing import Dict, List, Optional, Set

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

# ============================================================
# SLHNET Core API  public config + referral MVP
# ============================================================

core_router = APIRouter(tags=["slh_core"])

# alias so main.py can `from slh_core_api import router`
router = core_router


# --- Token / project config ---

SLH_SYMBOL = "SLH"
SLH_CONTRACT = "0xACb0A09414CEA1C879c67bB7A877E4e19480f022"
SLH_DECIMALS = 15

# מאפשר לשלוט במחיר דרך משתנה סביבה SLH_PRICE_ILS, ברירת מחדל: 444
try:
    SLH_PRICE_ILS = float(os.getenv("SLH_PRICE_ILS", "444"))
except ValueError:
    SLH_PRICE_ILS = 444.0


class PublicConfig(BaseModel):
    project: str = "SLHNET"
    landing_url: str = "https://slh-nft.com/"
    token_symbol: str = SLH_SYMBOL
    token_contract: str = SLH_CONTRACT
    token_decimals: int = SLH_DECIMALS
    token_price_ils: float = Field(..., description="Demo price of SLH in ILS")
    links: Dict[str, str]
    meta: Dict[str, str]


@core_router.get("/config/public", response_model=PublicConfig)
def get_public_config() -> PublicConfig:
    """
    קונפיג ציבורי  מיועד לאתר / קליינט.
    כרגע מחזיר נתונים סטטיים + קישורים מרכזיים.
    ניתן להרחבה בהמשך.
    """
    links = {
        "landing": "https://slh-nft.com/",
        "bot": "https://t.me/Buy_My_Shop_bot",
        "investor_telegram": "https://t.me/Osif83",
    }
    meta = {
        "description": "SLHNET  רשת עסקית סביב טוקן SLH, ריפרל מדורג ואקו-סיסטם של חנויות דיגיטליות.",
        "stage": "mvp-core-api",
    }
    return PublicConfig(
        token_price_ils=SLH_PRICE_ILS,
        links=links,
        meta=meta,
    )


@core_router.get("/api/token/price")
def get_token_price():
    """
    נקודת קצה פשוטה שמחזירה את מחיר ה-SLH בשקלים.
    כרגע: קונפיג ידני דרך SLH_PRICE_ILS או ברירת מחדל 444.
    בעתיד ניתן לחבר ל-Oracle / בורסה חיצונית.
    """
    return {
        "symbol": SLH_SYMBOL,
        "contract": SLH_CONTRACT,
        "decimals": SLH_DECIMALS,
        "price_ils": SLH_PRICE_ILS,
        "source": "manual_config",
        "updated_at": datetime.utcnow().isoformat() + "Z",
    }


# ============================================================
# Referral MVP  עץ הפניות בסיסי בזיכרון
# ============================================================

class ReferralNode(BaseModel):
    user_id: int
    username: Optional[str] = None
    children: List["ReferralNode"] = []


ReferralNode.update_forward_refs()


class ReferralStats(BaseModel):
    total_users: int
    total_relations: int
    total_visits: int
    roots: List[int]


class TrackVisitRequest(BaseModel):
    """
    בקשה לרישום ביקור / כניסה דרך לינק ריפרל.
    זה לא מחליף לוגיקת הרשמה בבוט, אלא שכבת טלמטריה / גרף חברתי.
    """
    referrer_id: int = Field(..., description="telegram user_id של המפנה")
    visitor_id: Optional[int] = Field(
        None,
        description="telegram user_id של המבקר, אם ידוע (אחרי /start בבוט)",
    )
    source: Optional[str] = Field(
        None,
        description="מקור / תיוג: למשל 'landing', 'whatsapp', 'telegram_share' וכו'",
    )
    ts: Optional[float] = Field(
        None,
        description="timestamp שנשלח מהלקוח (אם יש). אם לא  נשתמש ב-time.time().",
    )


# "Fake DB" בזיכרון  MVP בלבד.
# בהמשך אפשר להחליף למשהו מבוסס Postgres דרך db.py
_REFERRAL_GRAPH: Dict[int, List[int]] = {}
_USER_ALIASES: Dict[int, str] = {}
_VISITS: List[Dict[str, object]] = []


def _add_relation(referrer_id: int, visitor_id: int) -> None:
    """
    רישום קשר ריפרל בסיסי: referrer -> visitor.
    אם כבר קיים, לא נוסיף שוב.
    """
    if referrer_id == visitor_id:
        return

    children = _REFERRAL_GRAPH.setdefault(referrer_id, [])
    if visitor_id not in children:
        children.append(visitor_id)


def _collect_all_users() -> Set[int]:
    users: Set[int] = set()
    for src, dsts in _REFERRAL_GRAPH.items():
        users.add(src)
        users.update(dsts)
    return users


def _find_roots() -> List[int]:
    """
    משתמשים שאין להם 'מפנה מעליהם'  נחשבים שורשים בגרף.
    """
    all_users = _collect_all_users()
    all_children: Set[int] = set()
    for dsts in _REFERRAL_GRAPH.values():
        all_children.update(dsts)
    roots = sorted(all_users - all_children)
    return roots


def _build_tree(user_id: int, depth: int = 0, max_depth: int = 6) -> ReferralNode:
    """
    בניית עץ ריפרל רקורסיבי עד עומק מוגבל (כדי להימנע ממעגלים אינסופיים).
    """
    if depth > max_depth:
        return ReferralNode(user_id=user_id, username=_USER_ALIASES.get(user_id), children=[])

    children_ids = _REFERRAL_GRAPH.get(user_id, [])
    children_nodes = [
        _build_tree(child_id, depth=depth + 1, max_depth=max_depth)
        for child_id in children_ids
    ]
    return ReferralNode(
        user_id=user_id,
        username=_USER_ALIASES.get(user_id),
        children=children_nodes,
    )


@core_router.post("/api/referral/track_visit")
def track_visit(req: TrackVisitRequest):
    """
    רושם ביקור דרך לינק ריפרל.
    * אם יש visitor_id  מוסיף קשת בגרף referrer -> visitor.
    * שומר אירוע ב-LOG בזיכרון (MVP).
    """
    ts = req.ts or time.time()
    event = {
        "referrer_id": req.referrer_id,
        "visitor_id": req.visitor_id,
        "source": req.source or "unknown",
        "ts": ts,
    }
    _VISITS.append(event)

    if req.visitor_id:
        _add_relation(req.referrer_id, req.visitor_id)

    return {
        "status": "ok",
        "stored": True,
        "event": event,
    }


@core_router.get("/api/referral/stats", response_model=ReferralStats)
def get_referral_stats() -> ReferralStats:
    """
    סטטיסטיקות עיקריות על גרף ההפניות.
    כרגע על בסיס הזיכרון בתהליך.
    """
    all_users = _collect_all_users()
    roots = _find_roots()
    total_relations = sum(len(v) for v in _REFERRAL_GRAPH.values())

    return ReferralStats(
        total_users=len(all_users),
        total_relations=total_relations,
        total_visits=len(_VISITS),
        roots=roots,
    )


@core_router.get("/api/referral/tree/{user_id}", response_model=ReferralNode)
def get_referral_tree(user_id: int) -> ReferralNode:
    """
    מחזיר עץ ריפרל למשתמש נתון (כולל הילדים שלו ברמות הבאות).
    אם אין נתונים  מחזירים צומת ריק (ילדים = []) כדי לא לשבור קליינט.
    """
    all_users = _collect_all_users()
    if user_id not in all_users and user_id not in _REFERRAL_GRAPH:
        # אין עץ רשום עבור המשתמש  מחזירים צומת בסיסי.
        return ReferralNode(
            user_id=user_id,
            username=_USER_ALIASES.get(user_id),
            children=[],
        )

    return _build_tree(user_id)

