import os
import logging
import requests

try:
    import google.generativeai as genai
except ImportError:
    genai = None

log = logging.getLogger("MediaIntel")

def download_telegram_file(file_id, bot_token, save_path):
    url = f"https://api.telegram.org/bot{bot_token}/getFile?file_id={file_id}"
    resp = requests.get(url).json()
    if not resp.get('ok'): return None
    file_path = resp['result']['file_path']
    
    dl_url = f"https://api.telegram.org/file/bot{bot_token}/{file_path}"
    file_data = requests.get(dl_url).content
    
    os.makedirs(os.path.dirname(save_path), exist_ok=True)
    with open(save_path, 'wb') as f:
        f.write(file_data)
    return save_path

def analyze_media(file_path, file_type):
    """Uses Gemini API to extract details from an image or video."""
    filename = os.path.basename(file_path)
    
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key or not genai:
        return {
            "filename": filename,
            "file_type": file_type,
            "summary": "Basic logging (GEMINI_API_KEY not set or library missing).",
            "entities": "N/A"
        }
        
    try:
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel('gemini-1.5-flash')
        
        # Upload to Gemini File API
        sample_file = genai.upload_file(path=file_path)
        prompt = "Extract a summary, category, and key numbers/entities from this media. Format as plain text without markdown."
        
        response = model.generate_content([sample_file, prompt])
        
        # Delete file from Gemini after processing to save space
        genai.delete_file(sample_file.name)
        
        return {
            "filename": filename,
            "file_type": file_type,
            "summary": response.text[:200] + "...",  # Truncated for excel
            "entities": "Extracted via Gemini AI"
        }
    except Exception as e:
        log.error(f"Gemini API error: {e}")
        return {
            "filename": filename,
            "file_type": file_type,
            "summary": f"Error during AI extraction: {e}",
            "entities": "Error"
        }
