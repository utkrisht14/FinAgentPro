"""One stdio MCP server. No network listener or write-capable agent tools."""
import json

from functools import lru_cache
from fastmcp import FastMCP

from finsight.config import Settings
from finsight.data.service import FinanceService
from finsight.models.schemas import FinancialDataset
from finsight.tools.calculations import add_valuation, fundamental_metrics, market_metrics


mcp = FastMCP("Finsight Finance")

@lru_cache(maxsize=1)
def service() -> FinanceService:
    return FinanceService(Settings())


@mcp.tool()
def get_company_info(ticker: str) -> dict:
    """ Resolve a US ticker to company metadata and SEC CIK"""
    return service().company(ticker).model_dump(mode="json")


@mcp.tool()
def get_fundamentals(ticker: str, years: int = 5) -> dict:
    """ Get normalized annual USD facts, deterministic ratios and provenance. Raw data is offloaded. """
    if not 3 <= years <= 5:
        raise ValueError("Analysis requires 3 to 5 annual periods.")
    dataset = service().financials(ticker, years)
    return {
        "analysis": fundamental_metrics(dataset).model_dump(mode="json"),
        "artifact": service().offload(dataset.model_dump(mode="json")),
    }


@mcp.tool()
def get_market_analysis(ticker: str, years: int = 5) -> dict:
    """Get deterministic price returns, volatility and drawdown; raw daily prices are offloaded."""
    if not 3 <= years <= 5:
        raise ValueError("Analysis requires 3 to 5 years.")
    dataset = service().prices(ticker, years)
    analysis = market_metrics(dataset)
    try:
        analysis = add_valuation(
            analysis, service().snapshot(ticker), service().financials(ticker, years)
        )
    except Exception as exc:
        analysis.warnings.append(f"Valuation inputs unavailable: {str(exc)[:250]}")
    return {
        "analysis": analysis.model_dump(mode="json"),
        "artifact": service().offload(dataset.model_dump(mode="json")),
    }


@mcp.tool()
def get_filing_evidence(ticker: str, form: str = "10-K") -> dict:
    """Get bounded SEC filing section excerpts and a reference to the complete offloaded filing."""
    filing = service().filing(ticker, form)
    result = filing.model_dump(mode="json")
    result["sections"] = {name: text[:10000] for name, text in filing.sections.items()}
    if any(len(text) > 10000 for text in filing.sections.values()):
        result["warnings"].append(
            "Excerpts limited to 10,000 characters per section; read artifact for full filing."
        )
    return result


@mcp.tool()
def get_company_filings(ticker: str) -> list[dict]:
    """List recent 10-K and 10-Q filings with SEC URLs."""
    return service().sec.filings(service().company(ticker))


@mcp.tool()
def read_research_artifact(artifact: str, offset: int = 0, limit: int = 12000) -> dict:
    """Read a bounded slice of an offloaded dataset or filing using its artifact reference."""
    return service().read_artifact(artifact, offset, limit)


@mcp.tool()
def get_macro_series(name: str) -> dict:
    """ Fetch federal_funds, treasury_10y, or cpi from FRED. CPI is an index, not an inflation rate. """
    return service().macro(name)


@mcp.tool()
def calculate_financial_metrics(artifact: str) -> dict:
    """ Recalculate annual metrics from a validated, offloaded financial dataset. """
    root = service().settings.data_dir / "artifacts"
    path = (root / artifact).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError("Invalid artifact path.")
    dataset = FinancialDataset.model_validate(json.loads(path.read_text(encoding="utf-8")))
    return fundamental_metrics(dataset).model_dump(mode="json")


def main():
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
