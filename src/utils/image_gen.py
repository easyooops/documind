"""Image generation utility using DEFAULT_IMAGE_MODEL from .env.

Supports AWS Bedrock image models (e.g., Amazon Titan Image Generator, Stability AI)
for generating diagrams, illustrations, and decorative images for slides.
"""

from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
from typing import Optional

from src.core.config import settings
from src.core.logging import get_logger

logger = get_logger(__name__)

IMAGE_CACHE_DIR = Path("data/cache/images")


async def generate_image(
    prompt: str,
    width: int = 512,
    height: int = 512,
    style: str = "professional",
    negative_prompt: str = "blurry, low quality, text, watermark, logo",
) -> Optional[Path]:
    """Generate an image using the configured image model.

    Args:
        prompt: Description of the image to generate
        width: Image width in pixels (must be supported by model)
        height: Image height in pixels
        style: Style hint ('professional', 'diagram', 'illustration', 'abstract')
        negative_prompt: What to avoid in the image

    Returns:
        Path to the generated PNG image, or None on failure
    """
    model_id = settings.default_image_model
    if not model_id:
        logger.warning("image_gen.no_model", message="DEFAULT_IMAGE_MODEL not configured")
        return None

    cache_key = hashlib.md5(f"{prompt}_{width}_{height}_{style}".encode()).hexdigest()
    cache_path = IMAGE_CACHE_DIR / f"{cache_key}.png"

    if cache_path.exists():
        return cache_path

    IMAGE_CACHE_DIR.mkdir(parents=True, exist_ok=True)

    style_prefix = _get_style_prefix(style)
    full_prompt = f"{style_prefix}{prompt}"

    try:
        model_key = model_id.lower()
        if "nova-canvas" in model_key:
            image_data = await _generate_with_nova_canvas(
                model_id, full_prompt, negative_prompt, width, height
            )
        elif "titan" in model_key:
            image_data = await _generate_with_titan(model_id, full_prompt, negative_prompt, width, height)
        elif "stability" in model_key or "sdxl" in model_key:
            image_data = await _generate_with_stability(model_id, full_prompt, negative_prompt, width, height)
        else:
            image_data = await _generate_with_titan(model_id, full_prompt, negative_prompt, width, height)

        if image_data:
            cache_path.write_bytes(image_data)
            logger.info("image_gen.success", model=model_id, prompt=prompt[:50], path=str(cache_path))
            return cache_path
        return None

    except Exception as e:
        logger.warning("image_gen.error", model=model_id, error=str(e)[:200])
        return None


async def _generate_with_titan(
    model_id: str, prompt: str, negative_prompt: str, width: int, height: int
) -> Optional[bytes]:
    """Generate image using Amazon Titan Image Generator via Bedrock."""
    client = _bedrock_runtime_client()

    body = json.dumps({
        "taskType": "TEXT_IMAGE",
        "textToImageParams": {
            "text": prompt,
            "negativeText": negative_prompt,
        },
        "imageGenerationConfig": {
            "numberOfImages": 1,
            "width": width,
            "height": height,
            "quality": "standard",
        }
    })

    response = client.invoke_model(
        modelId=model_id,
        body=body,
        contentType="application/json",
        accept="application/json",
    )

    response_body = json.loads(response["body"].read())
    images = response_body.get("images", [])
    if images:
        return base64.b64decode(images[0])
    return None


async def _generate_with_nova_canvas(
    model_id: str, prompt: str, negative_prompt: str, width: int, height: int
) -> Optional[bytes]:
    """Generate image using Amazon Nova Canvas via Bedrock."""
    client = _bedrock_runtime_client()

    text_params = {"text": prompt}
    if negative_prompt:
        text_params["negativeText"] = negative_prompt
    body = json.dumps({
        "taskType": "TEXT_IMAGE",
        "textToImageParams": text_params,
        "imageGenerationConfig": {
            "numberOfImages": 1,
            "width": width,
            "height": height,
        },
    })

    response = client.invoke_model(
        modelId=model_id,
        body=body,
        contentType="application/json",
        accept="application/json",
    )

    response_body = json.loads(response["body"].read())
    images = response_body.get("images", [])
    if images:
        return base64.b64decode(images[0])
    return None


async def _generate_with_stability(
    model_id: str, prompt: str, negative_prompt: str, width: int, height: int
) -> Optional[bytes]:
    """Generate image using Stability AI models via Bedrock."""
    client = _bedrock_runtime_client()

    body = json.dumps({
        "text_prompts": [
            {"text": prompt, "weight": 1.0},
            {"text": negative_prompt, "weight": -1.0},
        ],
        "cfg_scale": 7,
        "steps": 30,
        "width": width,
        "height": height,
    })

    response = client.invoke_model(
        modelId=model_id,
        body=body,
        contentType="application/json",
        accept="application/json",
    )

    response_body = json.loads(response["body"].read())
    artifacts = response_body.get("artifacts", [])
    if artifacts:
        return base64.b64decode(artifacts[0]["base64"])
    return None


def _bedrock_runtime_client():
    import boto3
    from botocore.config import Config

    session_kwargs: dict = {}
    if settings.aws_region:
        session_kwargs["region_name"] = settings.aws_region
    if settings.aws_access_key_id and settings.aws_secret_access_key:
        session_kwargs["aws_access_key_id"] = settings.aws_access_key_id
        session_kwargs["aws_secret_access_key"] = settings.aws_secret_access_key
        if settings.aws_session_token:
            session_kwargs["aws_session_token"] = settings.aws_session_token
    elif settings.aws_profile:
        session_kwargs["profile_name"] = settings.aws_profile

    session = boto3.Session(**session_kwargs)
    return session.client(
        "bedrock-runtime",
        config=Config(
            connect_timeout=settings.aws_bedrock_connect_timeout,
            read_timeout=settings.aws_bedrock_read_timeout,
            retries={
                "max_attempts": settings.aws_bedrock_max_attempts,
                "mode": "standard",
            },
        ),
    )


def _get_style_prefix(style: str) -> str:
    """Return a style-appropriate prompt prefix."""
    prefixes = {
        "professional": "Clean, professional, corporate presentation graphic: ",
        "diagram": "Technical diagram, clean lines, minimal style, white background: ",
        "illustration": "Modern flat illustration, clean vector style: ",
        "abstract": "Abstract geometric pattern, corporate colors: ",
        "infographic": "Data infographic, clean design, professional: ",
    }
    return prefixes.get(style, prefixes["professional"])
