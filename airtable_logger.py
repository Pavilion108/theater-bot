import os
import requests
import datetime
import logging

log = logging.getLogger("TheaterBot")

def log_to_airtable(data):
    """
    Sends the extracted media data to Airtable.
    Requires AIRTABLE_API_KEY, AIRTABLE_BASE_ID, and AIRTABLE_TABLE_NAME in .env.
    """
    api_key = os.getenv("AIRTABLE_API_KEY")
    base_id = os.getenv("AIRTABLE_BASE_ID")
    table_name = os.getenv("AIRTABLE_TABLE_NAME")
    
    if not api_key or not base_id or not table_name:
        log.warning("Airtable environment variables not fully configured. Skipping Airtable sync.")
        return False, "Airtable API keys are missing in Render settings."
        
    # Handle the case where the user pasted the full Airtable URL in AIRTABLE_BASE_ID
    if "airtable.com" in base_id:
        # Example format: https://airtable.com/appXXXXXXXX/tblYYYYYYYY
        parts = base_id.split("/")
        for p in parts:
            if p.startswith("app"):
                base_id = p
                break

    url = f"https://api.airtable.com/v0/{base_id}/{table_name}"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }
    
    payload = {
        "records": [
            {
                "fields": {
                    "Timestamp": datetime.datetime.now().isoformat(),
                    "Filename": data.get("filename", ""),
                    "File Type": data.get("file_type", ""),
                    "Summary": data.get("summary", ""),
                    "Entities": data.get("entities", "")
                }
            }
        ],
        "typecast": True  # Helps with automatic type conversion
    }
    
    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=15)
        resp.raise_for_status()
        log.info("Successfully synced media intel to Airtable!")
        return True, "Data successfully logged to Airtable."
    except requests.exceptions.RequestException as e:
        error_msg = str(e)
        if hasattr(e, 'response') and e.response is not None:
            try:
                error_data = e.response.json()
                if "error" in error_data:
                    error_obj = error_data["error"]
                    if isinstance(error_obj, dict) and "message" in error_obj:
                        error_msg = f"{error_obj.get('type')}: {error_obj.get('message')}"
                    else:
                        error_msg = str(error_obj)
            except Exception:
                error_msg = e.response.text
        
        log.error(f"Failed to log to Airtable: {error_msg}")
        return False, f"Airtable API Error: {error_msg}"

