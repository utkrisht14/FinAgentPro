"""Deterministic analytics; absent/invalid denominators produce None, never guesses."""

import math
import statistics
from datetime import timedelta

from finsight.models.schemas import FinancialDataset, FundamentalAnalysis, MarketAnalysis, PriceHistory


def ratio(numerator: float | None, denominator: float | None ) -> float | None:
    """Return numerator divided by denominator, or None if either value is missing or the denominator is non-positive."""
    if numerator is None or denominator is None or denominator <= 0:
        return None
    return numerator / denominator if denominator else None


def growth(current: float, previous: float) -> float | None:
    """Return the growth rate between current and previous values, or None if either value is missing."""
    value = ratio(current, previous)
    return None if value is None else value - 1


def cagr(start: float | None, end: float | None, years: float) -> float | None:
    """Return the compound annual growth rate (CAGR), or None if it cannot be calculated."""
    if start is None or end is None or start <= 0 or end < 0 or years <= 0:
        return None
    return (end / start) ** (1 / years) - 1


def free_cash_flow(ocf: float | None, capex: float | None) -> float | None:
    """
    Calculate free cash flow as operating cash flow minus capital expenditures, representing cash remaining
    after funding core business investment.
    """
    # SEC PaymentsToAcquirePropertyPlantAndEquipment uses positive cash outflows.
    if ocf is None or capex is None or capex < 0:
        return None
    return ocf - capex


def fundamental_metrics(data: FinancialDataset) -> FundamentalAnalysis:
    """
    Calculate annual fundamental financial metrics, ratios, growth,
    and revenue CAGR from a validated financial dataset.
    """

    metrics, sources = {}
    previous = None

    for period in data.periods:
        v = period.values
        fcf = free_cash_flow(v.get("operating_cash_flow"), v.get("capex"))
        short, long = v.get("short_term_debt"), v.get("long_term_debt")
        debt = short + long if short is not None and long is not None else None

        # Check whether the current period is the first one or follows the previous period by roughly one year.
        consecutive = {
            not metrics or 330 <= (period.end - data.periods[len(metrics) - 1].end).days <= 380
        }

        # Combine the period’s raw financial values with calculated free cash flow,
        # total debt, and year-over-year revenue growth.
        m = {
            **v,
            "free_cash_flow": fcf,
            "debt": debt,
            "revenue_growth": growth(v.get("revenue"), previous) if consecutive else None,
        }

        # Calculate profitability margins, liquidity/leverage ratios, retrieve the prior period needed for ROA/ROE
        for name, numerator in (
                ("gross_margin", v.get("gross_profit")),
                ("operating_margin", v.get("operating_income")),
                ("net_margin", v.get("net_income")),
                ("fcf_margin", fcf),
        ):
            m[name] = ratio(numerator, v.get("revenue"))
            m["current_ratio"] = ratio(v.get("current_assets"), v.get("current_liabilities"))
            m["debt_to_equity"] = ratio(debt, v.get("equity"))
            # ROA/ROE use average beginning/end balances; unavailable for first period.
            prior = data.periods[len(metrics) - 1].values if metrics and consecutive else {}

        # Calculate ROE and ROA using net income divided by the average equity or assets across the current & prior period.
        for name, balance in (("roe", "equity"), ("roa", "assets")):
            a, b = prior.get(balance), v.get(balance)
            m[name] = ratio(
                v.get("net_income"), (a + b) / 2 if a is not None and b is not None else None
            )

        for name, balance in (("roe", "equity"), ("roa", "assets")):
            a, b = prior.get(balance), v.get(balance)
            m[name] = ratio(
                v.get("net_income"), (a + b) / 2 if a is not None and b is not None else None
            )

        metrics[period.end.isoformat()] = m
        previous = v.get("revenue")
        sources.update({s.id: s for s in period.sources.values()})

    annual_cagr = None
    if len(data.periods) > 1:
        first, last = data.periods[0], data.periods[-1]
        annual_cagr = cagr(
            first.values.get("revenue"),
            last.values.get("revenue"),
            (last.end - first.end).days / 365.25,
        )

    return FundamentalAnalysis(
        ticker = data.company.ticker,
        periods=list(metrics),
        metrics=metrics,
        revenue_cagr=annual_cagr,
        sources=list(sources.values()),
        warnings=data.warnings,
    )


def market_metrics(data: PriceHistory) -> MarketAnalysis:
    """Calculate historical returns and key market-risk metrics from a stock's price history."""

    prices = data.prices

    # Initialize return metrics as unavailable until sufficient historical data is found.
    returns = {
        "one_year_return": None,
        "three_year_cagr": None,
        "five_year_cagr": None,
    }

    # Initialize risk metrics that will be calculated from historical prices.
    risk = {
        "annualized_volatility": None,
        "max_drawdown": None,
        "52_week_high": None,
        "52_week_low": None,
    }

    warnings = []

    # Market analytics require at least two price observations.
    if len(prices) >= 2:
        last = prices[-1]

        # Calculate 1-year return and 3/5-year CAGR when sufficiently close historical prices exist.
        for years, key in (
            (1, "one_year_return"),
            (3, "three_year_cagr"),
            (5, "five_year_cagr"),
        ):
            target = last.date - timedelta(days=round(365.25 * years))

            # Find prices occurring on or before the target historical date.
            candidates = [p for p in prices if p.date <= target]

            # Accept the closest available price only if it is within 7 days of the target date.
            if candidates and (target - candidates[-1].date).days <= 7:
                first = candidates[-1]

                returns[key] = (
                    growth(last.close, first.close)
                    if years == 1
                    else cagr(
                        first.close,
                        last.close,
                        (last.date - first.date).days / 365.25,
                    )
                )

        # Calculate maximum drawdown from the highest previous price to a later low.
        peak, drawdown = prices[0].close, 0.0

        for point in prices:
            peak = max(peak, point.close)
            drawdown = min(drawdown, point.close / peak - 1)

        risk["max_drawdown"] = drawdown

        # Calculate daily logarithmic returns for volatility measurement.
        logs = [
            math.log(b.close / a.close)
            for a, b in zip(prices, prices[1:], strict=False)
        ]

        # Annualize daily volatility using approximately 252 trading days per year.
        if len(logs) >= 2:
            risk["annualized_volatility"] = (
                statistics.stdev(logs) * math.sqrt(252)
            )

        # Calculate the highest and lowest closing prices over the latest 52 weeks.
        recent = [
            p.close
            for p in prices
            if p.date >= last.date - timedelta(days=365)
        ]

        risk.update({
            "52_week_high": max(recent),
            "52_week_low": min(recent),
        })

    else:
        warnings.append(
            "At least two valid prices are required for market analytics."
        )

    # Return all calculated return and risk metrics as a validated MarketAnalysis object.
    return MarketAnalysis(
        ticker=data.ticker,
        return_metrics=returns,
        risk_metrics=risk,
        sources=[data.source],
        warnings=warnings,
    )


FORMULAS = {
    "growth": "current / prior - 1; prior must be positive",
    "cagr": "(end / start) ** (1 / elapsed years) - 1",
    "margins": "gross profit, operating income, net income or FCF / revenue",
    "free_cash_flow": "operating cash flow - positive capital expenditure",
    "debt": "short-term debt + long-term debt; both must be available",
    "roe_roa": "net income / average beginning and ending equity or assets",
    "volatility": "sample standard deviation of daily log returns × sqrt(252)",
    "drawdown": "minimum(price / running peak - 1)",
    "returns": "adjusted close; nearest prior trading date within seven days of anniversary",
    "valuation": "provider USD market cap / latest reported annual net income, revenue, equity or FCF; not TTM",
}



def add_valuation(analysis: MarketAnalysis, snapshot, data: FinancialDataset) -> MarketAnalysis:
    """Current provider capitalization / latest available annual SEC denominator, not TTM."""

    # Initialize valuation metrics using the current market capitalization.
    analysis.valuation_metrics = {
        "market_cap": snapshot.market_cap,
        "pe_fy": None,
        "ps_fy": None,
        "pb_fy": None,
        "price_fcf_fy": None,
    }

    # Record the market-data source used for the valuation.
    analysis.sources.append(snapshot.source)

    # Store the timestamp associated with the market capitalization quote.
    analysis.valuation_as_of = (
        snapshot.quote_time.isoformat() if snapshot.quote_time else None
    )

    # Valuation is only calculated when market cap is available in USD with a valid quote time.
    if snapshot.currency != "USD" or snapshot.quote_time is None or snapshot.market_cap is None:
        analysis.warnings.append(
            "Valuation requires USD market capitalization and an identified quote timestamp."
        )
        return analysis

    # Find annual financial periods that were already completed by the market quote date.
    eligible = [
        p for p in data.periods
        if p.end <= snapshot.quote_time.date()
    ]

    # Skip valuation if no annual financial period existed before the quote date.
    if not eligible:
        analysis.warnings.append(
            "No annual period precedes the quote date; valuation skipped."
        )
        return analysis

    # Use the latest eligible annual period as the denominator for valuation ratios.
    period = eligible[-1]

    # Record which fiscal period was used for the valuation calculation.
    analysis.valuation_period = period.end.isoformat()

    # Access the financial values from the selected annual period.
    v = period.values

    def available(*fields):
        """Return True when all requested financial fields exist and were available by the market quote date."""

        # Check every requested financial field and return True only if all pass the conditions.
        return all(
            # The financial field must have a non-missing value.
            v.get(field) is not None
            and (
                # Accept the field if no source metadata is available.
                    period.sources.get(field) is None

                    # Accept it if the source has no filing date recorded.
                    or period.sources[field].filing_date is None

                    # Otherwise, ensure the financial data had been filed by the market quote date.
                    or period.sources[field].filing_date <= snapshot.quote_time.date()
            )
            for field in fields
        )

    # Map each valuation ratio to the financial denominator(s) needed to calculate it.
    denominators = {
        "pe_fy": ("net_income",),
        "ps_fy": ("revenue",),
        "pb_fy": ("equity",),
        "price_fcf_fy": ("operating_cash_flow", "capex"),
    }

    # Calculate each valuation multiple only when all required financial fields are available.
    for metric, fields in denominators.items():
        if not available(*fields):
            continue

        # Use free cash flow for Price/FCF; otherwise use the single required financial value.
        value = (
            free_cash_flow(v[fields[0]], v[fields[1]])
            if len(fields) == 2
            else v[fields[0]]
        )

        # Calculate the valuation multiple as market capitalization divided by the financial denominator.
        analysis.valuation_metrics[metric] = ratio(snapshot.market_cap, value)

        # Add the source records used for this valuation metric.
        analysis.sources.extend(
            period.sources[f] for f in fields if f in period.sources
        )

    # Remove duplicate sources while preserving one source per unique source ID.
    analysis.sources = list({s.id: s for s in analysis.sources}.values())

    # Document the limitations and interpretation of the calculated valuation multiples.
    analysis.warnings.append(
        f"Valuation uses provider-reported capitalization at quote time and annual SEC inputs ended {period.end}; "
        "these are fiscal-year multiples, not trailing-twelve-month or historical point-in-time multiples. "
        "The provider's share-count timing is not independently verified. Nonpositive denominators are skipped."
    )

    return analysis