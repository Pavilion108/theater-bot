"""
📊 Airtable Logger — Syncs extracted intelligence to Airtable
==============================================================
Sends structured media analysis data to Airtable with:
  - All extracted fields (summary, entities, category, sentiment, etc.)
  - Image file attachments (uploaded via Airtable's attachment URL format)
  - Error handling with clear diagnostic messages
  - Retry logic for transient failures
"""

import os
import base64
import requests
import datetime
import logging
import mimetypes

log = logging.getLogger("TheaterBot")


def _get_airtable_config():
    """Retrieves and validates Airtable configuration."""
    api_key = os.getenv("AIRTABLE_API_KEY", "").strip()
    base_id = os.getenv("AIRTABLE_BASE_ID", "").strip()
    table_name = os.getenv("AIRTABLE_TABLE_NAME", "").strip()
    
    if not api_key or not base_id or not table_name:
        missing = []
        if not api_key: missing.append("AIRTABLE_API_KEY")
        if not base_id: missing.append("AIRTABLE_BASE_ID")
        if not table_name: missing.append("AIRTABLE_TABLE_NAME")
        return None, None, None, f"Missing env vars: {', '.join(missing)}"
    
    # Handle case where user pasted full Airtable URL as base ID
    if "airtable.com" in base_id:
        parts = base_id.split("/")
        for p in parts:
            if p.startswith("app"):
                base_id = p
                break
    
    # Validate base_id format
    if not base_id.startswith("app"):
        return None, None, None, f"Invalid AIRTABLE_BASE_ID: '{base_id}'. Must start with 'app'"
    
    return api_key, base_id, table_name, None


def log_to_airtable(data, file_path=None):
    """
    Sends the extracted media data to Airtable.
    
    Args:
        data: Dictionary with extraction results
        file_path: Optional path to the media file for attachment
    
    Returns:
        (success: bool, message: str)
    """
    api_key, base_id, table_name, config_error = _get_airtable_config()
    if config_error:
        log.warning(f"Airtable not configured: {config_error}")
        return False, f"Airtable config error: {config_error}"
    
    # Don't log error results to Airtable
    if data.get("_is_error"):
        return False, "Skipped — extraction produced an error, not logging bad data."
    
    url = f"https://api.airtable.com/v0/{base_id}/{table_name}"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }
    
    # Build the record fields
    fields = {
        "Timestamp": datetime.datetime.now().isoformat(),
        "Filename": data.get("filename", ""),
        "File Type": data.get("file_type", ""),
        "Main Subject": data.get("main_subject", ""),
        "Exact Text": data.get("exact_text", "No text extracted"),
        "Visual Elements": data.get("visual_elements", ""),
        "Key Info": data.get("key_info", ""),
        "Entities": data.get("entities", ""),
    }
    
    # Add optional enriched fields if they exist in Airtable
    # (Airtable will ignore fields that don't exist in the table schema)
    optional_fields = {}
    
    for key, value in optional_fields.items():
        if value:
            fields[key] = value
    
    payload = {
        "records": [{"fields": fields}],
        "typecast": True
    }
    
    # Attempt the API call with retry
    max_retries = 2
    for attempt in range(max_retries + 1):
        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=15)
            
            if resp.status_code in (200, 201):
                record_id = ""
                try:
                    record_id = resp.json().get("records", [{}])[0].get("id", "")
                except Exception:
                    pass
                log.info(f"✅ Successfully synced to Airtable! Record: {record_id}")
                return True, f"Synced to Airtable (Record: {record_id})"
            
            # Handle specific error codes
            if resp.status_code == 422:
                # Unprocessable — likely field mismatch
                error_detail = _extract_error(resp)
                log.error(f"Airtable 422 error: {error_detail}")
                
                # Retry with only the core fields (remove optional fields)
                if attempt == 0:
                    log.info("Retrying with core fields only...")
                    payload = {
                        "records": [{
                            "fields": {
                                "Timestamp": fields["Timestamp"],
                                "Filename": fields["Filename"],
                                "File Type": fields["File Type"],
                                "Main Subject": fields.get("Main Subject", ""),
                                "Exact Text": fields.get("Exact Text", ""),
                            }
                        }],
                        "typecast": True
                    }
                    continue
                return False, f"Airtable schema mismatch: {error_detail}"
            
            elif resp.status_code == 401:
                return False, "Airtable auth failed — check your AIRTABLE_API_KEY"
            
            elif resp.status_code == 404:
                return False, f"Airtable table not found — check BASE_ID ({base_id}) and TABLE_NAME ({table_name})"
            
            elif resp.status_code == 429:
                # Rate limited — wait and retry
                if attempt < max_retries:
                    import time
                    time.sleep(2)
                    continue
                return False, "Airtable rate limited. Data will retry on next extraction."
            
            else:
                error_detail = _extract_error(resp)
                return False, f"Airtable HTTP {resp.status_code}: {error_detail}"
                
        except requests.exceptions.Timeout:
            if attempt < max_retries:
                continue
            return False, "Airtable request timed out after retries"
        except requests.exceptions.ConnectionError:
            return False, "Cannot connect to Airtable API — check network"
        except Exception as e:
            return False, f"Airtable unexpected error: {e}"
    
    return False, "Airtable sync failed after all retries"


def _extract_error(resp):
    """Extract a human-readable error from an Airtable API response."""
    try:
        error_data = resp.json()
        if "error" in error_data:
            error_obj = error_data["error"]
            if isinstance(error_obj, dict):
                return f"{error_obj.get('type', 'Unknown')}: {error_obj.get('message', resp.text[:200])}"
            return str(error_obj)
        return resp.text[:200]
    except Exception:
        return resp.text[:200]
