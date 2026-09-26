import re

from bs4 import BeautifulSoup

from finsight.data.http import CachedHttp, DataUnavailable
from finsight.models.schemas import Company, Filing


class SecClient:

    def __init__(self, http: CachedHttp, user_agent: str):
        if "@" not in user_agent:
            raise DataUnavailable("Set SEC_USER_AGENT to your application name and contact email in .env.")
        self.http = http


    def company(self, ticker: str):
        ticker = Company.valid_ticker(ticker)

        # Fetch the SEC company directory containing ticker symbols, company names, and CIK numbers.
        mapping = self.http.get("https://www.sec.gov/files/company_tickers.json")

        # Find the SEC company record whose ticker matches the requested ticker, or return None if not found.
        row = next((v for v in mapping.values() if v["ticker"].upper() == ticker), None)

        if row is None:
            raise DataUnavailable(f"SEC company not found for ticker {ticker}")

        # Convert the company CIK to a 10-digit string by adding leading zeros.
        cik = str(row["cik_str"]).zfill(10)

        metadata = self.http.get(f"https://data.sec.gov/submissions/CIK{cik}.json")

        return Company(
            ticker = ticker,
            name = metadata.get("name", row["title"]),
            cik = cik,
            industry = metadata.get("sicDescription"),
            fiscal_year_end = metadata.get("fiscalYearEnd"),
        )


    def facts(self, company: Company):
        return self.http.get(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{company.cik}.json")


    def filings(self, company: Company) -> list[dict]:
        # Fetch the company’s SEC submission metadata and recent filing history using its CIK.
        payload = self.http.get(f"https://data.sec.gov/submissions/CIK{company.cik}.json")

        # Extract the company's recent SEC filings, or use an empty dictionary if unavailable.
        recent = payload.get("filings", {}).get("recent", {})

        rows = []

        # Collect recent 10-K and 10-Q filings with their filing date, accession number, and SEC document URL.
        for i, form in enumerate(recent.get("form", [])):
            if form in ("10-K", "10-Q"):
                accession = recent["accessionNumber"][i]
                document = recent["primaryDocument"][i]
                rows.append(
                    {
                        "form": form,
                        "filed": recent["filingDate"][i],
                        "accession": accession,
                        "url": f"https://www.sec.gov/Archives/edgar/data/{int(company.cik)}/"
                        f"{accession.replace('-', '')}/{document}",
                    }
                )
        return rows


    def filing(self, company: Company, form: str="10-K") -> tuple[Filing, str]:
        candidates = [r for r in self.filings(company) if r["form"] == form]

        if not candidates:
            raise DataUnavailable(f"No recent {form} filing found for {company.ticker}.")

        # Select the most recently filed report based on the filing date.
        row = max(candidates, key=lambda r: r["filed"])

        # Download the filing HTML as text and cache it for 30 days.
        html = self.http.get(row["url"], text=True, ttl=30 * 86400)

        # Parse the HTML into a BeautifulSoup object for easier content extraction.
        soup = BeautifulSoup(html, "html.parser")

        # Remove script, style, and inline XBRL header elements from the filing HTML before extracting text.
        for node in soup(["script", "style", "ix:header"]):
            node.decompose() # Remove this HTML element from soup entirely.

        text = soup.get_text("\n", strip=True)
        sections = extract_sections(text, form)
        warnings = (
            []
            if sections
            else ["Section boundaries were not confidently identified; use the offloaded filing."]
        )

        return Filing(ticker=company.ticker, form=form, sections=sections, warnings=warnings)


def extract_sections(text: str, form: str) -> dict[str, str]:
    # Define the start and end patterns for important sections in 10-K or 10-Q filings.
    # Longest bounded occurrence avoids most table-of-contents matches. Never claim full coverage.
    patterns = (
        {
            "Business": (
                r"1\.?\s+Business",
                r"1A\.?\s+Risk"
            ),
            "Risk Factors": (
                r"1A\.?\s+Risk Factors",
                r"1B\.?|1C\.?|2\.?\s+Properties"
            ),
            "MD&A": (
                r"7\.?\s+Management",
                r"7A\.?|8\.?\s+Financial"
            ),
            "Market Risk": (
                r"7A\.?",
                r"8\.?\s+Financial"
            ),
            "Legal Proceedings": (
                r"3\.?\s+Legal",
                r"4\.?"
            ),
        }
        if form == "10-K"
        else {
            "MD&A": (
                r"2\.?\s+Management",
                r"3\.?\s+Quantitative"
            ),
            "Risk Factors": (
                r"1A\.?\s+Risk Factors",
                r"2\.?\s+Unregistered"
            ),
        }
    )

    result = {}

    # Replace repeated spaces, tabs, newlines, and non-breaking spaces with one normal space.
    normalized = re.sub(r"[\s\u00a0]+", " ", text)

    # Search for each desired filing section between its start and stop boundaries.
    for name, (start, stop) in patterns.items():
        matches = re.findall(
            r"\bItem\s+" + start + r"(.*?)(?=\bItem\s+(?:" + stop + r"))",
            normalized,
            flags=re.I | re.S,
        )

        if matches:
            # Choose the longest match to reduce the chance of selecting a table-of-contents entry.
            section = max(matches, key=len).strip()

            # Ignore very short matches that are unlikely to contain the real section content.
            if len(section) > 300:
                result[name] = section

    # Return the extracted sections as {section_name: section_text}.
    return result
