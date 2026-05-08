from datamodel import OrderDepth, TradingState, Order
from typing import Dict, List
import json


class Trader:

    POSITION_LIMITS = {
        "INTARIAN_PEPPER_ROOT": 80,
        "ASH_COATED_OSMIUM": 80,
    }

    OSMIUM_MAX_ORDER_SIZE = 12
    OSMIUM_SKEW_FACTOR = 0.6
    OSMIUM_EDGE_THRESHOLD = 4

    PEPPER_LAYER_1 = 1
    PEPPER_LAYER_2 = 3


    ########################################
    # MARKET ACCESS FEE STRATEGY
    ########################################

    def bid(self):
        return 15


    ########################################
    # MAIN LOOP
    ########################################

    def run(self, state: TradingState):

        result: Dict[str, List[Order]] = {}

        for product, order_depth in state.order_depths.items():

            position = state.position.get(product, 0)
            limit = self.POSITION_LIMITS[product]

            if product == "INTARIAN_PEPPER_ROOT":
                orders = self.trade_pepper(order_depth, position, limit)

            elif product == "ASH_COATED_OSMIUM":
                orders = self.trade_osmium(order_depth, position, limit)

            else:
                orders = []

            if orders:
                result[product] = orders

        return result, 0, json.dumps({})


    ########################################
    # MICROPRICE CALCULATION
    ########################################

    def microprice(self, best_bid, best_bid_volume,
                   best_ask, best_ask_volume):

        return (
            best_bid * best_ask_volume +
            best_ask * best_bid_volume
        ) / (best_bid_volume + best_ask_volume)


    ########################################
    # PEPPER ROOT STRATEGY
    ########################################

    def trade_pepper(self, order_depth, position, limit):

        orders = []

        if not order_depth.sell_orders:
            return orders

        best_ask = min(order_depth.sell_orders.keys())
        best_ask_volume = abs(order_depth.sell_orders[best_ask])

        buy_capacity = limit - position

        if buy_capacity <= 0:
            return orders


        ####################################
        # TAKE LIQUIDITY FIRST
        ####################################

        take_size = min(best_ask_volume, buy_capacity)

        orders.append(
            Order("INTARIAN_PEPPER_ROOT",
                  best_ask,
                  take_size)
        )

        buy_capacity -= take_size


        ####################################
        # LAYERED PASSIVE BIDS
        ####################################

        if buy_capacity > 0 and order_depth.buy_orders:

            best_bid = max(order_depth.buy_orders.keys())
            mid = (best_bid + best_ask) / 2

            layer1_price = round(mid - self.PEPPER_LAYER_1)
            layer2_price = round(mid - self.PEPPER_LAYER_2)

            layer_size = min(6, buy_capacity)

            orders.append(
                Order("INTARIAN_PEPPER_ROOT",
                      layer1_price,
                      layer_size)
            )

            buy_capacity -= layer_size


            if buy_capacity > 0:

                orders.append(
                    Order("INTARIAN_PEPPER_ROOT",
                          layer2_price,
                          min(6, buy_capacity))
                )

        return orders


    ########################################
    # OSMIUM STRATEGY
    ########################################

    def trade_osmium(self, order_depth, position, limit):

        orders = []

        if not order_depth.buy_orders or not order_depth.sell_orders:
            return orders


        ####################################
        # BEST LEVELS
        ####################################

        best_bid = max(order_depth.buy_orders.keys())
        best_ask = min(order_depth.sell_orders.keys())

        best_bid_volume = order_depth.buy_orders[best_bid]
        best_ask_volume = abs(order_depth.sell_orders[best_ask])


        ####################################
        # MICROPRICE FAIR VALUE
        ####################################

        fair_value = self.microprice(
            best_bid,
            best_bid_volume,
            best_ask,
            best_ask_volume
        )


        ####################################
        # ORDERBOOK IMBALANCE SIGNAL
        ####################################

        imbalance = best_bid_volume / (
            best_bid_volume + best_ask_volume
        )

        if imbalance > 0.6:
            fair_value += 2

        elif imbalance < 0.4:
            fair_value -= 2


        ####################################
        # INVENTORY SKEW
        ####################################

        skew = -position * self.OSMIUM_SKEW_FACTOR

        adjusted_fair = fair_value + skew


        ####################################
        # SPREAD-AWARE QUOTES
        ####################################

        spread = best_ask - best_bid

        if spread >= 6:

            bid_price = best_bid + 1
            ask_price = best_ask - 1

        else:

            bid_price = round(adjusted_fair - 4)
            ask_price = round(adjusted_fair + 4)


        ####################################
        # POSITION CAPACITY
        ####################################

        buy_capacity = limit - position
        sell_capacity = limit + position


        ####################################
        # PASSIVE MARKET MAKING
        ####################################

        if buy_capacity > 0:

            orders.append(
                Order("ASH_COATED_OSMIUM",
                      bid_price,
                      min(self.OSMIUM_MAX_ORDER_SIZE,
                          buy_capacity))
            )

        if sell_capacity > 0:

            orders.append(
                Order("ASH_COATED_OSMIUM",
                      ask_price,
                      -min(self.OSMIUM_MAX_ORDER_SIZE,
                           sell_capacity))
            )


        ####################################
        # AGGRESSIVE MISPRICING CAPTURE
        ####################################

        if best_ask < adjusted_fair - self.OSMIUM_EDGE_THRESHOLD:

            trade_size = min(
                best_ask_volume,
                self.OSMIUM_MAX_ORDER_SIZE,
                buy_capacity
            )

            if trade_size > 0:

                orders.append(
                    Order("ASH_COATED_OSMIUM",
                          best_ask,
                          trade_size)
                )


        if best_bid > adjusted_fair + self.OSMIUM_EDGE_THRESHOLD:

            trade_size = min(
                best_bid_volume,
                self.OSMIUM_MAX_ORDER_SIZE,
                sell_capacity
            )

            if trade_size > 0:

                orders.append(
                    Order("ASH_COATED_OSMIUM",
                          best_bid,
                          -trade_size)
                )


        return orders