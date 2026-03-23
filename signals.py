"""
signals.py — Signal detection logic.

Three signals are implemented:

  MOMENTUM    — YES price moved ≥ MOMENTUM_THRESHOLD in the last
                MOMENTUM_LOOKBACK_SECONDS seconds.

  LIQUIDITY   — One side of the order book has less than
                LIQUIDITY_IMBALANCE_THRESHOLD × the other side's total depth.

  MISPRICING  — |current_mid_price - fair_value| > MISPRICING_THRESHOLD.
                Fair value defaults to DEFAULT_FAIR_VALUE until you supply
                a real model.

Each check function returns a Signal dataclass or None.
`evaluate_market` runs all three and returns a (possibly empty) list.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional

import config
import database

logger = logging.getLogger(__name__)


@dataclass
class Signal:
    ticker: str
    title: str
    signal_type: str          # MOMENTUM | LIQUIDITY | MISPRICING
    current_price: float      # fraction, e.g. 0.62
    fair_value: float
    recommended: str          # BUY_YES | BUY_NO | NONE
    detail: str               # human-readable explanation


# ── Individual signal checks ──────────────────────────────────────────────────

def check_momentum(ticker: str, title: str, current_price: float) -> Optional[Signal]:
    """
    Momentum signal.

    Looks up price history in the DB for the past MOMENTUM_LOOKBACK_SECONDS
    and checks whether the price moved by more than MOMENTUM_THRESHOLD.
    """
    lookback_start = (
        datetime.utcnow() - timedelta(seconds=config.MOMENTUM_LOOKBACK_SECONDS)
    ).isoformat()

    history = database.get_price_history(ticker, since_iso=lookback_start)
    if len(history) < 2:
        # Not enough data to compute momentum yet.
        return None

    oldest_price = history[0]["yes_price"]
    if oldest_price is None or oldest_price == 0:
        return None

    price_change = (current_price - oldest_price) / oldest_price

    if abs(price_change) < config.MOMENTUM_THRESHOLD:
        return None

    direction = "up" if price_change > 0 else "down"
    recommended = "BUY_YES" if price_change > 0 else "BUY_NO"

    detail = (
        f"Price moved {price_change:+.1%} ({direction}) over the last "
        f"{config.MOMENTUM_LOOKBACK_SECONDS // 60} min "
        f"(from {oldest_price:.2f} → {current_price:.2f})"
    )
    logger.info("[MOMENTUM] %s — %s", ticker, detail)

    return Signal(
        ticker=ticker,
        title=title,
        signal_type="MOMENTUM",
        current_price=current_price,
        fair_value=config.DEFAULT_FAIR_VALUE,
        recommended=recommended,
        detail=detail,
    )


def check_liquidity(ticker: str, title: str, current_price: float,
                    orderbook: dict) -> Optional[Signal]:
    """
    Liquidity imbalance signal.

    Compares total contracts available on the YES side vs. NO side of the
    order book. If either side is thin relative to the other, flag it.

    `orderbook` is the dict returned by KalshiClient.get_orderbook():
        { "yes": [[price_cents, size], ...], "no": [[price_cents, size], ...] }
    """
    yes_levels = orderbook.get("yes", [])
    no_levels  = orderbook.get("no",  [])

    yes_depth = sum(level[1] for level in yes_levels)
    no_depth  = sum(level[1] for level in no_levels)

    total_depth = yes_depth + no_depth
    if total_depth == 0:
        return None  # empty book — no signal

    thinner_side  = min(yes_depth, no_depth)
    thicker_side  = max(yes_depth, no_depth)

    if thicker_side == 0:
        return None

    ratio = thinner_side / thicker_side
    if ratio >= config.LIQUIDITY_IMBALANCE_THRESHOLD:
        return None  # book is balanced enough

    thin_name  = "YES" if yes_depth < no_depth else "NO"
    thick_name = "NO"  if yes_depth < no_depth else "YES"

    # If YES side is thin, the market may be underpriced — consider BUY_YES.
    recommended = "BUY_YES" if thin_name == "YES" else "BUY_NO"

    detail = (
        f"Order book imbalance: {thin_name} side has {thinner_side} contracts vs "
        f"{thicker_side} on {thick_name} (ratio {ratio:.2f} < "
        f"{config.LIQUIDITY_IMBALANCE_THRESHOLD})"
    )
    logger.info("[LIQUIDITY] %s — %s", ticker, detail)

    return Signal(
        ticker=ticker,
        title=title,
        signal_type="LIQUIDITY",
        current_price=current_price,
        fair_value=config.DEFAULT_FAIR_VALUE,
        recommended=recommended,
        detail=detail,
    )


def check_mispricing(ticker: str, title: str, current_price: float,
                     fair_value: float | None = None) -> Optional[Signal]:
    """
    Mispricing signal.

    Compares the current mid-price to a fair value estimate.
    Uses DEFAULT_FAIR_VALUE if no model-supplied value is provided.
    """
    fv = fair_value if fair_value is not None else config.DEFAULT_FAIR_VALUE
    diff = current_price - fv

    if abs(diff) < config.MISPRICING_THRESHOLD:
        return None

    # If price is above fair value → market is overpriced → sell YES / buy NO.
    recommended = "BUY_NO" if diff > 0 else "BUY_YES"

    detail = (
        f"Price {current_price:.2f} is {abs(diff):.2f} away from fair value {fv:.2f} "
        f"({'overpriced' if diff > 0 else 'underpriced'})"
    )
    logger.info("[MISPRICING] %s — %s", ticker, detail)

    return Signal(
        ticker=ticker,
        title=title,
        signal_type="MISPRICING",
        current_price=current_price,
        fair_value=fv,
        recommended=recommended,
        detail=detail,
    )


# ── Master evaluator ──────────────────────────────────────────────────────────

def evaluate_market(market: dict, orderbook: dict,
                    fair_value: float | None = None) -> list[Signal]:
    """
    Run all signal checks for a single market and return any that fired.

    Args:
        market:     Market dict from KalshiClient.get_markets()
        orderbook:  Order book dict from KalshiClient.get_orderbook()
        fair_value: Optional model-supplied fair value (defaults to config value)

    Returns:
        List of Signal objects (may be empty).
    """
    ticker = market.get("ticker", "")
    title  = market.get("title", ticker)

    # Derive a mid-price from the best bid/ask, falling back to last price.
    yes_ask = _best_price(orderbook.get("yes", []), side="ask")
    yes_bid = _best_price(orderbook.get("yes", []), side="bid")

    if yes_ask is not None and yes_bid is not None:
        current_price = (yes_ask + yes_bid) / 2.0
    elif yes_ask is not None:
        current_price = yes_ask
    elif yes_bid is not None:
        current_price = yes_bid
    else:
        # Fall back to last_price field from the market object (in cents → fraction)
        last_cents = market.get("last_price") or market.get("yes_bid") or 50
        current_price = last_cents / 100.0

    signals: list[Signal] = []

    for check_fn, kwargs in [
        (check_momentum,  dict(ticker=ticker, title=title, current_price=current_price)),
        (check_liquidity, dict(ticker=ticker, title=title, current_price=current_price,
                               orderbook=orderbook)),
        (check_mispricing, dict(ticker=ticker, title=title, current_price=current_price,
                                fair_value=fair_value)),
    ]:
        try:
            sig = check_fn(**kwargs)
            if sig:
                signals.append(sig)
                # Persist to DB immediately.
                database.insert_signal(
                    ticker=sig.ticker,
                    title=sig.title,
                    signal_type=sig.signal_type,
                    current_price=sig.current_price,
                    fair_value=sig.fair_value,
                    recommended=sig.recommended,
                    detail=sig.detail,
                )
        except Exception as exc:
            logger.warning("Signal check %s failed for %s: %s",
                           check_fn.__name__, ticker, exc)

    return signals


# ── Helpers ───────────────────────────────────────────────────────────────────

def _best_price(levels: list, side: str) -> float | None:
    """
    Extract the best price (as a fraction 0–1) from an order book side.

    Kalshi order book levels are [price_cents, size] pairs.
    For the YES side:
      - 'ask' is the lowest price someone will sell YES at (first level when
        Kalshi returns levels sorted ascending)
      - 'bid' is the highest price someone will buy YES at (last level)

    We keep it simple: use the first non-zero level for ask, last for bid.
    """
    if not levels:
        return None
    try:
        if side == "ask":
            return levels[0][0] / 100.0
        else:  # bid
            return levels[-1][0] / 100.0
    except (IndexError, TypeError):
        return None
