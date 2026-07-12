"""
🍪 Cookie Manager — Import Chrome cookies for authenticated browsing.

Provides a simple way for users to export their Chrome cookies and inject
them into the Playwright browser session, making the headless browser appear
as a logged-in real user.
"""

import json
import logging
import os
import re
from pathlib import Path

log = logging.getLogger("TheaterBot")

COOKIES_DIR = Path(__file__).parent / "data"
COOKIES_FILE = COOKIES_DIR / "cookies.json"


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
            # Extract the content of the -H "cookie: ..." or -H 'cookie: ...' header
            match = re.search(r"[-H|--header]\s+['\"]cookie:\s*(.+?)['\"]", raw_cookies, re.IGNORECASE)
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

        # Save to file
        COOKIES_DIR.mkdir(parents=True, exist_ok=True)
        with open(COOKIES_FILE, "w") as f:
            json.dump(final_cookies, f, indent=2)

        # Categorize cookies by domain
        domains = set(c["domain"] for c in normalized)
        domain_list = ", ".join(sorted(domains))

        return True, (
            f"✅ Saved {len(normalized)} cookies for: {domain_list}\n"
            f"The bot will now browse as your logged-in session!"
        )

    except Exception as e:
        return False, f"❌ Error saving cookies: {e}"


def load_cookies() -> list[dict]:
    """Load saved cookies from file."""
    if not COOKIES_FILE.exists():
        return []
    try:
        with open(COOKIES_FILE) as f:
            return json.load(f)
    except Exception as e:
        log.error(f"Failed to load cookies: {e}")
        return []


def has_cookies() -> bool:
    """Check if cookies have been saved."""
    return COOKIES_FILE.exists()


def get_cookies_for_domain(domain: str) -> list[dict]:
    """Get cookies matching a specific domain."""
    cookies = load_cookies()
    return [c for c in cookies if domain in c.get("domain", "")]


def get_cookie_export_snippet() -> str:
    """Return the JavaScript snippet users should run in Chrome DevTools."""
    return (
        "📋 *Export your cookies correctly:*\n\n"
        "Since Google uses strict security, Javascript cannot read your login cookies. You have two options:\n\n"
        "**Option 1: Using EditThisCookie Extension (Easy)**\n"
        "1. Install the 'EditThisCookie' extension for Chrome.\n"
        "2. Open gemini.google.com and make sure you're logged in.\n"
        "3. Click the extension icon, then click the 'Export' button (arrow pointing out the door).\n"
        "4. Send it to the bot exactly as: `/cookies [paste here]`\n\n"
        "**Option 2: Using Developer Tools (No Extensions)**\n"
        "1. Open gemini.google.com and log in.\n"
        "2. Press F12 to open Developer Tools.\n"
        "3. Click the **Network** tab at the top.\n"
        "4. **Refresh the page (F5)** (this is very important!).\n"
        "5. You will see a list of files loading. Click literally **ANY** item in that list.\n"
        "6. A side panel opens. Scroll down to the **Request Headers** section.\n"
        "7. Find the header labeled `cookie:`.\n"
        "8. Right-click the massive block of text next to `cookie:` and click **Copy value** (or highlight all text next to it and copy).\n"
        "9. Send it to the bot exactly as: `/cookies [paste here]`"
    )
