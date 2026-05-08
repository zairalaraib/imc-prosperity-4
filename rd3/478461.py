"""
IMC Prosperity 4 - Round 3 - v10 (PRODUCTION)
==============================================
Diagnosis from 428622 (live):
  HG  : +10,440  ✓ working as intended
  VF  : +881     ✗ underperforming — MM adverse-selection bleeding ~3-4k
  OPT : +11      ✗ cooldown bug throttling fills to 4 per day

Changes vs v9 / 428622:
  1. HG : untouched (workhorse)
  2. VF : KILL the delta-1 MM, BEEF UP the MR
         - mr_max_pos: 150 → 200 (use full position limit)
         - mr_order_size: 5 → 15 (3x size per signal)
         - Removed bb+1/ba-1 quoting block entirely
         - Sim: 881 → ~5,200 (+4,300)
  3. OPT: Cooldown now triggers on FILLS (position decrease), not order placement
         - Removed the 3000-tick cooldown wall (set to 0)
         - Loosened near-ATM time-value filter: 14 → 10
         - Auto-detect 100k vs 1M day for end-of-day unwind (mirrors HG pattern)
         - Sim: +11 → ~+500 (+500)

Expected combined uplift: 11,332 → ~16,000-17,000 SeaShells
"""

from datamodel import Order, OrderDepth, TradingState
from typing import Dict, List, Optional, Tuple
import json

# ── Position limits ──────────────────────────────────────────────────────────
LIMITS = {
    "VELVETFRUIT_EXTRACT": 200,
    "HYDROGEL_PACK": 200,
    "VEV_4000": 300, "VEV_4500": 300, "VEV_5000": 300,
    "VEV_5100": 300, "VEV_5200": 300, "VEV_5300": 300,
    "VEV_5400": 300, "VEV_5500": 300, "VEV_6000": 300, "VEV_6500": 300,
}

# ── Hydrogel (unchanged from v9 — proven 10,440 on platform) ──────────────────
HG_FAIR        = 10000.0
HG_ALPHA       = 2.0 / 1001.0
HG_ENTRY       = 45.0
HG_EXIT        = 5.0
HG_MAX_POS     = 200
HG_ORDER_SIZE  = 3
HG_SMALL_END   = 98500
HG_BIG_END     = 985000

# ── Velvetfruit (MR-only, beefed up) ─────────────────────────────────────────
VF_FAIR          = 5250.0
VF_ALPHA         = 2.0 / 1001.0
VF_MR_ENTRY      = 15.0
VF_MR_EXIT       = 5.0
VF_MR_MAX_POS    = 200          # was 150 — sim +500 from this alone
VF_MR_ORDER_SIZE = 15           # was 5 — biggest single lever (sim +3,500)
VF_NO_NEW_SMALL  = 98500        # mirror HG endgame for VF position cleanup
VF_NO_NEW_BIG    = 985000

# ── Options ───────────────────────────────────────────────────────────────────
# Sell sizes / max-shorts unchanged from v9 (already calibrated).
OPT_SELL_ALL = {
    "VEV_4000": {"sell_size": 6,  "max_short": 80},
    "VEV_4500": {"sell_size": 6,  "max_short": 80},
    "VEV_5000": {"sell_size": 8,  "max_short": 100},
    "VEV_5100": {"sell_size": 8,  "max_short": 100},
    "VEV_5200": {"sell_size": 12, "max_short": 130},
    "VEV_5300": {"sell_size": 12, "max_short": 130},
    "VEV_5400": {"sell_size": 10, "max_short": 120},
    "VEV_5500": {"sell_size": 8,  "max_short": 100},
    "VEV_6000": {"sell_size": 5,  "max_short": 60},
    "VEV_6500": {"sell_size": 4,  "max_short": 40},
}
# End-of-day unwind: auto-detect 100k vs 1M day length.
OPT_UNWIND_START_SMALL = 98000
OPT_UNWIND_START_BIG   = 980000
OPT_UNWIND_RATE        = 20

# Time-value filter — loosened on near-ATM strikes.
def _required_tv(abs_moneyness: float) -> float:
    if abs_moneyness <= 40:  return 10.0   # was 14
    if abs_moneyness <= 100: return 7.0    # was 10
    if abs_moneyness <= 200: return 5.0    # was 7
    return 3.0                              # was 5

# Cooldown only fires after an actual fill (position drops), preventing
# rapid-fire shorts on a single price spike but no longer throttling refresh.
OPT_FILL_COOLDOWN_TICKS = 1000


# ── Helpers ───────────────────────────────────────────────────────────────────
def best_bid(od: OrderDepth) -> Optional[int]:
    return max(od.buy_orders.keys()) if od.buy_orders else None

def best_ask(od: OrderDepth) -> Optional[int]:
    return min(od.sell_orders.keys()) if od.sell_orders else None


# ── Hydrogel: anchored mean-reversion (UNCHANGED from v9) ────────────────────
def trade_hydrogel(state: TradingState, saved: dict) -> List[Order]:
    od = state.order_depths.get("HYDROGEL_PACK")
    if od is None or not od.buy_orders or not od.sell_orders:
        return []

    bb = max(od.buy_orders.keys())
    ba = min(od.sell_orders.keys())
    bid_qty = abs(od.buy_orders[bb])
    ask_qty = abs(od.sell_orders[ba])
    mid = (bb + ba) / 2.0

    position = state.position.get("HYDROGEL_PACK", 0)
    limit = LIMITS["HYDROGEL_PACK"]

    prev_ewm = saved.get("hg_ewm", HG_FAIR)
    residual = mid - prev_ewm

    if state.timestamp >= 100000:
        saved["hg_big"] = True
    endgame = HG_BIG_END if saved.get("hg_big") else HG_SMALL_END
    no_new = state.timestamp >= endgame

    orders: List[Order] = []

    if position > 0 and residual >= HG_EXIT:
        qty = min(position, bid_qty, limit + position)
        if qty > 0:
            orders.append(Order("HYDROGEL_PACK", bb, -qty))
    elif position < 0 and residual <= -HG_EXIT:
        qty = min(-position, ask_qty, limit - position)
        if qty > 0:
            orders.append(Order("HYDROGEL_PACK", ba, qty))
    elif not no_new:
        if residual <= -HG_ENTRY and position < HG_MAX_POS:
            qty = min(HG_ORDER_SIZE, HG_MAX_POS - position, ask_qty, limit - position)
            if qty > 0:
                orders.append(Order("HYDROGEL_PACK", ba, qty))
        elif residual >= HG_ENTRY and position > -HG_MAX_POS:
            qty = min(HG_ORDER_SIZE, HG_MAX_POS + position, bid_qty, limit + position)
            if qty > 0:
                orders.append(Order("HYDROGEL_PACK", bb, -qty))

    saved["hg_ewm"] = HG_ALPHA * mid + (1.0 - HG_ALPHA) * prev_ewm
    return orders


# ── Velvetfruit: MR ONLY (no MM — that was the bleed) ────────────────────────
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


# ── Options: passive sells with FILL-triggered cooldown ──────────────────────
def trade_options_passive(state: TradingState, saved: dict) -> Dict[str, List[Order]]:
    result: Dict[str, List[Order]] = {}
    u_od = state.order_depths.get("VELVETFRUIT_EXTRACT")
    if u_od is None:
        return result
    u_bb, u_ba = best_bid(u_od), best_ask(u_od)
    if u_bb is None or u_ba is None:
        return result
    underlying_mid = (u_bb + u_ba) / 2.0

    # Cooldowns now only set on FILLS (position decreased since last tick).
    cooldowns = saved.setdefault("opt_cooldowns", {})
    prev_pos = saved.setdefault("opt_prev_pos", {})
    unwind_start = OPT_UNWIND_START_BIG if saved.get("hg_big") else OPT_UNWIND_START_SMALL

    for product, params in OPT_SELL_ALL.items():
        od = state.order_depths.get(product)
        if od is None:
            continue
        bb, ba = best_bid(od), best_ask(od)
        if bb is None or ba is None:
            continue

        position = state.position.get(product, 0)
        limit = LIMITS[product]
        max_short = params["max_short"]
        sell_size = params["sell_size"]

        # Detect a fill since last tick (position dropped). Set cooldown.
        prior = prev_pos.get(product, 0)
        if position < prior:
            cooldowns[product] = state.timestamp
        prev_pos[product] = position

        # End-of-day: buy back shorts to flatten gamma exposure.
        if state.timestamp >= unwind_start and position < 0:
            buy_qty = min(-position, OPT_UNWIND_RATE,
                          limit - position, abs(od.sell_orders.get(ba, 0)))
            if buy_qty > 0:
                result[product] = [Order(product, ba, buy_qty)]
            continue

        last_ts = cooldowns.get(product, -10**9)
        in_cooldown = state.timestamp - last_ts < OPT_FILL_COOLDOWN_TICKS
        if in_cooldown:
            continue

        if position <= -max_short or ba - bb < 2:
            continue

        # Only short if there's enough time-value over intrinsic.
        strike = int(product.split("_")[1])
        mid = (bb + ba) / 2.0
        intrinsic = max(0.0, underlying_mid - strike)
        time_value = mid - intrinsic
        if time_value < _required_tv(abs(underlying_mid - strike)):
            continue

        qty = min(sell_size, limit + position, max_short + position)
        if qty <= 0:
            continue

        result[product] = [Order(product, ba - 1, -qty)]
    return result


# ── Trader ───────────────────────────────────────────────────────────────────
class Trader:
    def run(self, state: TradingState) -> Tuple[Dict[str, List[Order]], int, str]:
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

        opt_orders = trade_options_passive(state, saved)
        result.update(opt_orders)

        return result, 0, json.dumps(saved)