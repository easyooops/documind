"""Unified LLM provider factory - all providers through LangChain interface.

Supports: OpenAI, Anthropic, Azure OpenAI, AWS Bedrock, GCP Vertex AI,
           Google Gemini, Ollama, vLLM, and any OpenAI-compatible endpoint.

Connection info comes from .env (Settings).
Model name and parameters come from agents/configs/*.json resolved via loader.py.

Authentication chains:
- AWS Bedrock: explicit keys > named profile > env vars > shared config > IAM role
- GCP Vertex: GOOGLE_APPLICATION_CREDENTIALS > gcloud ADC > metadata server
- Azure: API key > DefaultAzureCredential (Managed Identity, CLI, env)
"""

from __future__ import annotations

from langchain_core.language_models import BaseChatModel

from src.core.config import settings
from src.core.exceptions import LLMProviderError
from src.core.logging import get_logger

logger = get_logger(__name__)


def _create_openai(model: str, **kwargs) -> BaseChatModel:
    """OpenAI API direct connection or any OpenAI-compatible proxy."""
    from langchain_openai import ChatOpenAI

    params = {
        "api_key": settings.openai_api_key,
        "model": model,
        **kwargs,
    }
    if settings.openai_base_url:
        params["base_url"] = settings.openai_base_url
    return ChatOpenAI(**params)


def _create_anthropic(model: str, **kwargs) -> BaseChatModel:
    """Anthropic Claude direct API connection."""
    from langchain_anthropic import ChatAnthropic

    return ChatAnthropic(
        api_key=settings.anthropic_api_key,
        model=model,
        **kwargs,
    )


def _create_azure(model: str, **kwargs) -> BaseChatModel:
    """Azure OpenAI Service connection.

    Auth priority:
    1. AZURE_OPENAI_API_KEY (if provided)
    2. DefaultAzureCredential (Managed Identity, CLI login, env vars)
    """
    from langchain_openai import AzureChatOpenAI

    params: dict = {
        "azure_endpoint": settings.azure_openai_endpoint,
        "api_version": settings.azure_openai_api_version,
        "azure_deployment": settings.azure_openai_deployment or model,
        **kwargs,
    }

    if settings.azure_openai_api_key:
        params["api_key"] = settings.azure_openai_api_key
    else:
        # Use DefaultAzureCredential (Managed Identity, az login, etc.)
        from azure.identity import DefaultAzureCredential
        params["azure_ad_token_provider"] = DefaultAzureCredential()

    return AzureChatOpenAI(**params)


def _create_bedrock(model: str, **kwargs) -> BaseChatModel:
    """AWS Bedrock connection with full credential chain support.

    Authentication priority (standard AWS SDK behavior):
    1. Explicit keys (AWS_ACCESS_KEY_ID + AWS_SECRET_ACCESS_KEY + optional SESSION_TOKEN)
    2. Named profile (AWS_PROFILE) - ideal for local dev with SSO/MFA
    3. Environment variables from shell (standard AWS_* vars)
    4. Shared config file (~/.aws/config) - supports role_arn, credential_process, SSO
    5. EC2 Instance Metadata / ECS Task Role / IRSA (K8s) - for production deployments

    For local development: set AWS_PROFILE in .env or configure ~/.aws/config.
    For production (EC2/ECS/EKS): leave all AWS fields blank; SDK auto-discovers IAM role.
    """
    from langchain_aws import ChatBedrock
    import boto3

    session_kwargs: dict = {}

    if settings.aws_region:
        session_kwargs["region_name"] = settings.aws_region

    # Priority 1: Explicit credentials
    if settings.aws_access_key_id and settings.aws_secret_access_key:
        session_kwargs["aws_access_key_id"] = settings.aws_access_key_id
        session_kwargs["aws_secret_access_key"] = settings.aws_secret_access_key
        if settings.aws_session_token:
            session_kwargs["aws_session_token"] = settings.aws_session_token
        logger.debug("bedrock.auth", method="explicit_keys")

    # Priority 2: Named profile
    elif settings.aws_profile:
        session_kwargs["profile_name"] = settings.aws_profile
        logger.debug("bedrock.auth", method="named_profile", profile=settings.aws_profile)

    # Priority 3-5: Let boto3 handle credential discovery
    # (env vars, shared config, instance metadata)
    else:
        logger.debug("bedrock.auth", method="default_chain")

    session = boto3.Session(**session_kwargs)

    # Optional: assume role for cross-account access
    if settings.aws_role_arn:
        sts_client = session.client("sts")
        assumed = sts_client.assume_role(
            RoleArn=settings.aws_role_arn,
            RoleSessionName="documind-bedrock-session",
        )
        creds = assumed["Credentials"]
        session = boto3.Session(
            aws_access_key_id=creds["AccessKeyId"],
            aws_secret_access_key=creds["SecretAccessKey"],
            aws_session_token=creds["SessionToken"],
            region_name=settings.aws_region,
        )
        logger.debug("bedrock.auth", method="assume_role", role=settings.aws_role_arn)

    bedrock_client = session.client("bedrock-runtime")

    model_kwargs = {k: v for k, v in kwargs.items() if k in ("temperature", "max_tokens", "top_p")}

    # Bedrock Anthropic models reject requests with both temperature and top_p
    if "temperature" in model_kwargs and "top_p" in model_kwargs:
        del model_kwargs["top_p"]

    return ChatBedrock(
        client=bedrock_client,
        model_id=model,
        model_kwargs=model_kwargs,
    )


def _create_gcp_vertex(model: str, **kwargs) -> BaseChatModel:
    """GCP Vertex AI connection.

    Authentication via Application Default Credentials (ADC):
    1. GOOGLE_APPLICATION_CREDENTIALS env var (service account JSON path)
    2. gcloud auth application-default login (local dev)
    3. GCE/GKE metadata server (production)
    4. Workload Identity Federation
    """
    from langchain_google_vertexai import ChatVertexAI

    return ChatVertexAI(
        model_name=model,
        project=settings.gcp_project_id,
        location=settings.gcp_location,
        **kwargs,
    )


def _create_gemini(model: str, **kwargs) -> BaseChatModel:
    """Google Gemini via API key (Google AI Studio, non-Vertex)."""
    from langchain_google_genai import ChatGoogleGenerativeAI

    return ChatGoogleGenerativeAI(
        model=model,
        google_api_key=settings.google_api_key,
        **kwargs,
    )


def _create_ollama(model: str, **kwargs) -> BaseChatModel:
    """Local Ollama via OpenAI-compatible interface."""
    from langchain_openai import ChatOpenAI

    return ChatOpenAI(
        api_key="ollama",
        base_url=settings.custom_llm_base_url or "http://localhost:11434/v1",
        model=model,
        **kwargs,
    )


def _create_custom(model: str, **kwargs) -> BaseChatModel:
    """Any OpenAI-compatible endpoint (vLLM, TGI, LiteLLM, LocalAI, etc.)."""
    from langchain_openai import ChatOpenAI

    return ChatOpenAI(
        api_key=settings.custom_llm_api_key or "no-key",
        base_url=settings.custom_llm_base_url,
        model=model,
        **kwargs,
    )


# Provider registry
_PROVIDERS = {
    "openai": _create_openai,
    "anthropic": _create_anthropic,
    "azure": _create_azure,
    "bedrock": _create_bedrock,
    "gcp_vertex": _create_gcp_vertex,
    "gemini": _create_gemini,
    "ollama": _create_ollama,
    "vllm": _create_custom,
    "custom": _create_custom,
}


def create_llm(model: str, provider: str | None = None, **kwargs) -> BaseChatModel:
    """Create a LangChain ChatModel for the given provider and model.

    Args:
        model: Model name (e.g., "gpt-4o", "claude-3-5-sonnet", "us.anthropic.claude-v2")
        provider: Override provider. If None, uses LLM_PROVIDER from .env
        **kwargs: LLM parameters (temperature, max_tokens, top_p)

    Returns:
        LangChain BaseChatModel instance
    """
    provider = provider or settings.llm_provider

    factory = _PROVIDERS.get(provider)
    if not factory:
        raise LLMProviderError(
            provider,
            f"Unsupported provider: '{provider}'. "
            f"Available: {', '.join(_PROVIDERS.keys())}",
        )

    clean_kwargs = {k: v for k, v in kwargs.items() if v is not None}
    logger.debug("llm.create", provider=provider, model=model, kwargs=list(clean_kwargs.keys()))

    return factory(model, **clean_kwargs)


def create_llm_from_config(resolved_config: dict) -> BaseChatModel:
    """Create LLM from a fully resolved agent config.

    Expects resolved_config to have an 'llm' sub-dict with keys:
        model, temperature, max_tokens, top_p

    Args:
        resolved_config: Merged config from loader.py containing 'llm' block.

    Returns:
        LangChain BaseChatModel instance
    """
    llm_cfg = resolved_config.get("llm", {})
    model = llm_cfg.get("model", "gpt-4o")
    temperature = llm_cfg.get("temperature", 0.7)
    max_tokens = llm_cfg.get("max_tokens", 4096)
    top_p = llm_cfg.get("top_p", 1.0)

    return create_llm(
        model=model,
        temperature=temperature,
        max_tokens=max_tokens,
        top_p=top_p,
    )
