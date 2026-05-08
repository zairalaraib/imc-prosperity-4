import json
from typing import Dict, List, Optional, Tuple
from datamodel import Order, OrderDepth, TradingState


class Trader:

    LIMITS = {
        "ASH_COATED_OSMIUM": 20,
        "INTARIAN_PEPPER_ROOT": 20,
    }

    DEFAULT_FAIR = {
        "ASH_COATED_OSMIUM": 10000,
        "INTARIAN_PEPPER_ROOT": 12000,
    }

    FAST_ALPHA = {
        "ASH_COATED_OSMIUM": 0.28,
        "INTARIAN_PEPPER_ROOT": 0.33,
    }

    SLOW_ALPHA = {
        "ASH_COATED_OSMIUM": 0.05,
        "INTARIAN_PEPPER_ROOT": 0.07,
    }

    # FIXED TREND WEIGHTS
    TREND_WEIGHT = {
        "ASH_COATED_OSMIUM": 0.0,
        "INTARIAN_PEPPER_ROOT": 0.0,
    }

    # FIXED IMBALANCE WEIGHT
    IMBALANCE_WEIGHT = 0.8

    TAKE_EDGE = {
        "ASH_COATED_OSMIUM": 0.6,
        "INTARIAN_PEPPER_ROOT": 0.7,
    }

    BASE_SPREAD = 1

    QUOTE_SIZE = {
        "ASH_COATED_OSMIUM": 18,
        "INTARIAN_PEPPER_ROOT": 16,
    }


    def run(self, state: TradingState):

        data = self.decode(state.traderData)

        fast = data.get("fast", {})
        slow = data.get("slow", {})

        result = {}

        for product in state.order_depths:

            if product not in self.LIMITS:
                continue

            depth = state.order_depths[product]

            mid = self.midprice(product, depth, fast)

            prev_fast = fast.get(product, self.DEFAULT_FAIR[product])
            prev_slow = slow.get(product, self.DEFAULT_FAIR[product])

            new_fast = (
                (1 - self.FAST_ALPHA[product]) * prev_fast
                + self.FAST_ALPHA[product] * mid
            )

            new_slow = (
                (1 - self.SLOW_ALPHA[product]) * prev_slow
                + self.SLOW_ALPHA[product] * mid
            )

            fast[product] = new_fast
            slow[product] = new_slow

            trend = new_fast - new_slow

            imbalance = self.imbalance(depth)

            fair = (
                new_fast
                + self.TREND_WEIGHT[product] * trend
                - self.IMBALANCE_WEIGHT * imbalance
            )

            position = state.position.get(product, 0)

            orders = self.trade_logic(
                product,
                depth,
                fair,
                position,
            )

            if orders:
                result[product] = orders

        traderData = json.dumps({"fast": fast, "slow": slow})

        return result, 0, traderData


    def trade_logic(
        self,
        product,
        depth,
        fair,
        position,
    ):

        orders = []

        limit = self.LIMITS[product]

        buy_cap = limit - position
        sell_cap = limit + position

        best_bid, _ = self.best_bid(depth)
        best_ask, _ = self.best_ask(depth)

        edge = self.TAKE_EDGE[product]


        # CLEAN TAKER LOGIC (REMOVED BAD MOMENTUM TRIGGERS)

        if best_ask is not None:

            if best_ask <= fair - edge:

                qty = min(buy_cap, 12)

                if qty > 0:
                    orders.append(Order(product, best_ask, qty))
                    buy_cap -= qty


        if best_bid is not None:

            if best_bid >= fair + edge:

                qty = min(sell_cap, 12)

                if qty > 0:
                    orders.append(Order(product, best_bid, -qty))
                    sell_cap -= qty


        inv_ratio = position / limit

        spread = self.BASE_SPREAD

        if abs(inv_ratio) > 0.7:
            spread += 1

        skew = round(inv_ratio * 5)

        bid_quote = round(fair - spread - skew)
        ask_quote = round(fair + spread - skew)


        if best_bid:
            bid_quote = min(bid_quote, best_bid + 1)

        if best_ask:
            ask_quote = max(ask_quote, best_ask - 1)


        if bid_quote >= ask_quote:
            ask_quote = bid_quote + 1


        qsize = self.QUOTE_SIZE[product]


        if buy_cap > 0:
            orders.append(Order(product, bid_quote, min(qsize, buy_cap)))

        if sell_cap > 0:
            orders.append(Order(product, ask_quote, -min(qsize, sell_cap)))


        return orders


    def midprice(self, product, depth, fast):

        best_bid, bid_vol = self.best_bid(depth)
        best_ask, ask_vol = self.best_ask(depth)

        fallback = fast.get(product, self.DEFAULT_FAIR[product])

        if best_bid is None and best_ask is None:
            return fallback

        if best_bid is None:
            return best_ask

        if best_ask is None:
            return best_bid

        mid = (best_bid + best_ask) / 2

        micro = self.microprice(best_bid, best_ask, bid_vol, ask_vol)

        return 0.55 * mid + 0.45 * micro


    def imbalance(self, depth):

        best_bid, bid_vol = self.best_bid(depth)
        best_ask, ask_vol = self.best_ask(depth)

        if bid_vol is None or ask_vol is None:
            return 0

        ask_vol = abs(ask_vol)

        if bid_vol + ask_vol == 0:
            return 0

        return (bid_vol - ask_vol) / (bid_vol + ask_vol)


    def decode(self, raw):

        if not raw:
            return {}

        try:
            return json.loads(raw)
        except:
            return {}


    def best_bid(self, depth):

        if not depth.buy_orders:
            return None, None

        price = max(depth.buy_orders)

        return price, depth.buy_orders[price]


    def best_ask(self, depth):

        if not depth.sell_orders:
            return None, None

        price = min(depth.sell_orders)

        return price, depth.sell_orders[price]


    def microprice(
        self,
        best_bid,
        best_ask,
        bid_vol,
        ask_vol,
    ):

        bid_vol = max(0, bid_vol or 0)
        ask_vol = max(0, -(ask_vol or 0))

        if bid_vol + ask_vol == 0:
            return (best_bid + best_ask) / 2

        return (
            best_ask * bid_vol
            + best_bid * ask_vol
        ) / (bid_vol + ask_vol)