from datetime import UTC, date, datetime, timedelta
from typing import Protocol

from finsight.data.cache import JsonCache
from finsight.data.http import DataUnavailable
from finsight.models.schemas import MarketSnapshot, Price, PriceHistory, Source

import yfinance as yf


class MarketProvider(Protocol):
    """
    Protocol defining the required interface for market data providers.

    Any market data provider must implement methods for retrieving historical
    price data and the latest market snapshot for a stock ticker.
    """

    def history(self, ticker: str, years: int = 5) -> PriceHistory: ...

    def snapshot(self, ticker: str) -> MarketSnapshot: ...



class YahooMarketProvider:
    """
    Replaceable, cached adapter. Yahoo availability/licensing is provider controlled.
    """

    def __init__(self, cache: JsonCache, cache_dir=None):
        self.cache = cache
        if cache_dir is not None:
            yf.set_tz_cache_location(cache_dir)

    def history(self, ticker: str, years: int=5) -> PriceHistory:
        """ Fetch and cache adjusted historical Yahoo Finance price data for the requested ticker and time period. """
        today = date.today()
        key = f"yahoo: {ticker}: {years}: {today}: adjusted"

        def fetch():
            try:
                frame = yf.Ticker(ticker).history(
                    start = today - timedelta(days=round(years * 365.25) + 10),
                    end = today,
                    auto_adjust=True,
                )
                if frame.empty:
                    raise DataUnavailable(f"No market prices available for {ticker}.")
                points = [
                    Price(date=index.date(), close=float(row["close"]), volume=float(row["Volume"]))
                    for index, row in frame.iterrows()
                    if row["Close"] > 0
                ]
                return PriceHistory(
                    ticker=ticker,
                    prices=points,
                    source=Source(
                        id=f"yahoo:{ticker}:{today}",
                        url=f"https://finance.yahoo.com/quote/{ticker}/history/",
                        label="Yahoo finance adjusted daily close.",
                        unit="USD",
                        period=f"{points[0].date}/{points[-1].date}"
                    )
                )
            except Exception as exc:
                raise DataUnavailable(
                    f"Market data unavailable for {ticker}; retry later."
                ) from exc

        return PriceHistory.model_validate(self.cache.remember(key, fetch, ttl=3600))


    def snapshot(self, ticker: str) -> MarketSnapshot:
        """Fetch and cache the latest Yahoo Finance market capitalization snapshot for the requested ticker."""

        def fetch():
            import math

            try:
                info = yf.Ticker(ticker).info
                cap = info.get("marketCap")

                if cap is None or not math.isfinite(float(cap)) or cap <= 0:
                    cap = None

                timestamp = info.get("regularMarketTime")
                quote_time = datetime.fromtimestamp(timestamp, tz=UTC) if timestamp else None
                return MarketSnapshot(
                    ticker=ticker,
                    market_cap=cap,
                    currency=info.get("currency", "UNKNOWN"),
                    quote_time=quote_time,
                    source=Source(
                        id=f"yahoo:{ticker}:snapshot:{date.today()}",
                        url=f"https://finance.yahoo.com/quote/{ticker}/key-statistics/",
                        label="Yahoo Finance provider-reported market capitalization.",
                        period=quote_time.isoformat() if quote_time else None,
                        unit=info.get("currency", "UNKNOWN"),
                        tag="marketCap"
                    ),
                ).model_dump(mode="json")
            except Exception as exc:
                raise DataUnavailable(
                    f"Market data unavailable for {ticker}; retry later."
                ) from exc

        return MarketSnapshot.model_validate(self.cache.remember(f"quote: {ticker}", fetch, ttl=900))