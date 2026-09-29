import json
import os
import sys
from contextlib import asynccontextmanager

from langchain_mcp_adapters.tools import load_mcp_tools
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from finsight.config import Settings
from finsight.data.http import DataUnavailable


class FinanceMCP:
    def __init__(self, session: ClientSession):
        self.session = session

    async def call(self, name: str, **arguments):
        result = await self.session.call_tool(name, arguments)
        if result.isError:
            message = " ".join(c.text for c in result.content if c.type == "text")
            raise DataUnavailable(f"{name}: {message[:500]}")
        if result.structuredContent is not None:
            value = result.structuredContent
            return value.get("result", value) if set(value) == {"result"} else value
        texts = [c.text for c in result.content if c.type == "text"]
        if len(texts) == 1:
            return json.loads(texts[0])
        return [json.loads(text) for text in texts]

    async def agent_tools(self):
        return await load_mcp_tools(self.session)


@asynccontextmanager
async def connect_finance(settings: Settings):
    # Explicit environment allows Streamlit reruns to select mode without mutating os.environ.
    env = {
        **os.environ,
        "FINSIGHT_MODE": settings.mode,
        "FINSIGHT_DATA_DIR": str(settings.data_dir.resolve()),
        "SEC_USER_AGENT": settings.sec_user_agent,
        "FRED_API_KEY": settings.fred_api_key,
    }
    params = StdioServerParameters(
        command=sys.executable, args=["-m", "finsight.mcp.finance_server"], env=env
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            yield FinanceMCP(session)
