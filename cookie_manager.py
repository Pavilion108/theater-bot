"""
🍪 Cookie Manager — Import Chrome cookies for authenticated browsing.

Provides a simple way for users to export their Chrome cookies and inject
them into the Playwright browser session, making the headless browser appear
as a logged-in real user.

PERSISTENT STORAGE: Cookies are also backed up to a Render environment variable
so they survive server restarts and redeploys.
"""

import base64
import json
import logging
import os
import re
import requests
from pathlib import Path

log = logging.getLogger("TheaterBot")

COOKIES_DIR = Path(__file__).parent / "data"
COOKIES_FILE = COOKIES_DIR / "cookies.json"

# Render API for persistent cookie backup
RENDER_API_KEY = os.getenv("Render_Server", "")
RENDER_SERVICE_ID = "srv-d95jbrdckfvc73bds590"


def _backup_cookies_to_render(cookies_json: list):
    """Backup cookies to Render env var so they survive redeploys."""
    if not RENDER_API_KEY:
        log.warning("No Render API key found, skipping cloud backup.")
        return
    try:
        encoded = base64.b64encode(json.dumps(cookies_json).encode()).decode()
        url = f"https://api.render.com/v1/services/{RENDER_SERVICE_ID}/env-vars"
        headers = {
            "Authorization": f"Bearer {RENDER_API_KEY}",
            "Accept": "application/json",
            "Content-Type": "application/json"
        }
        payload = [{"key": "GEMINI_COOKIES_B64", "value": encoded}]
        resp = requests.put(url, headers=headers, json=payload, timeout=10)
        if resp.status_code in (200, 201):
            log.info("✅ Cookies backed up to Render env var successfully.")
        else:
            log.warning(f"Render env var backup returned status {resp.status_code}: {resp.text}")
    except Exception as e:
        log.error(f"Failed to backup cookies to Render: {e}")


def _restore_cookies_from_render():
    """Restore cookies from Render env var if local file is missing."""
    encoded = os.getenv("GEMINI_COOKIES_B64", "")
    if not encoded:
        return []
    try:
        decoded = base64.b64decode(encoded).decode()
        cookies = json.loads(decoded)
        # Save to local file for future use
        COOKIES_DIR.mkdir(parents=True, exist_ok=True)
        with open(COOKIES_FILE, "w") as f:
            json.dump(cookies, f, indent=2)
        log.info(f"✅ Restored {len(cookies)} cookies from Render env var backup.")
        return cookies
    except Exception as e:
        log.error(f"Failed to restore cookies from Render: {e}")
        return []


def save_cookies(cookies_json_str: str) -> tuple[bool, str]:
    """Save cookies from a JSON string (exported from browser).
    
    Returns (success, message).
    """
    try:
        cookies = json.loads(cookies_json_str)
        if isinstance(cookies, dict):
            cookies = [cookies]
        if not isinstance(cookies, list):
            return False, "Invalid format — expected a JSON array of cookies."
    except json.JSONDecodeError:
        # Fallback: Assume it's a raw cookie string or a full cURL command
        raw_cookies = cookies_json_str.strip()
        
        # If it's a cURL command, extract the cookie header
        if "curl " in raw_cookies.lower():
            match = re.search(r"""[-H|--header]\s+['"]cookie:\s*(.+?)['"]""", raw_cookies, re.IGNORECASE)
            if match:
                raw_cookies = match.group(1)
            else:
                return False, "❌ Found cURL command, but couldn't find the 'cookie:' header in it."

        if "=" not in raw_cookies:
            return False, "❌ Invalid format. Please provide valid JSON, a raw cookie string (key=value;), or a cURL command."
        
        cookies = []
        for pair in raw_cookies.split(";"):
            pair = pair.strip()
            if not pair or "=" not in pair:
                continue
            name, value = pair.split("=", 1)
            cookies.append({
                "name": name.strip(),
                "value": value.strip(),
                "domain": ".google.com",
                "path": "/"
            })

    try:
        # Normalize cookie format for Playwright
        normalized = []
        for c in cookies:
            cookie = {
                "name": c.get("name", ""),
                "value": c.get("value", ""),
                "domain": c.get("domain", ""),
                "path": c.get("path", "/"),
            }
            # Optional fields
            if "expires" in c and c["expires"]:
                try:
                    cookie["expires"] = float(c["expires"])
                except (ValueError, TypeError):
                    pass
            if c.get("httpOnly"):
                cookie["httpOnly"] = True
            if c.get("secure"):
                cookie["secure"] = True
            if c.get("sameSite"):
                ss = str(c["sameSite"]).capitalize()
                if ss in ("Strict", "Lax", "None"):
                    cookie["sameSite"] = ss

            if cookie["name"] and cookie["value"] and cookie["domain"]:
                normalized.append(cookie)

        if not normalized:
            return False, "No valid cookies found in the data."

        # Merge with existing cookies
        existing_cookies = []
        if COOKIES_FILE.exists():
            try:
                with open(COOKIES_FILE, "r") as f:
                    existing_cookies = json.load(f)
            except:
                pass
                
        # Update logic: keep old cookies unless we have a new one with the same name & domain
        merged = {f"{c['name']}:{c['domain']}": c for c in existing_cookies}
        for c in normalized:
            merged[f"{c['name']}:{c['domain']}"] = c
            
        final_cookies = list(merged.values())

        # Save to local file
        COOKIES_DIR.mkdir(parents=True, exist_ok=True)
        with open(COOKIES_FILE, "w") as f:
            json.dump(final_cookies, f, indent=2)

        # PERSISTENT BACKUP: Also save to Render env var so cookies survive redeploys!
        _backup_cookies_to_render(final_cookies)

        # Categorize cookies by domain
        domains = set(c["domain"] for c in normalized)
        domain_list = ", ".join(sorted(domains))

        return True, (
            f"✅ Saved {len(normalized)} cookies for: {domain_list}\n"
            f"☁️ Backed up to cloud (survives server restarts!)\n"
            f"The bot will now browse as your logged-in session!"
        )

    except Exception as e:
        return False, f"❌ Error saving cookies: {e}"


def load_cookies() -> list[dict]:
    """Load saved cookies from file, or restore from Render env var backup."""
    if COOKIES_FILE.exists():
        try:
            with open(COOKIES_FILE) as f:
                return json.load(f)
        except Exception as e:
            log.error(f"Failed to load cookies from file: {e}")
    
    # File doesn't exist — try restoring from Render env var
    restored = _restore_cookies_from_render()
    if restored:
        return restored
    
    return []


def has_cookies() -> bool:
    """Check if cookies have been saved (locally or in cloud backup)."""
    if COOKIES_FILE.exists():
        return True
    # Check if we have a cloud backup
    if os.getenv("GEMINI_COOKIES_B64", ""):
        return True
    return False


def get_cookies_for_domain(domain: str) -> list[dict]:
    """Get cookies matching a specific domain."""
    cookies = load_cookies()
    return [c for c in cookies if domain in c.get("domain", "")]


def get_cookie_export_snippet() -> str:
    """Return instructions for exporting cookies."""
    return (
        "🍪 *Export your Gemini cookies:*\n\n"
        "*Easiest Method — EditThisCookie + File Upload:*\n"
        "1. Install 'EditThisCookie V3' extension for Chrome.\n"
        "2. Open gemini.google.com and make sure you're logged in.\n"
        "3. Click the extension icon → Export (copies all cookies).\n"
        "4. Open Notepad, paste, and save as `cookies.txt`.\n"
        "5. Send that `cookies.txt` file here as a document attachment!\n\n"
        "_The bot auto-detects cookie files and backs them up to the cloud so they survive server restarts!_"
    )
