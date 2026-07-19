"""
📸 Media Intelligence Module — Smart Data Extraction Pipeline
==============================================================
Handles the full pipeline:
  1. Download media from Telegram
  2. Send to AI Vision API for extraction
  3. Parse structured data
  4. Log to Airtable (with image attachment)
  5. Log to local Excel backup
"""

import os
import logging
import requests
import base64

from gemini_web_scraper import query_gemini_web, parse_extraction_result
from airtable_logger import log_to_airtable

log = logging.getLogger("MediaIntel")


def download_telegram_file(file_id, bot_token, save_path):
    """Downloads a file from Telegram servers."""
    url = f"https://api.telegram.org/bot{bot_token}/getFile?file_id={file_id}"
    try:
        resp = requests.get(url, timeout=15).json()
        if not resp.get('ok'):
            log.error(f"Telegram getFile failed: {resp}")
            return None
        file_path = resp['result']['file_path']
        
        dl_url = f"https://api.telegram.org/file/bot{bot_token}/{file_path}"
        file_data = requests.get(dl_url, timeout=30).content
        
        os.makedirs(os.path.dirname(save_path), exist_ok=True)
        with open(save_path, 'wb') as f:
            f.write(file_data)
        
        log.info(f"Downloaded {len(file_data)} bytes to {save_path}")
        return save_path
    except Exception as e:
        log.error(f"Failed to download Telegram file: {e}")
        return None


def encode_image(image_path):
    """Encodes image to base64 for vision APIs."""
    with open(image_path, "rb") as image_file:
        return base64.b64encode(image_file.read()).decode('utf-8')


def analyze_media(file_path, file_type, status_callback=None):
    """
    Full media analysis pipeline using AI Vision APIs.
    
    Returns a dictionary with all extracted data fields.
    """
    filename = os.path.basename(file_path)
    
    log.info(f"Processing media: {filename} (type: {file_type})")
    if status_callback:
        status_callback(f"🧠 Initializing AI extraction for {filename}...")
    
    try:
        # Step 1: Send to AI for extraction
        response_text = query_gemini_web(file_path, "", status_callback=status_callback)
        
        # Step 2: Check if we got an error
        is_error = response_text.startswith("Error:")
        
        if is_error:
            log.error(f"AI extraction failed: {response_text}")
            if status_callback:
                status_callback(f"⚠️ AI extraction issue: {response_text[:100]}")
            return {
                "filename": filename,
                "file_type": file_type,
                "summary": response_text,
                "entities": "Extraction failed",
                "category": "Error",
                "sentiment": "N/A",
                "key_data": "",
                "source": "",
                "airtable_status": "Skipped — no valid data to log",
                "_is_error": True
            }
        
        # Step 3: Parse structured response
        if status_callback:
            status_callback("✅ Response received. Parsing and standardizing data...")
        
        parsed = parse_extraction_result(response_text)
        
        result_data = {
            "filename": filename,
            "file_type": file_type,
            "summary": parsed.get("summary", response_text[:500]),
            "entities": parsed.get("entities", "Extracted via AI Vision"),
            "category": parsed.get("category", "Other"),
            "sentiment": parsed.get("sentiment", "Neutral"),
            "key_data": parsed.get("key_data", ""),
            "source": parsed.get("source", "Unknown"),
            "language": parsed.get("language", ""),
            "action_items": parsed.get("action_items", "None"),
            "_is_error": False
        }
        
        # Step 4: Log to Airtable (only if extraction succeeded)
        if status_callback:
            status_callback("📊 Syncing to Airtable...")
        
        airtable_success, airtable_msg = log_to_airtable(result_data, file_path=file_path)
        result_data["airtable_status"] = airtable_msg
        
        return result_data
        
    except Exception as e:
        log.error(f"Media analysis pipeline error: {e}", exc_info=True)
        return {
            "filename": filename,
            "file_type": file_type,
            "summary": f"Pipeline error: {e}",
            "entities": "Error",
            "category": "Error",
            "airtable_status": "Skipped — pipeline error",
            "_is_error": True
        }


def generate_text_summary(prompt):
    """Uses available AI APIs to generate a daily summary from text data."""
    messages = [{"role": "user", "content": prompt}]
    
    # Try OpenRouter first (most likely to work)
    openrouter_key = os.getenv("OPENROUTER_API_KEY")
    if openrouter_key:
        try:
            resp = requests.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {openrouter_key}",
                    "Content-Type": "application/json"
                },
                json={
                    "model": "google/gemini-2.0-flash-exp:free",
                    "messages": messages,
                    "max_tokens": 2000
                },
                timeout=30
            )
            if resp.status_code == 200:
                return resp.json()["choices"][0]["message"]["content"]
        except Exception as e:
            log.warning(f"OpenRouter text summary failed: {e}")
    
    # Try NVIDIA
    nvidia_key = os.getenv("NVIDIA_API_KEY") or os.getenv("KIMI_API_KEY")
    if nvidia_key and nvidia_key.startswith("nvapi-"):
        try:
            resp = requests.post(
                "https://integrate.api.nvidia.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {nvidia_key}"},
                json={"model": "meta/llama-3.1-8b-instruct", "messages": messages, "max_tokens": 1024},
                timeout=30
            )
            if resp.status_code == 200:
                return resp.json()["choices"][0]["message"]["content"]
        except Exception:
            pass
        
    # Try Kimi / Moonshot
    kimi_key = os.getenv("KIMI_API_KEY")
    if kimi_key and not kimi_key.startswith("nvapi-"):
        try:
            resp = requests.post(
                "https://api.moonshot.cn/v1/chat/completions",
                headers={"Authorization": f"Bearer {kimi_key}"},
                json={"model": "moonshot-v1-8k", "messages": messages},
                timeout=30
            )
            if resp.status_code == 200:
                return resp.json()["choices"][0]["message"]["content"]
        except Exception:
            pass

    # Try Google Gemini API
    api_key = os.getenv("GEMINI_API_KEY")
    if api_key:
        try:
            resp = requests.post(
                f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={api_key}",
                headers={"Content-Type": "application/json"},
                json={
                    "contents": [{"parts": [{"text": prompt}]}],
                    "generationConfig": {"maxOutputTokens": 2000}
                },
                timeout=30
            )
            if resp.status_code == 200:
                return resp.json()["candidates"][0]["content"]["parts"][0]["text"]
        except Exception:
            pass
        
    return "Failed to generate summary: No valid API keys available or all providers timed out."
