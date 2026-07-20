import os
import logging
import requests
import base64
import json
import re
import mimetypes

from airtable_logger import log_to_airtable

log = logging.getLogger("MediaIntel")

# Vision models ranked by capability for image analysis (not generation)
VISION_MODELS = [
    "google/gemini-3.1-flash-lite-image",
    "google/gemini-3.1-flash-image",
    "google/gemini-3-pro-image",
    "x-ai/grok-imagine-image-quality",
    "sourceful/riverflow-v2.5-pro",
    "recraft/recraft-v4.1-pro-vector",
    "openrouter/free"
]

INTEL_PROMPT = (
    "Analyze this media and extract intelligence. Return the result STRICTLY as a JSON object with these keys: "
    "'summary' (2-3 sentences), 'category' (News, Politics, Finance, Tech, Entertainment, Sports, Health, or Other), "
    "'entities' (comma separated list of people, organizations, locations), "
    "'sentiment' (Positive, Negative, Neutral, or Mixed), "
    "'key_data' (any important numbers, dates, or metrics, else 'None'), "
    "'source' (the likely source of this media, else 'Unknown'), "
    "'action_items' (1-2 bullet points if applicable, else 'None'). "
    "Return ONLY the JSON object, no markdown fences, no explanation."
)


def download_telegram_file(file_id, bot_token, save_path):
    url = f"https://api.telegram.org/bot{bot_token}/getFile?file_id={file_id}"
    resp = requests.get(url, timeout=15).json()
    if not resp.get('ok'): return None
    file_path = resp['result']['file_path']
    
    dl_url = f"https://api.telegram.org/file/bot{bot_token}/{file_path}"
    file_data = requests.get(dl_url, timeout=30).content
    
    os.makedirs(os.path.dirname(save_path), exist_ok=True)
    with open(save_path, 'wb') as f:
        f.write(file_data)
    return save_path


def encode_image(image_path):
    """Encodes image to base64 for vision APIs."""
    with open(image_path, "rb") as image_file:
        return base64.b64encode(image_file.read()).decode('utf-8')


def _get_mime_type(file_path):
    """Detect MIME type from file extension or content."""
    mime, _ = mimetypes.guess_type(file_path)
    if mime:
        return mime
    # Fallback: check magic bytes
    try:
        with open(file_path, 'rb') as f:
            header = f.read(16)
        if header[:4] == b'\x89PNG':
            return 'image/png'
        if header[:2] == b'\xff\xd8':
            return 'image/jpeg'
        if header[:4] == b'GIF8':
            return 'image/gif'
        if header[:4] == b'RIFF' and header[8:12] == b'WEBP':
            return 'image/webp'
    except:
        pass
    return 'image/jpeg'  # Safe default


def _call_openrouter_vision(api_key, image_b64, mime_type, prompt, model):
    """Make a single OpenRouter vision API call."""
    resp = requests.post(
        "https://openrouter.ai/api/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://jackbot-24-7.onrender.com",
            "X-Title": "Agent-T Intelligence Bot",
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
                            "text": prompt,
                        }
                    ]
                }
            ],
            "max_tokens": 1024,
            "temperature": 0.3,
        },
        timeout=60
    )
    
    if resp.status_code == 200:
        data = resp.json()
        content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
        model_used = data.get("model", model)
        return content, model_used
    else:
        log.warning(f"OpenRouter {model} returned {resp.status_code}: {resp.text[:200]}")
        return None, None


def analyze_media(file_path, file_type, status_callback=None):
    """Uses OpenRouter Vision API to extract intelligence from an image."""
    filename = os.path.basename(file_path)
    
    log.info(f"Processing media via OpenRouter Vision API: {filename}")
    if status_callback: status_callback("🧠 Encoding image for AI vision analysis...")
    
    try:
        # Encode the image
        image_b64 = encode_image(file_path)
        mime_type = _get_mime_type(file_path)
        
        openrouter_key = os.getenv("OPENROUTER_API_KEY")
        if not openrouter_key:
            return {
                "filename": filename,
                "file_type": file_type,
                "summary": "Error: OPENROUTER_API_KEY not set in environment variables.",
                "entities": "Error",
                "_is_error": True,
                "airtable_status": "Skipped (No API Key)"
            }
        
        # Try each vision model in order until one works
        response_text = None
        model_used = None
        last_error = "Unknown Error"
        
        for model in VISION_MODELS:
            if status_callback: status_callback(f"🔍 Analyzing with `{model.split('/')[-1]}`...")
            
            try:
                response_text, model_used = _call_openrouter_vision(
                    openrouter_key, image_b64, mime_type, INTEL_PROMPT, model
                )
                if response_text and len(response_text) > 10:
                    log.info(f"Got response from {model_used} ({len(response_text)} chars)")
                    break
                else:
                    last_error = f"Model {model} returned empty/invalid response"
                    response_text = None
            except Exception as e:
                log.warning(f"Model {model} failed: {e}")
                last_error = str(e)
                continue
        
        if not response_text:
            return {
                "filename": filename,
                "file_type": file_type,
                "summary": f"❌ OpenRouter API completely failed. Last error: {last_error}",
                "entities": "Error",
                "_is_error": True,
                "airtable_status": "Failed"
            }
        
        if status_callback: status_callback(f"✅ AI response received from `{model_used.split('/')[-1] if model_used else 'unknown'}`. Parsing...")
        
        # Parse the response
        result_data = {
            "filename": filename,
            "file_type": file_type,
            "summary": response_text[:1000] if len(response_text) > 1000 else response_text,
            "entities": "None",
            "category": "Other",
            "sentiment": "Neutral",
            "key_data": "None",
            "source": "Unknown",
            "action_items": "None",
            "model_used": model_used or "unknown",
        }
        
        try:
            # Strip markdown code fences if present
            cleaned = response_text.strip()
            if cleaned.startswith("```"):
                cleaned = re.sub(r'^```(?:json)?\s*', '', cleaned)
                cleaned = re.sub(r'\s*```$', '', cleaned)
            
            match = re.search(r'\{.*\}', cleaned.replace('\n', ' '), re.DOTALL)
            if match:
                parsed = json.loads(match.group(0))
                for k in ["summary", "entities", "category", "sentiment", "key_data", "source", "action_items"]:
                    if k in parsed:
                        result_data[k] = str(parsed[k])
        except:
            pass  # Fallback to the raw text in summary field
            
        # Log to Airtable
        airtable_success, airtable_msg = log_to_airtable(result_data)
        result_data["airtable_status"] = airtable_msg
        
        return result_data
    except Exception as e:
        log.error(f"OpenRouter Vision API error: {e}", exc_info=True)
        return {
            "filename": filename,
            "file_type": file_type,
            "summary": f"Error during AI extraction: {e}",
            "entities": "Error",
            "_is_error": True,
            "airtable_status": "Skipped (Error)"
        }


def generate_text_summary(prompt):
    """Uses available AI APIs to generate a text response, with fallback."""
    messages = [{"role": "user", "content": prompt}]
    
    openrouter_key = os.getenv("OPENROUTER_API_KEY")
    if openrouter_key:
        TEXT_MODELS = [
            "google/gemini-2.5-flash",
            "meta-llama/llama-3.1-8b-instruct",
            "anthropic/claude-3-haiku",
            "openrouter/free"
        ]
        
        for model in TEXT_MODELS:
            try:
                resp = requests.post(
                    "https://openrouter.ai/api/v1/chat/completions",
                    headers={"Authorization": f"Bearer {openrouter_key}"},
                    json={"model": model, "messages": messages, "max_tokens": 1024},
                    timeout=30
                )
                if resp.status_code == 200: 
                    return resp.json()["choices"][0]["message"]["content"]
                else:
                    log.warning(f"OpenRouter text model {model} failed: {resp.status_code} - {resp.text}")
            except Exception as e: 
                log.warning(f"Exception calling {model}: {e}")

    # Fallbacks for other providers
    nvidia_key = os.getenv("NVIDIA_API_KEY") or os.getenv("KIMI_API_KEY")
    if nvidia_key and nvidia_key.startswith("nvapi-"):
        try:
            resp = requests.post(
                "https://integrate.api.nvidia.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {nvidia_key}"},
                json={"model": "meta/llama-3.1-8b-instruct", "messages": messages, "max_tokens": 1024},
                timeout=30
            )
            if resp.status_code == 200: return resp.json()["choices"][0]["message"]["content"]
        except: pass
        
    kimi_key = os.getenv("KIMI_API_KEY")
    if kimi_key and not kimi_key.startswith("nvapi-"):
        try:
            resp = requests.post(
                "https://api.moonshot.cn/v1/chat/completions",
                headers={"Authorization": f"Bearer {kimi_key}"},
                json={"model": "moonshot-v1-8k", "messages": messages},
                timeout=30
            )
            if resp.status_code == 200: return resp.json()["choices"][0]["message"]["content"]
        except: pass

    return "Failed to generate summary: No valid API keys available."
