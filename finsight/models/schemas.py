"""Validated facts and analytical contracts. Ratios are fractions, monetary facts are USD."""

from datetime import UTC, date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Source(Model):
    id:str
    url:str
    label:str
    retrieved_at:datetime = Field(default_factory=lambda: datetime.now(UTC))
    filing_date:date | None = None
    form: str | None = None
    period:str | None = None
    tag: str | None = None
    unit: str | None = None
    accession : str | None = None


class Company(Model):
    ticker: str
    name: str
    cik: str
    industry: str | None = None
    fiscal_year_end: str | None = None

    @field_validator("ticker")
    @classmethod
    def valid_ticker(cls, value:str) -> str:
        import re

        value = value.strip().upper()
        if not re.fullmatch(r"[A-Z][A-Z0-9.-]{0,9}", value):
            raise ValueError("Enter a valid U.S. stock ticker, for example MSFT.")
        return value


class AnnualPeriod(Model):
    end: date
    start: date
    unit: Literal["USD"] = "USD"
    values: dict[str, float | None]
    sources: dict[str, Source] = Field(default_factory=dict)

    @model_validator(mode="after")
    def annual_only(self):
        if not 330 <= (self.end - self.start).days <= 365:
            raise ValueError(
                "Annual periods must be between 330 and 365 days long."
            )
        return self


class FinancialDataset(Model):
    company: Company
    periods: list[AnnualPeriod]
    warnings: list[str] = []
    demo: bool = False

    @model_validator(mode="after")
    def unique_ordered(self):
        ends = [p.end for p in self.periods]
        if ends != sorted(set(ends)):
            raise ValueError("Financial periods must be unique and chronological.")
        return self


class Price(Model):
    date: date
    close: float = Field(gt=0)
    volume: float | None = Field(default=None, ge=0)


class PriceHistory(Model):
    ticker: str
    prices: list[Price]
    source: Source
    adjusted: bool = True
    demo: bool = False

    @model_validator(mode="after")
    def ordered(self):
        dates = [p.date for p in self.prices]
        if dates != sorted(set(dates)):
            raise ValueError("Price history must be unique and chronological.")
        return self




