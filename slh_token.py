\"\"\"SLH Token integration on Binance Smart Chain (BSC)

- Validate BSC addresses
- Read SLH balance
- Verify on-chain sale tx (Transfer from user -> treasury)
\"\"\"

import os
from typing import Optional, Tuple

from web3 import Web3
from web3.exceptions import TransactionNotFound

SLH_CHAIN_ID = 56
SLH_RPC_URL = os.environ.get("BSC_RPC_URL", "https://bsc-dataseed.binance.org/")
SLH_TOKEN_ADDRESS = os.environ.get(
    "SLH_TOKEN_ADDRESS",
    "0xACb0A09414CEA1C879c67bB7A877E4e19480f022"
)
SLH_TOKEN_SYMBOL = os.environ.get("SLH_TOKEN_SYMBOL", "SLH")
SLH_TOKEN_DECIMALS = int(os.environ.get("SLH_TOKEN_DECIMALS", "15"))

TREASURY_ADDRESS = os.environ.get(
    "SLH_TREASURY_ADDRESS",
    "0x000000000000000000000000000000000000dEaD"  # replace with your real treasury address
)

w3 = Web3(Web3.HTTPProvider(SLH_RPC_URL))

ERC20_ABI = [
    {
        "constant": True,
        "inputs": [{"name": "_owner", "type": "address"}],
        "name": "balanceOf",
        "outputs": [{"name": "balance", "type": "uint256"}],
        "type": "function",
    },
    {
        "constant": True,
        "inputs": [],
        "name": "decimals",
        "outputs": [{"name": "", "type": "uint8"}],
        "type": "function",
    },
    {
        "constant": True,
        "inputs": [],
        "name": "symbol",
        "outputs": [{"name": "", "type": "string"}],
        "type": "function",
    },
    {
        "anonymous": False,
        "inputs": [
            {"indexed": True, "name": "from", "type": "address"},
            {"indexed": True, "name": "to", "type": "address"},
            {"indexed": False, "name": "value", "type": "uint256"},
        ],
        "name": "Transfer",
        "type": "event",
    },
]

SLH_CONTRACT = w3.eth.contract(
    address=Web3.to_checksum_address(SLH_TOKEN_ADDRESS),
    abi=ERC20_ABI,
)


def is_valid_bsc_address(address: str) -> bool:
    try:
        return w3.is_address(address)
    except Exception:
        return False


def checksum(address: str) -> str:
    return Web3.to_checksum_address(address)


def get_slh_balance(address: str) -> Optional[float]:
    if not is_valid_bsc_address(address):
        return None
    try:
        raw = SLH_CONTRACT.functions.balanceOf(checksum(address)).call()
        return raw / (10 ** SLH_TOKEN_DECIMALS)
    except Exception:
        return None


def verify_slh_sale_tx(
    tx_hash: str,
    expected_from: str,
    min_amount: float,
    treasury_address: Optional[str] = None,
) -> Tuple[bool, str, Optional[float], Optional[int]]:
    if treasury_address is None:
        treasury_address = TREASURY_ADDRESS

    try:
        tx_hash = tx_hash.strip()
        if not tx_hash.startswith("0x"):
            return False, "tx_hash לא תקין", None, None

        receipt = w3.eth.get_transaction_receipt(tx_hash)
    except TransactionNotFound:
        return False, "העסקה לא נמצאה בשרשרת (TransactionNotFound)", None, None
    except Exception as e:
        return False, f"שגיאה בקריאת העסקה: {e}", None, None

    if receipt.status != 1:
        return False, "העסקה נכשלה (status != 1)", None, receipt.blockNumber

    from_addr_checksum = checksum(expected_from)
    treasury_checksum = checksum(treasury_address)
    token_addr_checksum = checksum(SLH_TOKEN_ADDRESS)

    amount_found = 0

    try:
        events = SLH_CONTRACT.events.Transfer().process_receipt(receipt)
        for ev in events:
            if (
                ev["address"] == token_addr_checksum
                and ev["args"]["from"] == from_addr_checksum
                and ev["args"]["to"] == treasury_checksum
            ):
                amount_found += ev["args"]["value"]

        if amount_found == 0:
            return (
                False,
                "לא נמצאה העברת SLH מהמשתמש לכתובת הטרז'רי בעסקה הזו",
                None,
                receipt.blockNumber,
            )

        amount_slh = amount_found / (10 ** SLH_TOKEN_DECIMALS)
        if amount_slh < min_amount:
            return (
                False,
                f"הסכום בעסקה ({amount_slh} {SLH_TOKEN_SYMBOL}) קטן מהנדרש ({min_amount})",
                amount_slh,
                receipt.blockNumber,
            )

        return True, "OK", amount_slh, receipt.blockNumber

    except Exception as e:
        return False, f"שגיאה בניתוח האירועים: {e}", None, receipt.blockNumber
