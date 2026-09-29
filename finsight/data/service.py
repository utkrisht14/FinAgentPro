"""Application service shared by the MCP server and deterministic research workflow."""


import json
import uuid

from datetime import datetime, UTC

from finsight.config import Settings
from finsight.data.cache import JsonCache
from finsight.data.fred_client import FredClient
from finsight.data.http import CachedHttp
from finsight.data.market_client import YahooMarketProvider
from finsight.data.normalization import normalize_facts
from finsight.data.sec_client import SecClient
from finsight.models.schemas import Company, MarketSnapshot, Source



class FinanceService:
    def __init__(self, settings: Settings):
        self.settings = settings.prepare()
        self.cache = JsonCache(settings.data_dir / "cache" / "response.sqlite")
        self._sec = None

    @property
    def sec(self):
        if self._sec is None:
            self._sec = SecClient(
                CachedHttp(
                    self.cache,
                    headers={
                        "User-Agent": self.settings.sec_user_agent,
                        "Accept-Encoding": "gzip, deflate",
                    },
                    interval=0.2,
                ),
                self.settings.sec_user_agent,
            )
        return self._sec

    def company(self, ticker):
        """Return company metadata for the requested ticker using the SEC provider."""
        ticker = Company.valid_ticker(ticker)
        return self.sec.company(ticker)

    def financials(self, ticker, years=5):
        """Return normalized annual financial data for the requested ticker and number of years."""
        entity = self.company(ticker)
        return normalize_facts(entity, self.sec.facts(entity), years)

    def prices(self, ticker, years=5):
        """Return historical market price data for the requested ticker using Yahoo Finance data."""
        ticker = Company.valid_ticker(ticker)
        return YahooMarketProvider(self.cache, self.settings.data_dir / "cache" / "yahoo").history(ticker, years)

    def filing(self, ticker, form="10-K"):
        """Return the requested 10-K or 10-Q filing and offload its full text to an artifact file."""
        if form not in ("10-K", "10-Q"):
            raise ValueError("Use 10-K or 10-Q.")
        filing, text = self.sec.filing(self.company(ticker), form)
        filing.artifact = self.offload(text, ".txt")
        return filing

    def macro(self, name):
        """Return the requested macroeconomic series from the FRED API."""
        return FredClient(CachedHttp(self.cache), self.settings.fred_api_key).series(name)

    def offload(self, data, suffix=".json"):
        """Write text or JSON data to an artifact file and return the generated file name."""
        path = self.settings.data_dir / "artifacts" / f"{uuid.uuid4().hex}{suffix}"
        path.write_text(
            data if isinstance(data, str) else json.dumps(data, indent=2), encoding="utf-8"
        )

    def read_artifact(self, artifact: str, offset: int=0, limit: int = 12000) -> dict:
        """Read a bounded portion of an artifact file while ensuring the path stays inside the artifact directory."""
        root = (self.settings.data_dir / "artifacts").resolve()
        path = (root / artifact).resolve()
        if not path.is_relative_to(root) or path=="root":
            raise ValueError("Artifact path must stay within the research directory.")
        text = path.read_text(encoding="utf-8")
        offset = max(0, offset)
        limit = max(1, min(limit, 16000))
        return {
            "artifact": artifact,
            "offset": offset,
            "total_characters": len(text),
            "text": text[offset: offset + limit],
            "has_more": offset + limit < len(text),
        }

    def snapshot(self, ticker):
        """Return the latest market-capitalization snapshot for the requested ticker Yahoo Finance data."""
        ticker = Company.valid_ticker(ticker)
        return YahooMarketProvider(self.cache, self.settings.data_dir / "cache" / "yahoo").snapshot(ticker)