from datetime import UTC, date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class Model(BaseModel):
    """Base class for all validated financial data structures.

    Subclasses declare their own constructor arguments. Unexpected fields and
    non-finite numeric values (NaN and infinity) are rejected by configuration.
    Inherits Pydantic helpers such as model_validate and model_dump.
    """

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Source(Model):
    """Record where a fact came from and how to cite it.

    Args:
        id: Identifier used by findings to reference this source.
        url: Source location; stored as a string without URL validation.
        label: Human-readable source description.
        retrieved_at: Retrieval timestamp; defaults to the current UTC time.
        filing_date: Publication/filing date, when known; defaults to None.
        form: Filing form, such as 10-K or 10-Q; defaults to None.
        period: Reporting period or date-range label; defaults to None.
        tag: Original financial fact/XBRL tag; defaults to None.
        unit: Fact unit, such as USD or USD/shares; defaults to None.
        accession: SEC filing accession number; defaults to None.
    """

    id: str
    url: str
    label: str
    retrieved_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    filing_date: date | None = None
    form: str | None = None
    period: str | None = None
    tag: str | None = None
    unit: str | None = None
    accession: str | None = None


class Company(Model):
    """Identify a company for research and SEC retrieval.

    Args:
        ticker: Stock symbol, normalized and checked by valid_ticker.
        name: Company display name.
        cik: SEC Central Index Key, kept as a string to retain leading zeros.
        industry: Industry description, when available; defaults to None.
        fiscal_year_end: Fiscal year-end label, typically MMDD; defaults to None.
    """

    ticker: str
    name: str
    cik: str
    industry: str | None = None
    fiscal_year_end: str | None = None

    @field_validator("ticker")
    @classmethod
    def valid_ticker(cls, value: str) -> str:
        """Normalize ticker syntax without checking whether the stock exists.

        Args:
            value: Symbol to strip of surrounding whitespace and uppercase.

        Returns:
            A 1–10 character symbol beginning with a letter, followed by
            letters, digits, periods, or hyphens.

        Raises:
            ValueError: If the normalized symbol does not match that format.
        """
        import re

        value = value.strip().upper()
        if not re.fullmatch(r"[A-Z][A-Z0-9.-]{0,9}", value):
            raise ValueError("Enter a valid U.S. stock ticker, for example MSFT.")
        return value


class AnnualPeriod(Model):
    """Store normalized facts and their evidence for one annual period.

    Args:
        end: Fiscal period end date.
        start: Fiscal period start date; must be 330–380 days before end.
        unit: Currency of monetary totals; only USD is accepted (the default).
        values: Metric names mapped to numeric facts or None for missing facts.
        sources: Metric names mapped to their source metadata; defaults to empty.
            Per-share facts use the unit recorded in their source.
    """

    end: date
    start: date
    unit: Literal["USD"] = "USD"
    values: dict[str, float | None]
    sources: dict[str, Source] = Field(default_factory=dict)

    @model_validator(mode="after")
    def annual_only(self):
        """Validate the date span using this instance's start and end fields.

        No additional arguments are required; Pydantic calls this after parsing.

        Returns:
            This AnnualPeriod instance when the date span is valid.

        Raises:
            ValueError: If the end-minus-start span is outside 330–380 days.
        """
        if not 330 <= (self.end - self.start).days <= 380:
            raise ValueError("Annual periods must span 330–380 days; quarters are not annual data.")
        return self


class FinancialDataset(Model):
    """Group a company's annual financial periods into a validated dataset.

    Args:
        company: Company represented by the financial facts.
        periods: Annual periods ordered by unique ascending end dates.
        warnings: Data quality or coverage notes; defaults to an empty list.
        demo: Whether the dataset is synthetic; defaults to False.
    """

    company: Company
    periods: list[AnnualPeriod]
    warnings: list[str] = Field(default_factory=list)
    demo: bool = False

    @model_validator(mode="after")
    def unique_ordered(self):
        """Check period order and uniqueness without sorting or altering data.

        No additional arguments are required; reads this instance's periods.

        Returns:
            This FinancialDataset instance. An empty period list is allowed.

        Raises:
            ValueError: If period end dates repeat or are out of order.
        """
        ends = [p.end for p in self.periods]
        if ends != sorted(set(ends)):
            raise ValueError("Financial periods must be unique and chronological.")
        return self


class Price(Model):
    """Represent one daily market price observation.

    Args:
        date: Observation/trading date.
        close: Closing price; must be strictly positive.
        volume: Nonnegative traded volume, or None if unavailable (the default).
    """

    date: date
    close: float = Field(gt=0)
    volume: float | None = Field(default=None, ge=0)


class PriceHistory(Model):
    """Store an ordered price series with provenance.

    Args:
        ticker: Company stock symbol.
        prices: Observations with unique dates in ascending order.
        source: Provenance for the price series, including units when available.
        adjusted: Whether prices are adjusted; defaults to True.
        demo: Whether the series is synthetic; defaults to False.
    """

    ticker: str
    prices: list[Price]
    source: Source
    adjusted: bool = True
    demo: bool = False

    @model_validator(mode="after")
    def ordered(self):
        """Check observation dates without sorting or removing duplicates.

        No additional arguments are required; reads this instance's prices.

        Returns:
            This PriceHistory instance. An empty price list is allowed.

        Raises:
            ValueError: If observation dates repeat or are out of order.
        """
        dates = [p.date for p in self.prices]
        if dates != sorted(set(dates)):
            raise ValueError("Price dates must be unique and chronological.")
        return self


class Filing(Model):
    """Describe a filing and the text extracted for analysis.

    Args:
        ticker: Company stock symbol.
        form: Supported SEC form: 10-K or 10-Q.
        filed: Filing date.
        accession: SEC accession identifier.
        url: Source filing URL.
        sections: Section names mapped to extracted text; defaults to empty.
        artifact: Reference to offloaded filing text; defaults to None.
        warnings: Extraction or coverage limitations; defaults to an empty list.
        demo: Whether the filing is synthetic; defaults to False.
    """

    ticker: str
    form: Literal["10-K", "10-Q"]
    filed: date
    accession: str
    url: str
    sections: dict[str, str] = Field(default_factory=dict)
    artifact: str | None = None
    warnings: list[str] = Field(default_factory=list)
    demo: bool = False


class Finding(Model):
    """Represent a cited statement while distinguishing facts from interpretation.

    Args:
        text: Statement to present in the research output.
        kind: One of source_fact, calculated_metric, or interpretation.
        source_ids: At least one supporting source ID. Matching these IDs to
            actual report sources is checked by callers, not by this schema.
    """

    text: str
    kind: Literal["source_fact", "calculated_metric", "interpretation"]
    source_ids: list[str] = Field(min_length=1)


class FundamentalAnalysis(Model):
    """Collect deterministic financial metrics and cited fundamental findings.

    Args:
        ticker: Company stock symbol.
        periods: Reporting-period labels used in the analysis.
        metrics: Period labels mapped to metric-name/value dictionaries.
        revenue_cagr: Annualized revenue growth as a fraction, or None (default).
        strengths: Cited favorable findings; defaults to an empty list.
        concerns: Cited concerns; defaults to an empty list.
        sources: Supporting source records.
        warnings: Calculation and data limitations; defaults to an empty list.
    """

    ticker: str
    periods: list[str]
    metrics: dict[str, dict[str, float | None]]
    revenue_cagr: float | None = None
    strengths: list[Finding] = Field(default_factory=list)
    concerns: list[Finding] = Field(default_factory=list)
    sources: list[Source]
    warnings: list[str] = Field(default_factory=list)


class MarketAnalysis(Model):
    """Collect market performance, risk, and fiscal-year valuation results.

    Args:
        ticker: Company stock symbol.
        return_metrics: Return metric names mapped to values or None.
        risk_metrics: Risk metric names mapped to values or None.
        valuation_metrics: Valuation names mapped to multiples or None;
            defaults to an empty dictionary.
        valuation_period: Financial period used for valuation; defaults to None.
        valuation_as_of: Market observation date/time label; defaults to None.
        observations: Cited market findings; defaults to an empty list.
        sources: Supporting source records.
        warnings: Data and calculation limitations; defaults to an empty list.
    """

    ticker: str
    return_metrics: dict[str, float | None]
    risk_metrics: dict[str, float | None]
    valuation_metrics: dict[str, float | None] = Field(default_factory=dict)
    valuation_period: str | None = None
    valuation_as_of: str | None = None
    observations: list[Finding] = Field(default_factory=list)
    sources: list[Source]
    warnings: list[str] = Field(default_factory=list)


class RiskAnalysis(Model):
    """Organize cited risks and management commentary from a filing.

    Args:
        ticker: Company stock symbol.
        filing_type: Filing form label used for this analysis.
        filing_date: Filing date represented as a string.
        major_risks: Cited risk findings; defaults to an empty list.
        management_commentary: Cited management statements or interpretations;
            defaults to an empty list.
        liquidity_observations: Cited funding/liquidity findings; defaults to empty.
        legal_or_regulatory_points: Cited legal/regulatory findings; defaults to empty.
        source_sections: Names of filing sections used in the analysis.
        sources: Supporting source records.
        warnings: Coverage and interpretation limitations; defaults to empty.
    """

    ticker: str
    filing_type: str
    filing_date: str
    major_risks: list[Finding] = Field(default_factory=list)
    management_commentary: list[Finding] = Field(default_factory=list)
    liquidity_observations: list[Finding] = Field(default_factory=list)
    legal_or_regulatory_points: list[Finding] = Field(default_factory=list)
    source_sections: list[str]
    sources: list[Source]
    warnings: list[str] = Field(default_factory=list)


class Narrative(Model):
    """Represent a summary with cited findings and explicit limitations.

    Args:
        executive_summary: Human-readable research summary.
        findings: Supporting cited statements; defaults to an empty list.
        limitations: Missing evidence and other qualifications; defaults to empty.
    """

    executive_summary: str
    findings: list[Finding] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)


class ResearchRequest(Model):
    """Specify which company, history, and research topics to investigate.

    Args:
        ticker: Stock symbol, normalized using Company.valid_ticker.
        years: Requested history length, from 3 through 5 years; defaults to 5.
        focus: Nonempty list of fundamentals, market, risk, or macro topics.
            Defaults to fundamentals, market, and risk.
        question: User's research question; defaults to a request for long-term
            investment analysis.
    """

    ticker: str
    years: int = Field(default=5, ge=3, le=5)
    focus: list[Literal["fundamentals", "market", "risk", "macro"]] = Field(
        default_factory=lambda: ["fundamentals", "market", "risk"], min_length=1
    )
    question: str = "Analyse this company for long-term investment."

    @field_validator("ticker")
    @classmethod
    def normalize_ticker(cls, value):
        """Apply the shared company ticker validation to a research request.

        Args:
            value: Stock symbol supplied to the request.

        Returns:
            The stripped, uppercase ticker.

        Raises:
            ValueError: If the ticker has invalid syntax.
        """
        return Company.valid_ticker(value)


class ResearchReport(Model):
    """Combine a research run's request, evidence, analysis, and execution details.

    Args:
        run_id: UUID string identifying the run; normalized by valid_run_id.
        request: Original validated research request.
        company: Resolved company identity.
        created_at: Report timestamp; defaults to the current UTC time.
        demo: Whether this report uses synthetic demo data (required).
        executive_summary: Human-readable overview of the research.
        fundamental: Fundamental analysis, or None if absent (the default).
        market: Market analysis, or None if absent (the default).
        risk: Filing risk analysis, or None if absent (the default).
        findings: Combined cited findings; defaults to an empty list.
        sources: Source records supporting the report; defaults to an empty list.
        limitations: Missing evidence and other qualifications; defaults to empty.
        artifacts: Artifact labels mapped to stored file references; defaults to empty.
        activity: Execution event dictionaries for the activity view; defaults to empty.
        plan: Research plan steps; defaults to an empty list.
        macro: Optional macroeconomic research results; defaults to an empty dictionary.
    """

    run_id: str

    @field_validator("run_id")
    @classmethod
    def valid_run_id(cls, value):
        """Parse a run ID as a UUID and return its canonical string form.

        Args:
            value: UUID string supplied for the research run.

        Returns:
            Lowercase, hyphenated UUID string suitable for identifying the run.

        Raises:
            ValueError: If the string cannot be parsed as a UUID.
        """
        import uuid

        return str(uuid.UUID(value))

    request: ResearchRequest
    company: Company
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    demo: bool
    executive_summary: str
    fundamental: FundamentalAnalysis | None = None
    market: MarketAnalysis | None = None
    risk: RiskAnalysis | None = None
    findings: list[Finding] = Field(default_factory=list)
    sources: list[Source] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    artifacts: dict[str, str] = Field(default_factory=dict)
    activity: list[dict] = Field(default_factory=list)
    plan: list[str] = Field(default_factory=list)
    macro: dict = Field(default_factory=dict)


class MarketSnapshot(Model):
    """Capture market capitalization and its observation metadata for valuation.

    Args:
        ticker: Company stock symbol.
        market_cap: Strictly positive market capitalization, or None (the default).
        currency: Currency code reported by the market provider.
        quote_time: Provider observation timestamp, if known; defaults to None.
        source: Provenance for the market snapshot.
        demo: Whether the snapshot is synthetic; defaults to False.
    """

    ticker: str
    market_cap: float | None = Field(default=None, gt=0)
    currency: str
    quote_time: datetime | None = None
    source: Source
    demo: bool = False
