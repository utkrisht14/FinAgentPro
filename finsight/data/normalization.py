""""
This file converts raw SEC financial data into consistent annual records, aligning reporting periods, metric names,
and units so the program can calculate and compare financial performance reliably.
"""
import math
from datetime import date
from dataclasses import dataclass

from finsight.data.http import DataUnavailable
from finsight.models.schemas import AnnualPeriod, Company, FinancialDataset, Source


TAGS =  {
    "eps_diluted": ["EarningsPerShareDiluted"],
    "revenue": [
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "Revenues",
        "SalesRevenueNet",
        "RevenueFromContractWithCustomerIncludingAssessedTax",
        "RevenuesNetOfInterestExpense"
    ],
    "cost_of_revenue": ["CostOfRevenue", "CostOfGoodsAndServicesSold"],
    "gross_profit": ["GrossProfit"],
    "operating_income": ["OperatingIncomeLoss"],
    "net_income": ["NetIncomeLoss", "ProfitLoss"],
    "cash": ["CashAndCashEquivalentsAtCarryingValue"],
    "current_assets": ["AssetsCurrent"],
    "assets": ["Assets"],
    "current_liabilities": ["LiabilitiesCurrent"],
    "liabilities": ["Liabilities"],
    "equity": ["StockholdersEquity"],
    "short_term_debt": ["LongTermDebtCurrent"],
    "long_term_debt": ["LongTermDebtNoncurrent"],
    "operating_cash_flow": ["NetCashProvidedByUsedInOperatingActivities"],
    "capex": ["PaymentsToAcquirePropertyPlantAndEquipment"],
    "investing_cash_flow": ["NetCashProvidedByUsedInInvestingActivities"],
    "financing_cash_flow": ["NetCashProvidedByUsedInFinancingActivities"],
    "dividends": ["PaymentsOfDividends", "PaymentsOfDividendsCommonStock"],
    "share_repurchases": ["PaymentsForRepurchaseOfCommonStock"],
}

INSTANT = {
    "cash",
    "current_assets",
    "assets",
    "current_liabilities",
    "liabilities",
    "equity",
    "short_term_debt",
    "long_term_debt",
}

@dataclass(frozen=True)
class _FactCandidate:
    """
    Internal parsed fact, retaining its original item for source metadata.

    Attributes:
        item: Original SEC fact containing filing and accession metadata.
        end: Observation or reporting-period end date.
        start: Duration start date, or None for an instant fact.
        rank: Tag preference index; a lower index wins same-filing-date ties.
        tag: Original US-GAAP tag name.
        value: Finite numeric fact value.
    """

    item: dict
    end: date
    start: date | None
    rank: int
    tag: str
    value: float


def _unit_for_field(field: str) -> str:
    """Return the SEC unit key for a normalized metric name.

        Args:
            field: Metric key from TAGS.

        Returns:
            USD/shares for diluted EPS; USD for all other supported metrics.
        """
    return "USD/shares" if field == "eps_diluted" else "USD"


def _parse_candidate(field: str, tag: str, rank: int, item: dict) -> _FactCandidate:
    """
    Parse a fact and exclude invalid or unsupported observations.

    Args:
        field: Normalized metric name; INSTANT identifies balance-sheet fields.
        tag: Original US-GAAP tag name.
        rank: Preference index of the tag within TAGS[field].
        item: Raw SEC fact from the appropriate unit bucket.

    Returns:
        A finite fact from a 10-K or 10-K/A, or None for malformed or rejected
        input. Duration facts must span 330–380 days; instant facts need no start.
    """

    try:
        end = date.fromisoformat(item["end"])
        start = date.isoformat(item["start"]) if "start" in item else None
        value = float(item["val"])
        if not math.isfinite(value) or item.get("form") not in ("10-K", "10-K/A"):
            return None

        # Filing FY labels can describe a later year than a comparative fact.
        # Use actual dates to distinguish annual facts from quarterly facts.

        if field not in INSTANT and (start is None or not 330 <= (end - start).days <= 380):
            return None
        return _FactCandidate(item, end, start, rank, tag, value)
    except (ValueError, KeyError, TypeError):
        return None


def _collect_candidates(facts: dict) -> dict[str, list[_FactCandidate]]:
    """
    Gather supported facts in metric and tag-priority order.

    Args:
        facts: US-GAAP namespace from the SEC company-facts payload.

    Returns:
        Each supported metric mapped to its valid candidates, including empty
        lists for missing metrics. Other currencies and unit buckets are ignored.
    """

    candidates = {}
    for field, tags in TAGS.items():
        rows = []
        for rank, tag in enumerate(tags):
            items = facts.get(tag, {}).get("units", {}).get(_unit_for_field(field), [])
            for item in items:
                candidate = _parse_candidate(field, tag, rank, item)
                if candidate is not None:
                    rows.append(candidate)
        candidates[field] = rows
    return candidates


def _select_annual_anchors(candidates: dict[str, list[_FactCandidate]], years: int,
                           ) -> tuple[str, list[tuple[date, date]]]:
    """
    Choose the metric and annual date ranges that define the dataset.

    Args:
        candidates: Supported metric candidates from _collect_candidates.
        years: Requested number of most recent annual end dates.

    Returns:
        The anchor metric name and chronological (start, end) pairs. Revenue
        takes priority, followed by net income and operating cash flow. For a
        shared end date, the earliest start selects the longest valid duration.

    Raises:
        DataUnavailable: If no supported annual USD duration facts exist.
    """


    # Choose the first field with available candidates, prioritizing revenue, net income, then operating cash flow;
    # default to revenue if none exist.
    anchor_field = next(
        (field for field in ("revenue", "net_income", "operating_cash_flow") if candidates[field]),
        "revenue",
    )

    # Only duration fields can anchor a year; instant balance-sheet facts cannot.
    anchors = {(row.end, row.start) for row in candidates["anchor_field"] if row.start is not None}

    # Sort unique period end dates and keep the most recent requested number of years.
    ends = sorted({end for end, _ in anchors})[-years:]

    if not ends:
        raise DataUnavailable(f"No supported annual USD duration facts. Industry-specific tags may be needed.")

    # Return Anchor Field & Pair each selected end date with its earliest start date to choose the longest annual period.
    return anchor_field, [(min(start for e, start in anchors if e == end), end) for end in ends]


def _select_period_candidate(field: str, rows: list[_FactCandidate], start: date, end: date):
    """
    Select the preferred fact matching one annual period.

    Args:
        field: Normalized metric name.
        rows: Valid candidates for this metric, in tag-priority/input order.
        start: Selected annual start date.
        end: Selected annual end date.

    Returns:
        The latest-filed eligible candidate, using tag priority to break ties,
        or None if missing. Duration facts must match both dates; instant facts
        match only the end date. Exact ties retain the first eligible candidate.
    """

    # Keep facts matching the period end date, requiring the start date to match only for non-instant fields.
    eligible = [row for row in rows if row.end == end and (field in INSTANT or row.start == start)]

    # Later filings can restate prior-year comparatives, even under another tag.
    return max(eligible, key=lambda row: (row.item.get("filed", ""), -row.rank), default=None)


def _build_source(
    company: Company, field: str, candidate: _FactCandidate, start: date, end: date
    ) -> Source:
    """
    Build provenance for the chosen metric observation.

    Args:
        company: Company identity, including ticker and SEC CIK.
        field: Normalized metric name used in the source ID and label.
        candidate: Selected fact carrying its tag and filing metadata.
        start: Annual period start used in the source's reporting-period label.
        end: Annual period end used in the source ID and reporting-period label.

    Returns:
        A source with its accession-based SEC URL, filing metadata, tag, and unit.
    """
    item = candidate.item
    accn = item.get("accn", "")
    url = (
        f"https://www.sec.gov/Archives/edgar/data/{int(company.cik)}/"
        f"{accn.replace('-', '')}/{accn}-index.html"
    )
    return Source(
        id=f"sec:{company.ticker}:{end}:{field}",
        url=url,
        label=f"{field} · year ended {end}",
        filing_date=item.get("filed"),
        form=item.get("form"),
        period=f"{start}/{end}",
        tag=candidate.tag,
        unit=_unit_for_field(field),
        accession=accn,
    )


def _build_period(
        company: Company,  candidates: dict[str, list[_FactCandidate]], start: date, end: date
    ) -> AnnualPeriod:
    """
    Assemble normalized values and sources for a single annual period.

    Args:
        company: Company whose financials are being normalized.
        candidates: Supported metric candidates from _collect_candidates.
        start: Selected annual period start.
        end: Selected annual period end.

    Returns:
        A validated period containing every supported metric. Missing values
        remain None and have no source entry; values are never estimated.
    """
    values, sources = {}, {}
    for field, rows in candidates.items():
        candidate = _select_period_candidate(field, rows, start, end)
        values[field] = candidate.value if candidate is not None else None
        if candidate is not None:
            sources[field] = _build_source(company, field, candidate, start, end)
    return AnnualPeriod(start=start, end=end, values=values, sources=sources)


def _collect_warnings(
        company: Company, anchor_field: str, periods: list[AnnualPeriod], years: int
    ) -> list[str]:
    """
    Describe coverage and interpretation limitations in a stable order.

    Args:
        company: Company metadata used to identify bank-specific limitations.
        anchor_field: Metric selected to establish the annual reporting periods.
        periods: Normalized annual periods in chronological order.
        years: Number of annual periods originally requested.

    Returns:
        Warnings for missing revenue, bank accounting, missing metric values,
        annual gaps, debt coverage, and insufficient history, as applicable.
        The debt-coverage warning is always included.
    """
    warnings = []

    # Warn when revenue is unavailable and another metric was used to define annual periods.
    if anchor_field != "revenue":
        warnings.append(f"Revenue is unavailable; other annual fields are retained without estimating revenue.")

    # Warn that banks require different interpretation from typical industrial companies.
    if company.industry and "bank" in company.industry.lower():
        warnings.append(
            "Bank accounting requires industry-specific interpretation; "
            "conventional FCF and industrial-company margins may not be meaningful."
        )

    # Warn when consecutive annual periods are not spaced roughly one year apart.
    if any(
            not 330 <= (b.end - a.end).days <= 380 for a, b in zip(periods, periods[1:], strict=False)
    ):
        warnings.append(
            "Annual series contains gaps; year-over-year growth and average-balance returns are skipped across gaps."
        )
        warnings.append(
            "Debt uses current and noncurrent long-term borrowings; commercial paper and leases may be excluded."
        )

    # Warn when fewer annual periods were available than the user requested.
    if len(periods) < years:
        warnings.append(
            f"Only {len(periods)} comparable annual periods available; requested {years}."
        )

    return warnings


def normalize_facts(company: Company, payload: dict, years: int = 5) -> FinancialDataset:
    """
    Convert raw SEC company facts into comparable annual financial records.

    Args:
        company: Resolved company identity and industry metadata.
        payload: SEC company-facts response containing the facts/us-gaap namespace.
        years: Number of latest annual periods requested; defaults to 5. Callers
            should supply a positive count (research requests restrict it to 3–5).

    Returns:
        Chronological annual periods with normalized values, source provenance,
        and coverage warnings. Missing metrics stay None; only supported USD
        facts and USD/shares diluted EPS are used.

    Raises:
        DataUnavailable: If no supported annual duration facts can anchor a period.
        ValueError: If selected company/source data cannot form validated records.
    """
    facts = payload.get("facts", {}).get("us-gaap", {})
    candidates = _collect_candidates(facts)
    anchor_field, anchors = _select_annual_anchors(candidates, years)
    periods = [_build_period(company, candidates, start, end) for start, end in anchors]
    warnings = _collect_warnings(company, anchor_field, periods, years)
    return FinancialDataset(company=company, periods=periods, warnings=warnings)