from datamodel import OrderDepth, TradingState, Order
from typing import List, Dict

# ══════════════════════════════════════════════════════════════════
#  STRATEGY: PURE DIRECTIONAL
#
#  Every product below has a consistent intraday trend across
#  ALL 3 sample days. Enter max position at open, hold all day,
#  flatten at close. That's it. No MM, no pairs, no complexity.
#
#  Theoretical max PnL per day (10 units, open→close):
#    MICROCHIP_OVAL        SHORT  ~15,000
#    PEBBLES_XS            SHORT  ~13,000
#    OXYGEN_SHAKE_GARLIC   LONG   ~13,000
#    GALAXY_SOUNDS_BLACK_HOLES LONG ~11,000
#    UV_VISOR_AMBER        SHORT   ~9,500
#    PANEL_2X4             LONG    ~7,900
#    PEBBLES_S             SHORT   ~6,500
#    SNACKPACK_PISTACHIO   SHORT   ~3,000
#    SNACKPACK_CHOCOLATE   SHORT   ~1,100
# ══════════════════════════════════════════════════════════════════

POSITION_LIMIT = 10
TICKS_PER_DAY  = 10000  # timestamps go 0, 100, 200 ... 999900

# (product, direction)  +1 = long,  -1 = short
# Sorted by 3-day average daily PnL descending
DIRECTIONAL = [
    ("MICROCHIP_OVAL",             -1),   # avg ~14,900/day
    ("PEBBLES_XS",                 -1),   # avg ~13,300/day
    ("OXYGEN_SHAKE_GARLIC",        +1),   # avg ~13,000/day
    ("GALAXY_SOUNDS_BLACK_HOLES",  +1),   # avg ~11,500/day
    ("UV_VISOR_AMBER",             -1),   # avg  ~9,500/day
    ("PANEL_2X4",                  +1),   # avg  ~7,900/day
    ("PEBBLES_S",                  -1),   # avg  ~6,500/day
    ("SNACKPACK_PISTACHIO",        -1),   # avg  ~3,000/day
    ("SNACKPACK_CHOCOLATE",        -1),   # avg  ~1,100/day
]

# Enter during first 500 ticks of day (timestamps 0–49900)
# Exit during last 500 ticks of day (timestamps 950000–999900)
ENTRY_CLOSE_TICK = 500   # tick-of-day index (multiply by 100 for timestamp)
EXIT_OPEN_TICK   = 9500


class Trader:

    def run(self, state: TradingState):
        result: Dict[str, List[Order]] = {}

        # Tick index within the day: 0, 1, 2, ... 9999
        tick = (state.timestamp % (TICKS_PER_DAY * 100)) // 100

        for product, direction in DIRECTIONAL:
            od  = state.order_depths.get(product)
            if not od:
                continue

            pos     = state.position.get(product, 0)
            orders  = []
            target  = direction * POSITION_LIMIT

            if tick <= ENTRY_CLOSE_TICK:
                # ── OPEN WINDOW: drive position to max in our direction ──
                delta = target - pos
                if delta > 0:
                    # Need to buy
                    best_ask = min(od.sell_orders) if od.sell_orders else None
                    if best_ask is not None:
                        qty = min(delta, POSITION_LIMIT - pos)
                        if qty > 0:
                            orders.append(Order(product, best_ask, qty))
                elif delta < 0:
                    # Need to sell short
                    best_bid = max(od.buy_orders) if od.buy_orders else None
                    if best_bid is not None:
                        qty = max(delta, -POSITION_LIMIT - pos)
                        if qty < 0:
                            orders.append(Order(product, best_bid, qty))

            elif tick >= EXIT_OPEN_TICK:
                # ── CLOSE WINDOW: flatten to zero ──
                if pos > 0:
                    best_bid = max(od.buy_orders) if od.buy_orders else None
                    if best_bid is not None:
                        orders.append(Order(product, best_bid, -pos))
                elif pos < 0:
                    best_ask = min(od.sell_orders) if od.sell_orders else None
                    if best_ask is not None:
                        orders.append(Order(product, best_ask, -pos))

            # Between entry and exit: hold, do nothing

            if orders:
                result[product] = orders

        return result, 0, ""