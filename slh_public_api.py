import os
from datetime import datetime
from typing import List, Dict, Any

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter()


class PublicConfig(BaseModel):
    project: str
    network: str
    chain_id: int
    rpc_url: str
    token_address: str
    token_symbol: str
    token_decimals: int
    slh_price_nis: float
    urls: Dict[str, str]


class TokenPrice(BaseModel):
    symbol: str
    price_nis: float
    updated_at: str


class SaleItem(BaseModel):
    tx_hash: str
    buyer: str
    amount_slh: float
    price_nis: float
    timestamp: str


@router.get("/config/public", response_model=PublicConfig)
async def get_public_config():
    slh_price = float(os.getenv("SLH_NIS", "444"))

    return PublicConfig(
        project="SLHNET",
        network="BSC Mainnet",
        chain_id=56,
        rpc_url="https://bsc-dataseed.binance.org/",
        token_address="0xACb0A09414CEA1C879c67bB7A877E4e19480f022",
        token_symbol="SLH",
        token_decimals=15,
        slh_price_nis=slh_price,
        urls={
            "bot": os.getenv("WEBHOOK_URL", "").replace("/webhook", ""),
            "business_group": os.getenv("GROUP_STATIC_INVITE", ""),
            "paybox": os.getenv("PAYBOX_URL", ""),
            "bit": os.getenv("BIT_URL", ""),
            "paypal": os.getenv("PAYPAL_URL", ""),
        },
    )


@router.get("/api/token/price", response_model=TokenPrice)
async def get_token_price():
    slh_price = float(os.getenv("SLH_NIS", "444"))
    return TokenPrice(
        symbol="SLH",
        price_nis=slh_price,
        updated_at=datetime.utcnow().isoformat() + "Z",
    )


@router.get("/api/token/sales", response_model=List[SaleItem])
async def get_token_sales(limit: int = 50):
    # לעת עתה  רשימה ריקה כדי למנוע שגיאות בצד ה-Front
    return []


