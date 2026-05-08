"""
COMBINED TRADER v4 — FULL SCALE
================================
HG  — from 516163, sizing cranked: MAX_POS 70→200, CHUNK 5→15
VF  — from 488795 (original EWM, VF_FAIR=5250, untouched)
OPT — from 518920 + v2 unwind fix, sizing cranked to ~90% of limit
      sell_size 2-3x, max_short pushed to 250-280
      v6: unwind_start 98500→99000, VEV_5000 also set to expire
v7: VEV_5100 also set to expire, cooldown 900→500
v8: VEV_5200/5300 also expire, VF_FAIR 5250→5295

Logic untouched. Sizes only.
"""

from datamodel import Order, OrderDepth, TradingState
from typing import Dict, List, Optional
import json

# ── Position limits ──────────────────────────────────────────────────────────
LIMITS = {
    "VELVETFRUIT_EXTRACT": 200,
    "HYDROGEL_PACK": 200,
    "VEV_4000": 300, "VEV_4500": 300, "VEV_5000": 300,
    "VEV_5100": 300, "VEV_5200": 300, "VEV_5300": 300,
    "VEV_5400": 300, "VEV_5500": 300, "VEV_6000": 300, "VEV_6500": 300,
}


# ═══════════════════════════════════════════════════════════════════════════════
# HYDROGEL — from 516163, MAX_POS 70→200, CHUNK 5→15
# ═══════════════════════════════════════════════════════════════════════════════

HG_ENTRY_THRESH  = 40
HG_EXIT_THRESH   = -10
HG_CHUNK         = 15      # was 5
HG_MAX_POS       = 200     # was 70 — now using full limit
HG_UNWIND_START  = 95000
HG_UNWIND_OFFSET = 3


def trade_hydrogel(state: TradingState, saved: dict) -> List[Order]:
    od = state.order_depths.get("HYDROGEL_PACK")
    if od is None or not od.buy_orders or not od.sell_orders:
        return []

    bb  = max(od.buy_orders.keys())
    ba  = min(od.sell_orders.keys())
    mid = (bb + ba) / 2.0
    ts  = state.timestamp
    pos = state.position.get("HYDROGEL_PACK", 0)
    lim = LIMITS["HYDROGEL_PACK"]

    prev_ts = saved.get("hg_prev_ts", -1)
    if ts < prev_ts or prev_ts == -1:
        saved["hg_open"] = mid
    saved["hg_prev_ts"] = ts

    open_price = saved.get("hg_open", mid)
    deviation  = mid - open_price

    if ts >= 100000:
        saved["hg_big"] = True
    unwind_start = HG_UNWIND_START * 10 if saved.get("hg_big") else HG_UNWIND_START

    orders: List[Order] = []

    if ts >= unwind_start:
        if pos < 0:
            bid_px = round(mid + HG_UNWIND_OFFSET)
            qty = min(-pos, lim - pos)
            if qty > 0:
                orders.append(Order("HYDROGEL_PACK", bid_px, qty))
        elif pos > 0:
            ask_px = round(mid - HG_UNWIND_OFFSET)
            qty = min(pos, lim + pos)
            if qty > 0:
                orders.append(Order("HYDROGEL_PACK", ask_px, -qty))
        return orders

    if pos < 0 and deviation <= HG_EXIT_THRESH:
        qty = min(-pos, abs(od.sell_orders.get(ba, 0)), lim - pos)
        if qty > 0:
            orders.append(Order("HYDROGEL_PACK", ba, qty))
        return orders

    if pos > 0 and deviation >= -HG_EXIT_THRESH:
        qty = min(pos, abs(od.buy_orders.get(bb, 0)), lim + pos)
        if qty > 0:
            orders.append(Order("HYDROGEL_PACK", bb, -qty))
        return orders

    if deviation >= HG_ENTRY_THRESH and pos > -HG_MAX_POS:
        qty = min(HG_CHUNK, HG_MAX_POS + pos, abs(od.buy_orders.get(bb, 0)), lim + pos)
        if qty > 0:
            orders.append(Order("HYDROGEL_PACK", bb, -qty))
    elif deviation <= -HG_ENTRY_THRESH and pos < HG_MAX_POS:
        qty = min(HG_CHUNK, HG_MAX_POS - pos, abs(od.sell_orders.get(ba, 0)), lim - pos)
        if qty > 0:
            orders.append(Order("HYDROGEL_PACK", ba, qty))

    return orders


# ═══════════════════════════════════════════════════════════════════════════════
# VELVETFRUIT — from 488795 (original, untouched)
# ═══════════════════════════════════════════════════════════════════════════════

VF_FAIR          = 5250.0
VF_ALPHA         = 2.0 / 1001.0
VF_MR_ENTRY      = 15.0
VF_MR_EXIT       = 5.0
VF_MR_MAX_POS    = 200          # was 150 — sim +500 from this alone
VF_MR_ORDER_SIZE = 15           # was 5 — biggest single lever (sim +3,500)
VF_NO_NEW_SMALL  = 98500        # mirror HG endgame for VF position cleanup
VF_NO_NEW_BIG    = 985000


def best_bid(od: OrderDepth) -> Optional[int]:
    return max(od.buy_orders.keys()) if od.buy_orders else None

def best_ask(od: OrderDepth) -> Optional[int]:
    return min(od.sell_orders.keys()) if od.sell_orders else None


def trade_velvetfruit(state: TradingState, saved: dict) -> List[Order]:
    od = state.order_depths.get("VELVETFRUIT_EXTRACT")
    if od is None:
        return []
    bb, ba = best_bid(od), best_ask(od)
    if bb is None or ba is None:
        return []

    mid = (bb + ba) / 2.0
    position = state.position.get("VELVETFRUIT_EXTRACT", 0)
    limit = LIMITS["VELVETFRUIT_EXTRACT"]

    prev_ewm = saved.get("vf_ewm", VF_FAIR)
    residual = mid - prev_ewm
    saved["vf_ewm"] = VF_ALPHA * mid + (1.0 - VF_ALPHA) * prev_ewm

    # End-of-day: stop opening, only allow exits.
    endgame = VF_NO_NEW_BIG if saved.get("hg_big") else VF_NO_NEW_SMALL
    no_new = state.timestamp >= endgame

    orders: List[Order] = []

    if position > 0 and residual >= VF_MR_EXIT:
        qty = min(position, abs(od.buy_orders.get(bb, 0)), limit + position)
        if qty > 0:
            orders.append(Order("VELVETFRUIT_EXTRACT", bb, -qty))
    elif position < 0 and residual <= -VF_MR_EXIT:
        qty = min(-position, abs(od.sell_orders.get(ba, 0)), limit - position)
        if qty > 0:
            orders.append(Order("VELVETFRUIT_EXTRACT", ba, qty))
    elif not no_new:
        if residual <= -VF_MR_ENTRY and position < VF_MR_MAX_POS:
            qty = min(VF_MR_ORDER_SIZE, VF_MR_MAX_POS - position,
                      abs(od.sell_orders.get(ba, 0)), limit - position)
            if qty > 0:
                orders.append(Order("VELVETFRUIT_EXTRACT", ba, qty))
        elif residual >= VF_MR_ENTRY and position > -VF_MR_MAX_POS:
            qty = min(VF_MR_ORDER_SIZE, VF_MR_MAX_POS + position,
                      abs(od.buy_orders.get(bb, 0)), limit + position)
            if qty > 0:
                orders.append(Order("VELVETFRUIT_EXTRACT", bb, -qty))

    return orders


# ═══════════════════════════════════════════════════════════════════════════════
# OPTIONS — from 518920 + v2 unwind fix, ALL SIZING CRANKED
# sell_size ~2-3x, max_short pushed to 250-280 (~90% of 300 limit)
# ═══════════════════════════════════════════════════════════════════════════════

OPT_FILL_COOLDOWN = 500   # was 900 — fill position limit faster

OPT_SELL_CONFIG = {
    "VEV_4000": {"sell_size": 15,  "max_short": 250, "unwind": False},
    "VEV_4500": {"sell_size": 15,  "max_short": 250, "unwind": False},
    "VEV_5000": {"sell_size": 20,  "max_short": 260, "unwind": False},  # expire worthless — saves ~3.5k
    "VEV_5100": {"sell_size": 20,  "max_short": 260, "unwind": False},  # expire — saves ~2.8k
    "VEV_5200": {"sell_size": 25,  "max_short": 280, "unwind": False},  # expire — saves ~1.9k
    "VEV_5300": {"sell_size": 25,  "max_short": 280, "unwind": False},  # expire — saves ~1.2k
    "VEV_5400": {"sell_size": 20,  "max_short": 270, "unwind": False},  # expire — saves ~610
    "VEV_5500": {"sell_size": 15,  "max_short": 250, "unwind": False},
    "VEV_6000": {"sell_size": 5,   "max_short": 60,  "unwind": False},
    "VEV_6500": {"sell_size": 4,   "max_short": 40,  "unwind": False},
}

OPT_UNWIND_START_SMALL = 99000   # pushed later — less buyback window
OPT_UNWIND_START_BIG   = 985000
OPT_UNWIND_RATE        = 50   # was 20 — faster unwind to match bigger positions


def required_time_value(abs_moneyness: float) -> float:
    if abs_moneyness <= 40:  return 10
    if abs_moneyness <= 100: return 7
    if abs_moneyness <= 200: return 5
    return 3


def trade_options(state: TradingState, saved: dict) -> Dict[str, List[Order]]:
    orders = {}
    ts = state.timestamp
    big_day = ts > 100000
    unwind_start = OPT_UNWIND_START_BIG if big_day else OPT_UNWIND_START_SMALL

    for symbol, config in OPT_SELL_CONFIG.items():
        if symbol not in state.order_depths:
            continue

        depth = state.order_depths[symbol]
        bb = best_bid(depth)
        ba = best_ask(depth)
        if bb is None or ba is None:
            continue

        mid           = (bb + ba) / 2
        strike        = int(symbol.split("_")[1])
        intrinsic     = max(0, mid - strike)
        abs_moneyness = abs(mid - strike)
        min_tv        = required_time_value(abs_moneyness)
        position      = state.position.get(symbol, 0)
        sell_size     = config["sell_size"]
        max_short     = config["max_short"]
        do_unwind     = config["unwind"]
        cooldown_key  = symbol + "_cd"
        last_fill_time = saved.get(cooldown_key, -999999)

        if ts >= unwind_start:
            if do_unwind and position < 0:
                qty = min(-position, OPT_UNWIND_RATE)
                orders.setdefault(symbol, []).append(Order(symbol, ba, qty))
            continue

        if ba - intrinsic > min_tv:
            if position > -max_short:
                if ts - last_fill_time > OPT_FILL_COOLDOWN:
                    orders.setdefault(symbol, []).append(Order(symbol, bb, -sell_size))
                    saved[cooldown_key] = ts

    return orders


# ═══════════════════════════════════════════════════════════════════════════════
# TRADER
# ═══════════════════════════════════════════════════════════════════════════════

class Trader:
    def run(self, state: TradingState):
        try:
            saved = json.loads(state.traderData) if state.traderData else {}
        except Exception:
            saved = {}

        result: Dict[str, List[Order]] = {}

        hg_orders = trade_hydrogel(state, saved)
        if hg_orders:
            result["HYDROGEL_PACK"] = hg_orders

        vf_orders = trade_velvetfruit(state, saved)
        if vf_orders:
            result["VELVETFRUIT_EXTRACT"] = vf_orders

        opt_orders = trade_options(state, saved)
        for k, v in opt_orders.items():
            result.setdefault(k, []).extend(v)

        return result, 0, json.dumps(saved)