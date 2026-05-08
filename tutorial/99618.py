import json
from typing import Dict, List, Optional, Tuple
from datamodel import Order, OrderDepth, TradingState


class Trader:

    LIMITS = {
        "EMERALDS": 20,
        "TOMATOES": 20,
    }

    DEFAULT_FAIR = {
        "EMERALDS": 10000.0,
        "TOMATOES": 5000.0,
    }

    # Base EMA alpha — dynamically adjusted per tick by spread regime
    EMA_ALPHA = {
        "EMERALDS": 0.15,
        "TOMATOES": 0.35,
    }

    BASE_SPREAD = {
        "EMERALDS": 2,
        "TOMATOES": 2,
    }

    QUOTE_SIZE = {
        "EMERALDS": 5,
        "TOMATOES": 4,
    }

    # How strongly multi-level OFI nudges fair value
    OFI_WEIGHT = {
        "EMERALDS": 0.3,
        "TOMATOES": 0.5,
    }

    # How strongly full-book depth imbalance nudges fair value
    DEPTH_IMBALANCE_WEIGHT = {
        "EMERALDS": 0.4,
        "TOMATOES": 0.6,
    }

    # Spread reference used for volatility-adaptive alpha (calm market baseline)
    TYPICAL_SPREAD = {
        "EMERALDS": 2.0,
        "TOMATOES": 2.0,
    }

    # [NEW A] Momentum: EMA smoothing of fair-value deltas
    MOMENTUM_ALPHA = {
        "EMERALDS": 0.2,
        "TOMATOES": 0.3,
    }

    # [NEW A] How strongly the momentum EMA nudges fair value
    MOMENTUM_WEIGHT = {
        "EMERALDS": 0.25,
        "TOMATOES": 0.40,
    }

    # [NEW B] Number of book levels to include in multi-level OFI
    OFI_LEVELS = 3

    # [NEW B] Decay weight per level: level 1 = 1.0, level 2 = 0.5, level 3 = 0.25
    OFI_LEVEL_DECAY = 0.5


    def run(self, state: TradingState):

        persisted = self._decode_state(state.traderData)
        ema_fair  = persisted.get("ema_fair", {})
        prev_book = persisted.get("prev_book", {})       # multi-level snapshots
        ema_delta = persisted.get("ema_delta", {})       # [NEW A] momentum state
        next_book = {}

        result = {}

        for product, depth in state.order_depths.items():

            if product not in self.LIMITS:
                continue

            # ── Snapshot top-N levels for next tick's multi-level OFI ──────────
            next_book[product] = self._snapshot_levels(depth, self.OFI_LEVELS)
            # ───────────────────────────────────────────────────────────────────

            prev_fair = ema_fair.get(product, self.DEFAULT_FAIR[product])

            fair = self._compute_fair_value(
                product, depth, ema_fair, prev_book.get(product, {}), ema_delta
            )

            # ── [NEW A] Update momentum EMA ────────────────────────────────────
            delta = fair - prev_fair
            m_alpha = self.MOMENTUM_ALPHA[product]
            ema_delta[product] = (1 - m_alpha) * ema_delta.get(product, 0.0) + m_alpha * delta
            # ───────────────────────────────────────────────────────────────────

            ema_fair[product] = fair

            position = state.position.get(product, 0)

            orders = self._build_orders(product, depth, fair, position)

            if orders:
                result[product] = orders

        trader_data = json.dumps({
            "ema_fair":  ema_fair,
            "prev_book": next_book,
            "ema_delta": ema_delta,
        })

        return result, 0, trader_data


    def _compute_fair_value(self, product, depth, ema_fair, prev_book, ema_delta):

        previous = ema_fair.get(product, self.DEFAULT_FAIR[product])

        multi_micro = self._multi_level_microprice(depth)

        if multi_micro is None:
            return previous

        # ── Volatility-adaptive alpha ──────────────────────────────────────────
        best_bid = max(depth.buy_orders.keys()) if depth.buy_orders else None
        best_ask = min(depth.sell_orders.keys()) if depth.sell_orders else None
        if best_bid is not None and best_ask is not None:
            spread  = best_ask - best_bid
            typical = self.TYPICAL_SPREAD[product]
            vol_scale = max(0.5, min(2.5, spread / typical))
            alpha = min(0.7, max(0.05, self.EMA_ALPHA[product] * vol_scale))
        else:
            alpha = self.EMA_ALPHA[product]
        # ───────────────────────────────────────────────────────────────────────

        fair = (1 - alpha) * previous + alpha * multi_micro

        # ── Full-book depth imbalance nudge ────────────────────────────────────
        depth_imbalance = self._depth_imbalance(depth)
        half_spread = (best_ask - best_bid) / 2.0 if (best_bid and best_ask) else 1.0
        fair += self.DEPTH_IMBALANCE_WEIGHT[product] * depth_imbalance * half_spread
        # ───────────────────────────────────────────────────────────────────────

        # ── [NEW B] Multi-level OFI nudge ─────────────────────────────────────
        # Weighted OFI across top-N levels: level k carries weight decay^(k-1)
        ofi = self._multi_level_ofi(depth, prev_book)
        fair += self.OFI_WEIGHT[product] * ofi
        # ───────────────────────────────────────────────────────────────────────

        # ── [NEW A] Momentum nudge ─────────────────────────────────────────────
        # If fair has been drifting up, keep leaning that way; vice versa.
        momentum = ema_delta.get(product, 0.0)
        fair += self.MOMENTUM_WEIGHT[product] * momentum
        # ───────────────────────────────────────────────────────────────────────

        return fair


    # ── Helpers ────────────────────────────────────────────────────────────────

    def _snapshot_levels(self, depth, n: int) -> dict:
        """Store top-n bid and ask levels for next tick's multi-level OFI."""
        bids = sorted(depth.buy_orders.items(), reverse=True)[:n]
        asks = sorted(depth.sell_orders.items())[:n]
        return {
            "bids": [[px, vol] for px, vol in bids],
            "asks": [[px, vol] for px, vol in asks],
        }


    def _multi_level_ofi(self, depth, prev_book: dict) -> float:
        """
        [NEW B] Multi-level Order Flow Imbalance.
        Compares current top-N bid/ask levels to previous snapshot.
        Level k is weighted by OFI_LEVEL_DECAY^(k-1) so level 1 dominates.
        Result is normalized by total book volume → scale-independent.
        """
        if not prev_book:
            return 0.0

        curr_bids = sorted(depth.buy_orders.items(), reverse=True)[:self.OFI_LEVELS]
        curr_asks = sorted(depth.sell_orders.items())[:self.OFI_LEVELS]
        prev_bids = prev_book.get("bids", [])
        prev_asks = prev_book.get("asks", [])

        raw_ofi = 0.0
        decay   = self.OFI_LEVEL_DECAY

        for k, (curr_px, curr_vol) in enumerate(curr_bids):
            weight = decay ** k
            if k < len(prev_bids):
                prev_px, prev_vol = prev_bids[k]
                if curr_px > prev_px:
                    bid_ofi = float(curr_vol)
                elif curr_px == prev_px:
                    bid_ofi = float(curr_vol - prev_vol)
                else:
                    bid_ofi = -float(prev_vol)
            else:
                bid_ofi = float(curr_vol)
            raw_ofi += weight * bid_ofi

        for k, (curr_px, curr_vol) in enumerate(curr_asks):
            weight   = decay ** k
            curr_vol = -curr_vol   # asks stored as negative
            if k < len(prev_asks):
                prev_px, prev_vol = prev_asks[k]
                prev_vol = -prev_vol
                if curr_px < prev_px:
                    ask_ofi = float(curr_vol)
                elif curr_px == prev_px:
                    ask_ofi = float(curr_vol - prev_vol)
                else:
                    ask_ofi = -float(prev_vol)
            else:
                ask_ofi = float(curr_vol)
            raw_ofi -= weight * ask_ofi   # ask-side OFI subtracts

        total_vol = float(
            sum(depth.buy_orders.values()) + sum(-v for v in depth.sell_orders.values())
        )
        if total_vol == 0:
            return 0.0

        return raw_ofi / total_vol


    def _multi_level_microprice(self, depth):
        """Volume-weighted price across top 2 bid and ask levels."""
        bids = sorted(depth.buy_orders.items(), reverse=True)[:2]
        asks = sorted(depth.sell_orders.items())[:2]

        weighted_sum = 0
        total_weight = 0

        for price, volume in bids:
            weighted_sum += price * volume
            total_weight += volume

        for price, volume in asks:
            volume = -volume
            weighted_sum += price * volume
            total_weight += volume

        if total_weight == 0:
            return None

        return weighted_sum / total_weight


    def _depth_imbalance(self, depth) -> float:
        """
        Returns a value in [-1, +1].
        +1 = all book volume on the bid (bullish). -1 = all on the ask (bearish).
        Uses all visible levels.
        """
        total_bid = sum(depth.buy_orders.values())
        total_ask = sum(-v for v in depth.sell_orders.values())
        denom = total_bid + total_ask
        if denom == 0:
            return 0.0
        return (total_bid - total_ask) / denom


    def _build_orders(self, product, depth, fair, position):

        orders = []
        limit = self.LIMITS[product]

        best_bid = max(depth.buy_orders.keys()) if depth.buy_orders else None
        best_ask = min(depth.sell_orders.keys()) if depth.sell_orders else None

        if best_bid is None or best_ask is None:
            return orders

        spread    = best_ask - best_bid
        threshold = max(1, spread * 0.6)

        buy_capacity  = limit - position
        sell_capacity = limit + position

        # ── [NEW C] Book-sweeping aggressive taker ─────────────────────────────
        # Walk ALL ask levels while the price is still cheap vs fair - threshold.
        # Previously only hit the single best ask, leaving edge on the table.
        for ask_px in sorted(depth.sell_orders.keys()):
            if buy_capacity <= 0:
                break
            if ask_px > fair - threshold:
                break
            ask_liq = abs(depth.sell_orders[ask_px])
            qty = min(buy_capacity, ask_liq)
            orders.append(Order(product, ask_px, qty))
            buy_capacity -= qty

        # Walk ALL bid levels while the price is still rich vs fair + threshold.
        for bid_px in sorted(depth.buy_orders.keys(), reverse=True):
            if sell_capacity <= 0:
                break
            if bid_px < fair + threshold:
                break
            bid_liq = depth.buy_orders[bid_px]
            qty = min(sell_capacity, bid_liq)
            orders.append(Order(product, bid_px, -qty))
            sell_capacity -= qty
        # ───────────────────────────────────────────────────────────────────────

        inv_ratio = position / limit if limit else 0
        skew      = int(round(3 * inv_ratio))
        width     = self.BASE_SPREAD[product]

        if abs(inv_ratio) > 0.6:
            width += 1

        bid_quote = int(round(fair - width - skew))
        ask_quote = int(round(fair + width - skew))

        # Inside-spread positioning
        if fair > best_bid:
            bid_quote = best_bid + 1

        if fair < best_ask:
            ask_quote = best_ask - 1

        if bid_quote >= ask_quote:
            ask_quote = bid_quote + 1

        quote_size = self.QUOTE_SIZE[product]

        if buy_capacity > 0:
            orders.append(Order(product, bid_quote, min(quote_size, buy_capacity)))

        if sell_capacity > 0:
            orders.append(Order(product, ask_quote, -min(quote_size, sell_capacity)))

        return orders


    def _decode_state(self, raw):

        if not raw:
            return {}

        try:
            return json.loads(raw)

        except Exception:
            return {}