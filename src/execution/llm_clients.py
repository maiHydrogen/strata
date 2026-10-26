"""
STRATA Execution Engine - Universal Multi-LLM Client
Universal LLM invocation support for OpenAI, Anthropic, Google Gemini (via Vertex AI), and Mistral.
Adapted from Text2BIM's agents.py, stripped of Vectorworks and HuggingFace dependencies.
"""

import logging
import os
from typing import Optional
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

logger = logging.getLogger(__name__)

# Supported model identifiers
OPENAI_MODELS = {"gpt-4o", "o4-mini"}
ANTHROPIC_MODELS = {"claude-sonnet-4-20250514"}
GEMINI_MODELS = {"gemini-3.1-flash-preview"}
MISTRAL_MODELS = {"mistral-medium-2505"}


def _call_openai(prompt: str, model: str, api_key: Optional[str] = None) -> str:
    """
    Invoke OpenAI chat completion API.
    Supports models like gpt-4o, o4-mini.
    """
    resolved_key = api_key or os.getenv("OPENAI_API_KEY")
    if not resolved_key:
        raise ValueError(
            "OpenAI API key not found. Please provide api_key or set OPENAI_API_KEY environment variable."
        )

    try:
        from openai import OpenAI
    except ImportError as e:
        raise ImportError(
            "The 'openai' package is required to call OpenAI models. Install it with: pip install openai"
        ) from e

    try:
        client = OpenAI(api_key=resolved_key)
        response = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
        )
        content = response.choices[0].message.content
        if content is None:
            raise RuntimeError(f"OpenAI returned empty response for model {model}")
        return content
    except Exception as e:
        logger.error(f"Error calling OpenAI model '{model}': {e}")
        raise RuntimeError(f"OpenAI API call failed for model '{model}': {e}") from e


def _call_anthropic(prompt: str, model: str, api_key: Optional[str] = None) -> str:
    """
    Invoke Anthropic Messages API.
    Supports models like claude-sonnet-4-20250514.
    """
    resolved_key = api_key or os.getenv("ANTHROPIC_API_KEY")
    if not resolved_key:
        raise ValueError(
            "Anthropic API key not found. Please provide api_key or set ANTHROPIC_API_KEY environment variable."
        )

    try:
        from anthropic import Anthropic
    except ImportError as e:
        raise ImportError(
            "The 'anthropic' package is required to call Anthropic models. Install it with: pip install anthropic"
        ) from e

    try:
        client = Anthropic(api_key=resolved_key)
        response = client.messages.create(
            model=model,
            max_tokens=8192,
            messages=[{"role": "user", "content": prompt}],
        )
        texts = []
        for block in response.content:
            if hasattr(block, "text"):
                texts.append(block.text)
        result = "".join(texts)
        if not result:
            raise RuntimeError(f"Anthropic returned empty response for model {model}")
        return result
    except Exception as e:
        logger.error(f"Error calling Anthropic model '{model}': {e}")
        raise RuntimeError(f"Anthropic API call failed for model '{model}': {e}") from e


def _call_gemini(prompt: str, model: str, api_key: Optional[str] = None) -> str:
    import time
    try:
        from google import genai
    except ImportError as e:
        raise ImportError("Install google-genai") from e

    resolved_key = api_key or os.getenv("GEMINI_API_KEY")
    client = genai.Client(api_key=resolved_key)
    
    # Using 3.7-flash which might have lower server load than 3.6
    actual_model = "gemma-4-26b-a4b-it"
    
    last_error = None
    for attempt in range(4):
        try:
            response = client.models.generate_content(
                model=actual_model,
                contents=prompt,
            )
            return response.text
        except Exception as e:
            last_error = e
            error_str = str(e)
            if "503" in error_str or "429" in error_str:
                time.sleep(2 ** attempt) # Exponential backoff: 1s, 2s, 4s
                continue
            # If it's a different error, don't retry
            raise RuntimeError(f"Gemini API call failed: {e}") from e
            
    raise RuntimeError(f"Gemini API call failed after 4 retries. Server is overloaded: {last_error}")

    try:
        resolved_key = api_key or os.getenv("GEMINI_API_KEY")
        client = genai.Client(api_key=resolved_key)
        
        # Hardcode to the recommended working model
        actual_model = "gemini-3.6-flash"
        
        response = client.models.generate_content(
            model=actual_model,
            contents=prompt,
        )
        return response.text
    except Exception as e:
        logger.error(f"Error calling Gemini: {e}")
        raise RuntimeError(f"Gemini API call failed: {e}") from e

    try:
        resolved_key = api_key or os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
        if not resolved_key:
            raise ValueError("Gemini API key not found.")

        client = genai.Client(api_key=resolved_key)
        
        actual_model = model
        if model == "gemini":
            # Dynamically discover available models for this specific API key
            try:
                available = [m.name for m in client.models.list() if 'generateContent' in m.supported_actions]
                flash_models = [m for m in available if 'flash' in m]
                if flash_models:
                    # Prefer standard flash over experimental if possible
                    actual_model = flash_models[0]
                elif available:
                    actual_model = available[0]
                else:
                    actual_model = "gemini-3.1-pro-preview"
            except Exception:
                actual_model = "gemini-3.1-pro-preview"
        
        response = client.models.generate_content(
            model=actual_model,
            contents=prompt,
        )
        if response.text is None:
            raise RuntimeError(f"Gemini returned empty response for model {actual_model}")
        return response.text
    except Exception as e:
        logger.error(f"Error calling Gemini model '{model}': {e}")
        # Add available models to error message if we fail
        avail_str = ""
        try:
            avail = [m.name for m in client.models.list()]
            avail_str = " Available models: " + ", ".join(avail)
        except:
            pass
        raise RuntimeError(f"Gemini API call failed for model '{actual_model}' (Requested: {model}): {e}{avail_str}") from e

    try:
        resolved_key = api_key or os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
        if not resolved_key:
            raise ValueError("Gemini API key not found. Please provide api_key or set GEMINI_API_KEY.")

        # STRICTLY AI STUDIO: DO NOT pass project or location
        client = genai.Client(api_key=resolved_key)
        
        # If UI passes generic 'gemini', default to gemini-3.1-flash-preview
        actual_model = model if model != "gemini" else "gemini-3.1-flash-preview"
        
        response = client.models.generate_content(
            model=actual_model,
            contents=prompt,
        )
        if response.text is None:
            raise RuntimeError(f"Gemini returned empty response for model {actual_model}")
        return response.text
    except Exception as e:
        logger.error(f"Error calling Gemini model '{model}': {e}")
        raise RuntimeError(f"Gemini API call failed for model '{model}': {e}") from e

    try:
        client_kwargs = {"vertexai": True}
        project = os.getenv("GOOGLE_CLOUD_PROJECT") or os.getenv("VERTEXAI_PROJECT")
        if project:
            client_kwargs["project"] = project
        location = os.getenv("GOOGLE_CLOUD_LOCATION") or os.getenv("VERTEXAI_LOCATION")
        if location:
            client_kwargs["location"] = location

        resolved_key = api_key or os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
        if resolved_key:
            client_kwargs["api_key"] = resolved_key

        client = genai.Client(**client_kwargs)
        response = client.models.generate_content(
            model=model,
            contents=prompt,
        )
        if response.text is None:
            raise RuntimeError(f"Gemini returned empty response for model {model}")
        return response.text
    except Exception as e:
        logger.error(f"Error calling Gemini model '{model}': {e}")
        raise RuntimeError(f"Gemini API call failed for model '{model}': {e}") from e


def _call_mistral(prompt: str, model: str, api_key: Optional[str] = None) -> str:
    """
    Invoke Mistral AI Chat Completion API.
    Supports models like mistral-medium-2505.
    """
    resolved_key = api_key or os.getenv("MISTRAL_API_KEY")
    if not resolved_key:
        raise ValueError(
            "Mistral API key not found. Please provide api_key or set MISTRAL_API_KEY environment variable."
        )

    try:
        # Support mistralai >= 1.0.0 (Mistral) and older versions (MistralClient)
        try:
            from mistralai import Mistral
            client = Mistral(api_key=resolved_key)
            response = client.chat.complete(
                model=model,
                messages=[{"role": "user", "content": prompt}],
            )
            content = response.choices[0].message.content
            if content is None:
                raise RuntimeError(f"Mistral returned empty response for model {model}")
            return content
        except ImportError:
            from mistralai.client import MistralClient
            from mistralai.models.chat_completion import ChatMessage
            client = MistralClient(api_key=resolved_key)
            response = client.chat(
                model=model,
                messages=[ChatMessage(role="user", content=prompt)],
            )
            content = response.choices[0].message.content
            if content is None:
                raise RuntimeError(f"Mistral returned empty response for model {model}")
            return content
    except Exception as e:
        logger.error(f"Error calling Mistral model '{model}': {e}")
        raise RuntimeError(f"Mistral API call failed for model '{model}': {e}") from e


def call_llm(prompt: str, model: str, api_key: Optional[str] = None) -> str:
    """
    Dispatch prompt to the appropriate LLM provider based on model name.

    Supported models:
    - OpenAI: gpt-4o, o4-mini (and other gpt/o-series models)
    - Anthropic: claude-sonnet-4-20250514 (and other claude models)
    - Gemini: gemini-3.1-flash-preview (via google-genai with vertexai=True)
    - Mistral: mistral-medium-2505 (and other mistral models)

    Args:
        prompt: The input prompt string.
        model: Model identifier.
        api_key: Optional API key override. If not provided, falls back to environment variables.

    Returns:
        The text response generated by the LLM.

    Raises:
        ValueError: If model is unsupported or API key is missing.
        RuntimeError: If the provider call fails.
    """
    model_lower = model.lower()

    # OpenAI routing
    if model in OPENAI_MODELS or "gpt" in model_lower or model_lower.startswith("o1") or model_lower.startswith("o3") or model_lower.startswith("o4") or "openai" in model_lower:
        return _call_openai(prompt=prompt, model=model, api_key=api_key)

    # Anthropic routing
    if model in ANTHROPIC_MODELS or "claude" in model_lower or "anthropic" in model_lower:
        return _call_anthropic(prompt=prompt, model=model, api_key=api_key)

    # Gemini routing
    if model in GEMINI_MODELS or "gemini" in model_lower:
        return _call_gemini(prompt=prompt, model=model, api_key=api_key)

    # Mistral routing
    if model in MISTRAL_MODELS or "mistral" in model_lower or "codestral" in model_lower or "mixtral" in model_lower:
        return _call_mistral(prompt=prompt, model=model, api_key=api_key)

    raise ValueError(
        f"Unsupported model: '{model}'. Supported models include: "
        f"OpenAI ({', '.join(sorted(OPENAI_MODELS))}), "
        f"Anthropic ({', '.join(sorted(ANTHROPIC_MODELS))}), "
        f"Gemini ({', '.join(sorted(GEMINI_MODELS))}), "
        f"Mistral ({', '.join(sorted(MISTRAL_MODELS))})."
    )
