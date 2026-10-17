"""Runtime settings, read from COPILOT_* environment variables and the repo-root .env."""

from functools import lru_cache
from pathlib import Path

from psycopg.conninfo import make_conninfo
from pydantic_settings import BaseSettings, SettingsConfigDict


def find_repo_root(start: Path | None = None) -> Path:
    """Walk up from `start` to the directory holding docker-compose.yml."""
    here = (start or Path(__file__)).resolve()
    for candidate in (here, *here.parents):
        if (candidate / "docker-compose.yml").is_file():
            return candidate
    raise RuntimeError(f"no docker-compose.yml above {here}")


REPO_ROOT = find_repo_root()

ROLES = ("app_owner", "ops_reader", "ops_loader", "kb_reader", "kb_writer")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="COPILOT_", env_file=REPO_ROOT / ".env", extra="ignore"
    )

    pg_host: str = "127.0.0.1"  # not "localhost": Docker binds IPv4 only
    pg_port: int = 5433
    pg_db: str = "indagent"
    owner_password: str = "owner_dev_pw"
    ops_reader_password: str = "ops_reader_dev_pw"
    ops_loader_password: str = "ops_loader_dev_pw"
    kb_reader_password: str = "kb_reader_dev_pw"
    kb_writer_password: str = "kb_writer_dev_pw"
    jwt_secret: str = "dev-only-secret-change-me-0123456789abcdef"
    ollama_url: str = "http://127.0.0.1:11434"
    ollama_timeout_s: float = 60.0
    embed_model: str = "nomic-embed-text"
    reranker_model: str = "BAAI/bge-reranker-base"
    llm_model: str = "qwen2.5:7b-instruct"
    llm_timeout_s: float = 240.0
    llm_num_ctx: int = 4096
    data_svc_url: str = "http://127.0.0.1:8081"
    retrieval_strategy: str = "hybrid"
    dev_tenants: list[str] = ["acme", "globex"]

    def password_for(self, role: str) -> str:
        if role not in ROLES:
            raise ValueError(f"unknown database role: {role}")
        field = "owner_password" if role == "app_owner" else f"{role}_password"
        return getattr(self, field)

    def dsn(self, role: str) -> str:
        return make_conninfo(
            host=self.pg_host, port=self.pg_port, dbname=self.pg_db, user=role,
            password=self.password_for(role), connect_timeout=5,
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
