"""Configuration (§7.3).

Precedence: CLI flags (exported as env by `retinue.cli`) > `RETINUE_*` env vars >
`~/.retinue/config.toml` > defaults. Nested fields use `__` in env names, e.g.
`RETINUE_SERVER__PORT=9000`, `RETINUE_MODELS__DEFAULT=anthropic/claude-sonnet-4-5`.
"""

import os
import secrets as pysecrets
import stat
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    TomlConfigSettingsSource,
)

DEFAULT_HOME = Path("~/.retinue").expanduser()


class ServerSettings(BaseModel):
    host: str = "127.0.0.1"
    port: int = 8000
    # Vite dev server origins; same-origin production traffic never needs CORS.
    cors_origins: list[str] = Field(
        default_factory=lambda: ["http://localhost:5173", "http://127.0.0.1:5173"]
    )
    enable_docs: bool = True


class AuthSettings(BaseModel):
    access_ttl_s: int = 900  # 15 min (D18)
    refresh_ttl_days: int = 30
    registration_enabled: bool = True
    # None = auto: Secure cookies whenever the request arrived over https
    cookie_secure: bool | None = None


class StreamSettings(BaseModel):
    heartbeat_s: float = 15.0  # §19
    ring_size: int = 512  # §7.4
    ring_ttl_s: float = 60.0
    write_behind_ms: int = 50
    # how long a stream keeps producing with zero subscribers before it is
    # treated as an implicit client abort (§7.4 rule 3, resume-friendly)
    orphan_grace_s: float = 5.0


class ContextSettings(BaseModel):
    """§31.2 tiered caps, as fractions of the post-reserve budget."""

    history_frac: float = 0.45
    rag_frac: float = 0.20
    graph_frac: float = 0.10
    priors_frac: float = 0.08
    schema_cards_frac: float = 0.08
    memory_frac: float = 0.04
    slack_frac: float = 0.05
    default_max_output: int = 4096
    min_history_messages: int = 2  # never trim below the current exchange


class ModelsSettings(BaseModel):
    default: str | None = None  # e.g. "openai/gpt-4o"; None = first available
    housekeeping: str | None = None  # cheap-small class for titles etc. (§31.3)
    mock_enabled: bool = False  # dev/test provider ("mock/echo")
    list_ttl_s: int = 3600  # §8 model list cache TTL


class RateLimitSettings(BaseModel):
    enabled: bool = True
    auth_per_min: float = 5
    chat_per_min: float = 30
    default_per_min: float = 240
    burst_factor: float = 2.0


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="RETINUE_",
        env_nested_delimiter="__",
        extra="ignore",
    )

    home_dir: Path = DEFAULT_HOME
    data_dir: Path | None = None  # default: {home_dir}/data
    database_url: str | None = None  # default: sqlite+aiosqlite on {data_dir}/app.db
    secret: str | None = None  # master secret; auto-generated & persisted if unset
    log_level: str = "info"
    log_format: Literal["console", "json"] = "console"
    default_system_prompt: str | None = None
    image_proxy_enabled: bool = True
    auto_migrate: bool = True

    server: ServerSettings = Field(default_factory=ServerSettings)
    auth: AuthSettings = Field(default_factory=AuthSettings)
    stream: StreamSettings = Field(default_factory=StreamSettings)
    context: ContextSettings = Field(default_factory=ContextSettings)
    models: ModelsSettings = Field(default_factory=ModelsSettings)
    ratelimit: RateLimitSettings = Field(default_factory=RateLimitSettings)

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        toml_file = Path(
            os.environ.get("RETINUE_CONFIG_FILE", str(DEFAULT_HOME / "config.toml"))
        ).expanduser()
        sources: tuple[PydanticBaseSettingsSource, ...] = (init_settings, env_settings)
        if toml_file.is_file():
            sources = (*sources, TomlConfigSettingsSource(settings_cls, toml_file=toml_file))
        return sources

    # -- resolved paths -----------------------------------------------------

    @property
    def resolved_data_dir(self) -> Path:
        return (self.data_dir or self.home_dir / "data").expanduser()

    @property
    def logs_dir(self) -> Path:
        return self.resolved_data_dir / "logs"

    @property
    def effective_database_url(self) -> str:
        if self.database_url:
            return self.database_url
        return f"sqlite+aiosqlite:///{self.resolved_data_dir / 'app.db'}"

    @property
    def is_sqlite(self) -> bool:
        return self.effective_database_url.startswith("sqlite")

    def ensure_dirs(self) -> None:
        self.resolved_data_dir.mkdir(parents=True, exist_ok=True)
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        (self.home_dir / "keys").mkdir(parents=True, exist_ok=True)

    # -- master secret (§16 / D19) ------------------------------------------

    @property
    def secret_file(self) -> Path:
        return self.home_dir / "secret"

    def master_secret(self) -> str:
        """RETINUE_SECRET, else a generated secret persisted with 0600 perms.

        Persisting keeps encrypted credentials and sessions valid across
        restarts on zero-config installs; `retinue doctor` reports which mode
        is active so operators can promote to an explicit secret.
        """
        if self.secret:
            return self.secret
        path = self.secret_file
        if path.is_file():
            return path.read_text(encoding="utf-8").strip()
        path.parent.mkdir(parents=True, exist_ok=True)
        generated = pysecrets.token_urlsafe(48)
        path.write_text(generated + "\n", encoding="utf-8")
        path.chmod(stat.S_IRUSR | stat.S_IWUSR)
        return generated

    def secret_source(self) -> str:
        if self.secret:
            return "env"
        return "file" if self.secret_file.is_file() else "generated"
