from finsight.data.http import CachedHttp, DataUnavailable

SERIES = {"federal_funds": "FEDFUNDS", "treasury_10y": "DGS10", "cpi": "CPIAUCSL"}


class FredClient:
    """Client for retrieving and formatting supported macroeconomic data from the FRED API."""

    def __init__(self, http: CachedHttp, api_key: str):
        self.http, self.api_key = http, api_key

    def series(self, name: str) -> dict:
        """Fetch recent observations for a supported FRED macroeconomic series."""

        # Reject macroeconomic series that are not supported by this application.
        if name not in SERIES:
            raise ValueError("Supported macro series: federal_funds, treasury_10y, cpi")

        # Ensure a FRED API key is configured before making the request.
        if not self.api_key:
            raise DataUnavailable("Set FRED_API_KEY to enable optional macro research.")

        # Convert the friendly series name into the actual FRED series ID.
        series = SERIES[name]

        # Fetch the latest 24 observations from the FRED API.
        raw = self.http.get(
            "https://api.stlouisfed.org/fred/series/observations",
            params={
                "series_id": series,
                "api_key": self.api_key,
                "file_type": "json",
                "sort_order": "desc",
                "limit": 24,
            },
        )

        # Convert valid observation values from strings to floats and skip missing "." values.
        observations = [
            {"date": row["date"], "value": float(row["value"])}
            for row in raw.get("observations", [])
            if row["value"] != "."
        ]

        # Return normalized macroeconomic data with units, source, and interpretation note.
        return {
            "series": series,
            "observations": observations,
            "unit": "index 1982-1984=100" if name == "cpi" else "percent",
            "source": f"https://fred.stlouisfed.org/series/{series}",
            "note": "CPI is an index level, not an inflation rate." if name == "cpi" else "",
        }