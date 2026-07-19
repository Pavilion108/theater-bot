"""
🧠 AI Vision Extractor — Multi-Provider API Engine
=====================================================
Replaces the old Selenium-based Gemini web scraper with direct API calls.
Supports multiple AI providers with automatic fallback:
  1. OpenRouter (Gemini 2.0 Flash, Qwen2-VL, etc.)
  2. NVIDIA NIM (LLaVA, Cosmos)
  3. Google Gemini API (native)

This is infinitely more reliable than browser automation.
"""

import os
import base64
import json
import logging
import mimetypes
import requests

log = logging.getLogger("TheaterBot")

# ──────────────────────────────────────────────────────────────────────
# Smart Prompt — Extracts structured intelligence from any media
# ──────────────────────────────────────────────────────────────────────
EXTRACTION_PROMPT = """You are Agent-T, an AI intelligence extractor. Analyze this image/media and extract ALL useful information.

Provide your response in this EXACT format (plain text, no markdown):

SUMMARY: [2-3 sentence summary of what this image shows]
CATEGORY: [One of: News, Politics, Finance, Technology, Sports, Entertainment, Health, Education, Business, Science, Social, Other]
ENTITIES: [Comma-separated list of key people, organizations, places, numbers, dates mentioned]
SENTIMENT: [Positive, Negative, Neutral, or Mixed]
KEY_DATA: [Any specific numbers, statistics, prices, dates, or factual data points]
SOURCE: [If visible - news channel, website, social media account, or "Unknown"]
LANGUAGE: [Primary language of text in the image]
ACTION_ITEMS: [Any calls to action, deadlines, or important follow-ups, or "None"]

Be thorough. Extract every piece of useful information visible in the image."""

# ──────────────────────────────────────────────────────────────────────
# Provider 1: OpenRouter (Best — supports many vision models for free)
# ──────────────────────────────────────────────────────────────────────
def _query_openrouter(image_b64: str, mime_type: str, prompt: str) -> str | None:
    """Query OpenRouter API with vision model."""
    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        return None
    
    # Priority order of vision models (free or cheap tiers first)
    models = [
        "google/gemini-2.0-flash-exp:free",
        "google/gemma-3-27b-it:free",
        "qwen/qwen2.5-vl-72b-instruct:free",
        "meta-llama/llama-4-scout:free",
        "google/gemini-2.0-flash-001",
        "qwen/qwen-2.5-vl-7b-instruct",
    ]
    
    for model in models:
        try:
            log.info(f"Trying OpenRouter model: {model}")
            resp = requests.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                    "HTTP-Referer": "https://agent-t.bot",
                    "X-Title": "Agent-T Intelligence Bot"
                },
                json={
                    "model": model,
                    "messages": [
                        {
                            "role": "user",
                            "content": [
                                {
                                    "type": "image_url",
                                    "image_url": {
                                        "url": f"data:{mime_type};base64,{image_b64}"
                                    }
                                },
                                {
                                    "type": "text",
                                    "text": prompt
                                }
                            ]
                        }
                    ],
                    "max_tokens": 1500,
                    "temperature": 0.3
                },
                timeout=60
            )
            
            if resp.status_code == 200:
                data = resp.json()
                content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
                if content and len(content) > 20:
                    log.info(f"✅ OpenRouter success with {model}")
                    return content
                else:
                    log.warning(f"OpenRouter {model} returned empty/short response")
            else:
                error_info = resp.text[:200]
                log.warning(f"OpenRouter {model} returned {resp.status_code}: {error_info}")
                # If rate limited, try next model
                if resp.status_code == 429:
                    continue
                # If auth error, no point trying other models on same provider
                if resp.status_code in (401, 403):
                    return None
        except requests.exceptions.Timeout:
            log.warning(f"OpenRouter {model} timed out")
            continue
        except Exception as e:
            log.warning(f"OpenRouter {model} error: {e}")
            continue
    
    return None


# ──────────────────────────────────────────────────────────────────────
# Provider 2: NVIDIA NIM API
# ──────────────────────────────────────────────────────────────────────
def _query_nvidia(image_b64: str, mime_type: str, prompt: str) -> str | None:
    """Query NVIDIA NIM API with vision model."""
    api_key = os.getenv("NVIDIA_API_KEY")
    if not api_key or not api_key.startswith("nvapi-"):
        return None
    
    try:
        log.info("Trying NVIDIA NIM API...")
        resp = requests.post(
            "https://integrate.api.nvidia.com/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json"
            },
            json={
                "model": "microsoft/phi-4-multimodal-instruct",
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:{mime_type};base64,{image_b64}"
                                }
                            },
                            {
                                "type": "text",
                                "text": prompt
                            }
                        ]
                    }
                ],
                "max_tokens": 1500,
                "temperature": 0.3
            },
            timeout=60
        )
        
        if resp.status_code == 200:
            content = resp.json()["choices"][0]["message"]["content"]
            if content and len(content) > 20:
                log.info("✅ NVIDIA NIM success")
                return content
    except Exception as e:
        log.warning(f"NVIDIA NIM error: {e}")
    
    return None


# ──────────────────────────────────────────────────────────────────────
# Provider 3: Google Gemini API (native)
# ──────────────────────────────────────────────────────────────────────
def _query_gemini_native(image_b64: str, mime_type: str, prompt: str) -> str | None:
    """Query Google Gemini API directly."""
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        return None
    
    try:
        log.info("Trying Google Gemini API...")
        resp = requests.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={api_key}",
            headers={"Content-Type": "application/json"},
            json={
                "contents": [{
                    "parts": [
                        {
                            "inline_data": {
                                "mime_type": mime_type,
                                "data": image_b64
                            }
                        },
                        {"text": prompt}
                    ]
                }],
                "generationConfig": {
                    "temperature": 0.3,
                    "maxOutputTokens": 1500
                }
            },
            timeout=60
        )
        
        if resp.status_code == 200:
            data = resp.json()
            content = data.get("candidates", [{}])[0].get("content", {}).get("parts", [{}])[0].get("text", "")
            if content and len(content) > 20:
                log.info("✅ Google Gemini API success")
                return content
    except Exception as e:
        log.warning(f"Google Gemini API error: {e}")
    
    return None


# ──────────────────────────────────────────────────────────────────────
# Main Entry Point — Replaces the old query_gemini_web function
# ──────────────────────────────────────────────────────────────────────
def query_gemini_web(file_path: str, prompt: str, status_callback=None) -> str:
    """
    Analyze an image using AI vision APIs.
    
    This replaces the old Selenium-based Gemini web scraper.
    Uses direct API calls with automatic fallback across providers.
    
    Args:
        file_path: Path to the image/media file
        prompt: The extraction prompt (uses smart default if generic)
        status_callback: Optional function to send status updates
    
    Returns:
        Extracted text content from the image
    """
    # Use our smart extraction prompt instead of the generic one
    if not prompt or prompt == "Extract a summary, category, and key numbers/entities from this media. Format as plain text without markdown.":
        prompt = EXTRACTION_PROMPT
    
    # Step 1: Read and encode the image
    if status_callback:
        status_callback("📖 Reading and encoding image...")
    
    try:
        with open(file_path, "rb") as f:
            image_data = f.read()
        
        # Check file size (most APIs limit to ~20MB for base64)
        file_size_mb = len(image_data) / (1024 * 1024)
        if file_size_mb > 15:
            return "Error: Image file is too large (>15MB). Please send a smaller image."
        
        image_b64 = base64.b64encode(image_data).decode("utf-8")
        
        # Detect MIME type
        mime_type, _ = mimetypes.guess_type(file_path)
        if not mime_type or not mime_type.startswith("image"):
            # Default to JPEG for unknown types (Telegram often strips extensions)
            mime_type = "image/jpeg"
        
        log.info(f"Image encoded: {file_size_mb:.1f}MB, type: {mime_type}")
    except Exception as e:
        return f"Error: Could not read image file: {e}"
    
    # Step 2: Try each provider in order
    if status_callback:
        status_callback("🧠 Sending to AI for analysis...")
    
    providers = [
        ("OpenRouter", _query_openrouter),
        ("NVIDIA NIM", _query_nvidia),
        ("Google Gemini", _query_gemini_native),
    ]
    
    errors = []
    for name, provider_fn in providers:
        if status_callback:
            status_callback(f"🔄 Trying {name}...")
        
        try:
            result = provider_fn(image_b64, mime_type, prompt)
            if result:
                return result
            errors.append(f"{name}: No valid response")
        except Exception as e:
            errors.append(f"{name}: {e}")
            log.error(f"Provider {name} failed: {e}")
    
    # All providers failed
    error_summary = " | ".join(errors)
    return f"Error: All AI providers failed. Details: {error_summary}"


def parse_extraction_result(raw_text: str) -> dict:
    """
    Parses the structured extraction result into a dictionary.
    
    Args:
        raw_text: The raw text response from the AI
    
    Returns:
        Dictionary with parsed fields
    """
    result = {
        "summary": "",
        "category": "Other",
        "entities": "",
        "sentiment": "Neutral",
        "key_data": "",
        "source": "Unknown",
        "language": "English",
        "action_items": "None"
    }
    
    if raw_text.startswith("Error:"):
        result["summary"] = raw_text
        return result
    
    # Parse line-by-line
    lines = raw_text.strip().split("\n")
    for line in lines:
        line = line.strip()
        if not line:
            continue
        
        # Match KEY: VALUE format
        for key in ["SUMMARY", "CATEGORY", "ENTITIES", "SENTIMENT", "KEY_DATA", "SOURCE", "LANGUAGE", "ACTION_ITEMS"]:
            if line.upper().startswith(f"{key}:"):
                value = line[len(key) + 1:].strip()
                result[key.lower()] = value
                break
    
    # If no structured parsing worked, use the whole text as summary
    if not result["summary"] and raw_text:
        result["summary"] = raw_text[:500]
    
    return result
