import os
import logging
import requests
import base64

from gemini_web_scraper import query_gemini_web
from airtable_logger import log_to_airtable

log = logging.getLogger("MediaIntel")

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

def analyze_media(file_path, file_type, status_callback=None):
    """Uses Gemini Web API via headless browser to extract details from an image or video."""
    filename = os.path.basename(file_path)
    
    log.info(f"Processing media with Gemini headless browser: {filename}")
    if status_callback: status_callback(f"🧠 Initializing AI extraction for {filename}...")
    try:
        prompt = (
            "Analyze this media and extract intelligence. Return the result STRICTLY as a JSON object with these keys: "
            "'summary' (2-3 sentences), 'category' (News, Politics, Finance, Tech, Entertainment, Sports, Health, or Other), "
            "'entities' (comma separated list of people, organizations, locations), "
            "'sentiment' (Positive, Negative, Neutral, or Mixed), "
            "'key_data' (any important numbers, dates, or metrics, else 'None'), "
            "'source' (the likely source of this media, else 'Unknown'), "
            "'action_items' (1-2 bullet points if applicable, else 'None')."
        )
        response_text = query_gemini_web(file_path, prompt, status_callback=status_callback)
        
        if status_callback: status_callback("✅ Response received. Standardizing and logging data...")
        
        import json
        import re
        
        # Try to parse JSON from the response text
        result_data = {
            "filename": filename,
            "file_type": file_type,
            "summary": response_text[:1000] if len(response_text) > 1000 else response_text,
            "entities": "None",
            "category": "Other",
            "sentiment": "Neutral",
            "key_data": "None",
            "source": "Unknown",
            "action_items": "None"
        }
        
        try:
            match = re.search(r'\{.*\}', response_text.replace('\n', ' '), re.DOTALL)
            if match:
                parsed = json.loads(match.group(0))
                for k in ["summary", "entities", "category", "sentiment", "key_data", "source", "action_items"]:
                    if k in parsed:
                        result_data[k] = str(parsed[k])
        except:
            pass # Fallback to the raw text in summary field
            
        # Log to Airtable
        airtable_success, airtable_msg = log_to_airtable(result_data)
        result_data["airtable_status"] = airtable_msg
        
        return result_data
    except Exception as e:
        log.error(f"Gemini Web scraper error: {e}")
        return {
            "filename": filename,
            "file_type": file_type,
            "summary": f"Error during AI extraction: {e}",
            "entities": "Error",
            "_is_error": True,
            "airtable_status": "Skipped (Error)"
        }

def generate_text_summary(prompt):
    """Uses available AI APIs to generate a daily summary from Excel text data."""
    messages = [{"role": "user", "content": prompt}]
    
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

    openrouter_key = os.getenv("OPENROUTER_API_KEY")
    if openrouter_key:
        try:
            resp = requests.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={"Authorization": f"Bearer {openrouter_key}"},
                json={"model": "mistralai/mistral-nemo", "messages": messages},
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

    api_key = os.getenv("GEMINI_API_KEY")
    if api_key and genai:
        try:
            genai.configure(api_key=api_key)
            model = genai.GenerativeModel('gemini-1.5-flash')
            return model.generate_content(prompt).text
        except: pass
        
    return "Failed to generate summary: No valid API keys available."
