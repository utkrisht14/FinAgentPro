from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    mode: Literal["demo", "live"] = Field("live", validation_alias="LIVE")
    data_dir:Path = Field(Path("runtime"), validation_alias="FINSIGHT_DATA_DIR")
    sec_user_agent: str = ""
    llm_provider: Literal["ollama", "openai"] = ""
    llm_model:str = ""
    openai_api_key: str = ""
    ollama_base_url = "http://localhost:11434"
    fred_api_key:str = Field("", repr=False)
    app_env:str = ""

    def prepare(self):
        self.data_dir = self.data_dir.resolve()
        for directory in ("cache", "artifacts", "reports", "db"):
            (self.data_dir / directory).mkdir(parents=True, exist_ok=True)
        return self