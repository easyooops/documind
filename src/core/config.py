"""Application settings - connection profiles and default model configuration."""

from __future__ import annotations

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Connection and model routing settings.

    Connection info: provider auth credentials.
    Model routing: USE_DEFAULT_MODELS toggle + 3 default model names.
    Agent parameters (temperature, max_tokens, etc.) are ALWAYS in agents/configs/{agent}.json.
    """

    # Provider selection
    llm_provider: str = "openai"

    # Default model toggle and names
    use_default_models: bool = True
    default_llm_model: str = "gpt-4o"
    default_vlm_model: str = "gpt-4o"
    default_image_model: str = "dall-e-3"

    # Application identity
    app_name: str = "DocuMind"

    # OpenAI
    openai_api_key: str = ""
    openai_base_url: str | None = None

    # Anthropic
    anthropic_api_key: str = ""

    # Google Gemini (API key)
    google_api_key: str = ""

    # GCP Vertex AI
    gcp_project_id: str = ""
    gcp_location: str = "us-central1"
    google_application_credentials: str | None = None

    # AWS Bedrock
    aws_profile: str | None = None
    aws_region: str = "us-east-1"
    aws_access_key_id: str | None = None
    aws_secret_access_key: str | None = None
    aws_session_token: str | None = None
    aws_role_arn: str | None = None
    aws_bedrock_connect_timeout: int = 10
    aws_bedrock_read_timeout: int = 180
    aws_bedrock_max_attempts: int = 3

    # Azure OpenAI
    azure_openai_api_key: str = ""
    azure_openai_endpoint: str = ""
    azure_openai_api_version: str = "2024-06-01"
    azure_openai_deployment: str = ""

    # Custom / On-premise (OpenAI-compatible: vLLM, TGI, Ollama, etc.)
    custom_llm_base_url: str = "http://localhost:8080/v1"
    custom_llm_api_key: str = ""
    custom_llm_model_name: str = ""

    # Database
    database_type: str = "sqlite"
    database_path: str = "./data/documind.db"
    db_host: str = "localhost"
    db_port: int = 5432
    db_name: str = "documind"
    db_user: str = "documind"
    db_password: str = ""

    # Storage
    storage_type: str = "local"
    storage_local_path: str = "./data/outputs"
    aws_s3_bucket: str | None = None
    aws_s3_region: str | None = None

    # App
    app_port: int = 8000
    app_env: str = "development"
    log_level: str = "INFO"
    log_file: str = "data/logs/documind.log"
    log_backup_count: int = 14
    cors_origins: str = "http://localhost:3000,http://localhost:5173"

    def get_default_model(self, provider_type: str) -> str | None:
        """Get the default model name for a given provider_type.

        Returns None if USE_DEFAULT_MODELS is false.
        """
        if not self.use_default_models:
            return None

        mapping = {
            "llm": self.default_llm_model,
            "vlm": self.default_vlm_model,
            "image": self.default_image_model,
        }
        return mapping.get(provider_type, self.default_llm_model)

    @property
    def db_url(self) -> str:
        if self.database_type == "sqlite":
            return f"sqlite+aiosqlite:///{self.database_path}"
        elif self.database_type == "postgresql":
            return (
                f"postgresql+asyncpg://{self.db_user}:{self.db_password}"
                f"@{self.db_host}:{self.db_port}/{self.db_name}"
            )
        raise ValueError(f"Unsupported database type: {self.database_type}")

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}


settings = Settings()
