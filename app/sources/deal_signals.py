from app.sources.nse_deals import Deal

# Direction convention, consistent with the rest of the project: a higher number means more
# risk. Large institutional or promoter selling is the signal of interest here, so the
# derived measures are shaped so that more selling reads as higher.
#
#   deal_sell_share_pct    HIGHER is worse: share of traded volume that was sold
#   deal_net_quantity      LOWER is worse: buy minus sell, so negative means net selling
#   deal_count             informational: how much large-lot activity there was at all
HIGHER_IS_RISKIER = ("deal_sell_share_pct",)

UNITS = {
    "deal_count": "count",
    "deal_buy_quantity": "shares",
    "deal_sell_quantity": "shares",
    "deal_net_quantity": "shares",
    "deal_sell_share_pct": "percent",
    "deal_distinct_clients": "count",
}


def extract_deal_kpis(deals: list[Deal]) -> dict[str, float]:
    """Turn one company's large-lot deals into KPI values.

    All deals passed in are expected to belong to a single symbol and a single publication
    day, because that is how NSE publishes them.
    """
    if not deals:
        return {}

    buy_quantity = sum(deal.quantity for deal in deals if deal.side == "BUY")
    sell_quantity = sum(deal.quantity for deal in deals if deal.side == "SELL")
    total = buy_quantity + sell_quantity

    kpis: dict[str, float] = {
        "deal_count": float(len(deals)),
        "deal_buy_quantity": round(buy_quantity, 2),
        "deal_sell_quantity": round(sell_quantity, 2),
        "deal_net_quantity": round(buy_quantity - sell_quantity, 2),
        "deal_distinct_clients": float(len({deal.client_name for deal in deals if deal.client_name})),
    }

    if total > 0:
        kpis["deal_sell_share_pct"] = round(sell_quantity / total * 100, 2)

    return kpis


def deal_summary(deals: list[Deal]) -> dict:
    """Normalized record stored alongside the raw rows."""
    if not deals:
        return {}

    return {
        "symbol": deals[0].symbol,
        "security_name": deals[0].security_name,
        "deal_date": deals[0].deal_date.isoformat(),
        "deal_types": sorted({deal.deal_type for deal in deals}),
        "deal_count": len(deals),
        "clients": sorted({deal.client_name for deal in deals if deal.client_name}),
        "kpis": extract_deal_kpis(deals),
    }
