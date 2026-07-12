"""
🍪 Cookie Manager — Import Chrome cookies for authenticated browsing.

Provides a simple way for users to export their Chrome cookies and inject
them into the Playwright browser session, making the headless browser appear
as a logged-in real user.
"""

import json
import logging
import os
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
            # If it's a single cookie, wrap in list
            cookies = [cookies]
        
        if not isinstance(cookies, list):
            return False, "Invalid format — expected a JSON array of cookies."

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

    except json.JSONDecodeError:
        return False, "❌ Invalid JSON format. Please copy the exact output from the browser snippet."
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
        "📋 *Export your cookies in 3 steps:*\n\n"
        "*Step 1:* Open either Paytm (paytm.com/movies) OR Gemini (gemini.google.com) and make sure you're logged in\n\n"
        "*Step 2:* Press `F12` → click the *Console* tab → paste this and press Enter:\n\n"
        "```\n"
        "copy(JSON.stringify(document.cookie.split('; ').map(c => {"
        "const [n,...v] = c.split('=');"
        "return {name:n, value:v.join('='), domain:window.location.hostname, path:'/'}"
        "})))\n"
        "```\n\n"
        "*Step 3:* The cookies are now in your clipboard. Send them here as:\n"
        "`/cookies [paste here]`\n\n"
        "*(Note: You can do this once for Paytm and once for Gemini. The bot will automatically save and combine them!)*"
    )
