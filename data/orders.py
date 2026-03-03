import logging
from auth.fyers_auth import get_fyers_instance

logger = logging.getLogger(__name__)


def place_buy_order(symbol: str, qty: int) -> dict:
    """Place a CNC market buy order. Returns Fyers API response."""
    fyers = get_fyers_instance()
    data = {
        "symbol": symbol,
        "qty": qty,
        "type": 2,          # Market order
        "side": 1,          # Buy
        "productType": "CNC",
        "limitPrice": 0,
        "stopPrice": 0,
        "validity": "DAY",
        "disclosedQty": 0,
        "offlineOrder": False,
        "orderTag": "darvas_bot",
    }
    response = fyers.place_order(data=data)
    logger.info(f"Buy order response for {symbol} x{qty}: {response}")
    return response


def place_sell_order(symbol: str, qty: int) -> dict:
    """
    Place a CNC market sell order.
    NOTE: For delivery holdings, CDSL eDIS/TPIN must be pre-authorised
    via the Fyers app. This call will succeed only if eDIS is already done.
    """
    fyers = get_fyers_instance()
    data = {
        "symbol": symbol,
        "qty": qty,
        "type": 2,          # Market order
        "side": -1,         # Sell
        "productType": "CNC",
        "limitPrice": 0,
        "stopPrice": 0,
        "validity": "DAY",
        "disclosedQty": 0,
        "offlineOrder": False,
        "orderTag": "darvas_bot",
    }
    response = fyers.place_order(data=data)
    logger.info(f"Sell order response for {symbol} x{qty}: {response}")
    return response
