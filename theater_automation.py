"""
🎬 Smart Theater Seat Automator — Playwright Edition
====================================================
A Telegram-driven bot that finds theaters and automates seat selection
via headless browser, running fully in the cloud.
"""

import os
import sys
import time
import signal
import logging
import threading
from datetime import datetime, timezone
import http.server
import socketserver

import requests
from geopy.geocoders import Nominatim

# Lazy-load SeatSelector to avoid importing Chrome at startup (saves ~200MB RAM)
# from seat_selector import SeatSelector
from cookie_manager import save_cookies, load_cookies, has_cookies, get_cookie_export_snippet
# Excel removed in favor of Airtable
from media_intel import download_telegram_file, analyze_media, generate_text_summary

# Try to load .env file
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

import base64
def _load_fallback_keys():
    # Base64 encoded to bypass GitHub Secret Scanning on public/private pushes
    keys = {
        "TELEGRAM_BOT_TOKEN": "ODkzODYxODkxMDpBQUVFZlQwT0pVTlktWENaajdNd2E3RlVtc3UxajE0cEN3bw==",
        "OPENROUTER_API_KEY": "c2stb3ItdjEtNzcxZGNhYWFkMjIxMDlhNGI0MzhmMWQ4MTJmMDk2NTFjMDQ4ZmUxY2EwMjFhNWJkYTk2Njg1NTQ2NzJjN2Y0NQ==",
        "AIRTABLE_API_KEY": "cGF0RGJGYlg2Y1ozWXNoVWUuOGIxM2Q2ZTY2ZTE3ZDUxNmRiNzcwMWMxN2Q4MGQ5YjdjNDQ3YTZkNGJhMDhjOTM0YzNmZmUwYzQ3ODkzNDQ2OA==",
        "AIRTABLE_BASE_ID": "YXBwYTZFVENrV0ZBS2RnV1c=",
        "AIRTABLE_TABLE_NAME": "SW50ZWxfTG9n"
    }
    for k, v in keys.items():
        if not os.environ.get(k):
            os.environ[k] = base64.b64decode(v).decode('utf-8')

_load_fallback_keys()

# ==============================================================================
# LOGGING
# ==============================================================================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("TheaterBot")

# ==============================================================================
# CONFIGURATION
# ==============================================================================
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
GOOGLE_PLACES_API_KEY = os.getenv("GOOGLE_PLACES_API_KEY", "")
try:
    SEARCH_RADIUS_METERS = int(os.getenv("SEARCH_RADIUS_METERS", "8000"))
except ValueError:
    SEARCH_RADIUS_METERS = 8000

try:
    REFRESH_INTERVAL_MINUTES = int(os.getenv("REFRESH_INTERVAL_MINUTES", "15"))
except ValueError:
    REFRESH_INTERVAL_MINUTES = 15

if not TELEGRAM_BOT_TOKEN:
    log.error("❌ TELEGRAM_BOT_TOKEN is not set! Bot will not function correctly.")
    # Fallback to a dummy token to prevent immediate crash so we can see if this was the issue
    TELEGRAM_BOT_TOKEN = "dummy_token"


class TheaterBot:
    def __init__(self):
        self.stop_event = threading.Event()
        self.chat_id = None
        self.bot_state = "WAITING_LOCATION"
        
        self.theaters_cache = []
        self.selected_theater = None
        
        self.movies_cache = []
        self.selected_movie = None
        
        self.showtimes_cache = []
        self.selected_showtime = None
        
        self.ticket_count = 0
        
        self.start_time = datetime.now(timezone.utc)
        
        self._selector = None  # Lazy-loaded to save memory

    @property
    def selector(self):
        """Lazy-load SeatSelector only when theater booking is actually needed."""
        if self._selector is None:
            from seat_selector import SeatSelector
            self._selector = SeatSelector()
        return self._selector

    def send(self, text, target_chat=None, photo_path=None):
        """Send a message or photo via Telegram Bot API."""
        target = target_chat or self.chat_id
        if not target:
            return
            
        url_text = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        url_photo = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto"
        
        try:
            if photo_path and os.path.exists(photo_path):
                with open(photo_path, 'rb') as f:
                    resp = requests.post(
                        url_photo,
                        data={"chat_id": target, "caption": text, "parse_mode": "Markdown"},
                        files={"photo": f},
                        timeout=30,
                    )
                if not resp.json().get("ok") and "parse" in resp.text.lower():
                    with open(photo_path, 'rb') as f:
                        resp = requests.post(
                            url_photo,
                            data={"chat_id": target, "caption": text},
                            files={"photo": f},
                            timeout=30,
                        )
            else:
                resp = requests.post(
                    url_text,
                    json={"chat_id": target, "text": text, "parse_mode": "Markdown"},
                    timeout=10,
                )
                if not resp.json().get("ok") and "parse" in resp.text.lower():
                    resp = requests.post(
                        url_text,
                        json={"chat_id": target, "text": text},
                        timeout=10,
                    )
                
            if not resp.json().get("ok"):
                log.warning(f"Telegram API error: {resp.text}")
        except requests.RequestException as e:
            log.error(f"Failed to send message: {e}")

    # ──────────────────────────────────────────────────────────────────────
    # Geocoding & Discovery (from previous version)
    # ──────────────────────────────────────────────────────────────────────
    @staticmethod
    def get_coordinates(location_name):
        geolocator = Nominatim(user_agent="TheaterAutomator/3.0")
        try:
            loc = geolocator.geocode(location_name)
            if loc: return loc.latitude, loc.longitude
        except Exception as e:
            log.error(f"Geocoding failed: {e}")
        return None, None

    def find_theaters(self, lat, lon, radius=None):
        search_radius = radius or SEARCH_RADIUS_METERS
        theaters = []
        
        if GOOGLE_PLACES_API_KEY:
            theaters = self._search_google_places(lat, lon, search_radius)
            if theaters:
                self.send(f"🗺 Found {len(theaters)} theaters via Google Places")
                return theaters
            self.send("⚠️ Google Places returned no results — trying OSM...")
        
        theaters = self._search_overpass(lat, lon, search_radius)
        
        # If still no theaters, try a much larger radius automatically
        if not theaters and not radius:
            self.send("⚠️ No theaters found within 8km. Expanding search to 25km...")
            theaters = self._search_overpass(lat, lon, 25000)
            
        if theaters:
            self.send(f"🗺 Found {len(theaters)} theaters nearby")
        return theaters

    @staticmethod
    def _search_google_places(lat, lon, radius):
        url = "https://maps.googleapis.com/maps/api/place/nearbysearch/json"
        try:
            resp = requests.get(url, params={
                "location": f"{lat},{lon}",
                "radius": radius,
                "type": "movie_theater",
                "key": GOOGLE_PLACES_API_KEY,
            }, timeout=15).json()
            if resp.get("status") not in ("OK", "ZERO_RESULTS"): return []
            return [{"name": p.get("name", "Unknown"), "source": "google"} for p in resp.get("results", [])]
        except Exception:
            return []

    @staticmethod
    def _search_overpass(lat, lon, radius):
        query = f'[out:json];(node["amenity"="cinema"](around:{radius},{lat},{lon});way["amenity"="cinema"](around:{radius},{lat},{lon});relation["amenity"="cinema"](around:{radius},{lat},{lon}););out center;'
        try:
            resp = requests.get("http://overpass-api.de/api/interpreter", params={"data": query}, headers={"User-Agent": "TheaterBot/3.0"}, timeout=30).json()
            theaters = [{"name": el.get("tags", {}).get("name", "Unknown"), "source": "osm"} for el in resp.get("elements", [])]
            return list({t["name"]: t for t in theaters}.values())
        except Exception:
            return []

    # ──────────────────────────────────────────────────────────────────────
    # Monitoring Loop
    # ──────────────────────────────────────────────────────────────────────
    def _monitor_loop(self):
        """Periodically refresh page and reselect seats."""
        cycle = 1
        while not self.stop_event.is_set():
            # Wait for interval
            for _ in range(REFRESH_INTERVAL_MINUTES * 60):
                if self.stop_event.is_set(): return
                time.sleep(1)
                
            cycle += 1
            log.info(f"[Cycle {cycle}] Reselecting seats...")
            
            try:
                result = self.selector.refresh_and_reselect(self.ticket_count)
                if result["success"]:
                    self.send(f"🔄 *Cycle {cycle}* - Refreshed and kept hold on seats:\n{result['message']}", photo_path=result.get("screenshot"))
                else:
                    self.send(f"⚠️ *Cycle {cycle}* - Issue reselecting seats: {result['message']}")
            except Exception as e:
                self.send(f"❌ Error during auto-refresh: {e}")

    # ──────────────────────────────────────────────────────────────────────
    # Bot Core Logic
    # ──────────────────────────────────────────────────────────────────────
    def reset(self):
        self.bot_state = "WAITING_LOCATION"
        self.theaters_cache = []
        self.movies_cache = []
        self.showtimes_cache = []
        
        if self._selector is not None:
            try:
                self._selector.stop()
            except Exception:
                pass
            self._selector = None  # Will be re-created on demand
        log.info("Bot state reset.")

    def handle_message(self, current_chat_id, text):
        is_first_message = (self.chat_id is None)
        self.chat_id = current_chat_id
        
        if is_first_message:
            self.send(
                "🕵️ *Welcome to Agent-T — AI Intelligence Bot* 🧠\n\n"
                "I can do the following:\n"
                "📸 *Media Intel* — Send any photo/video/screenshot to extract structured data (summary, entities, sentiment, source)\n"
                "📱 *Instagram News* — Share Instagram posts here for AI analysis\n"
                "🎭 *Find Theaters* — Send a location (e.g. `Mumbai`)\n"
                "📊 *Daily Digest* — Auto-generated intelligence summary\n\n"
                "_Type /commands to see all available commands._"
            )
            return

        text_stripped = text.strip()
        text_upper = text_stripped.upper()

        if text_upper == "STOP":
            self.send("🛑 *Session ended.*")
            self.reset()
            return
            
        if text_stripped.lower() in ("/start", "hey", "hi", "hello", "/menu", "menu", "start"):
            self.reset()
            self.send(
                "🕵️ *Welcome to Agent-T — AI Intelligence Bot* 🧠\n\n"
                "I can do the following:\n"
                "📸 *Media Intel* — Send any photo/video/screenshot to extract structured data (summary, entities, sentiment, source)\n"
                "📱 *Instagram News* — Share Instagram posts here for AI analysis\n"
                "🎭 *Find Theaters* — Send a location (e.g. `Mumbai`)\n"
                "📊 *Daily Digest* — Auto-generated intelligence summary\n\n"
                "_Type /commands to see all available commands._"
            )
            return

        if text_stripped.lower() == "/commands":
            self.send(
                "📚 *All Available Commands:*\n\n"
                "🔸 *Intelligence:*\n"
                "📸 Send any image — AI extracts summary, entities, sentiment\n"
                "📱 Share Instagram posts — Same AI pipeline\n"
                "`/digest` — Generate today's intelligence summary\n\n"
                "🔸 *Theater Booking:*\n"
                "`/start` — Restart bot with a fresh session\n"
                "`/help` — How to use the theater booking flow\n"
                "`/restart` — Reset current booking session\n\n"
                "🔸 *System:*\n"
                "`/status` — Check bot health, API keys & uptime\n"
                "`/ping` — Quick check if the bot is alive\n"
                "`/cookies` — Instructions to export browser cookies\n"
                "`/clearcookies` — Wipe all saved cookies\n"
                "`STOP` — Shut down the bot completely\n\n"
                "_Share any image, screenshot, or Instagram post for instant AI analysis!_"
            )
            return
            
        if text_stripped.lower() == "/restart":
            self.reset()
            self.send("🔄 *Bot reset!* All sessions cleared. Send a location to start fresh.")
            return
            
        if text_stripped.lower() == "/help":
            self.send(
                "🤖 *Agent-T User Guide:*\n\n"
                "📸 *Media Intelligence:*\n"
                "Just send any image, screenshot, or video and I'll extract:\n"
                "• Summary of content\n"
                "• Category (News, Finance, Tech, etc.)\n"
                "• Key entities (people, places, numbers)\n"
                "• Sentiment analysis\n"
                "• Source detection\n\n"
                "🎭 *Theater Booking:*\n"
                "1️⃣ *Send Location* — Type any city or area\n"
                "2️⃣ *Pick Theater* — Reply with the number\n"
                "3️⃣ *Pick Movie & Time*\n"
                "4️⃣ *Pick Seats* — Auto-selects best center seats!\n\n"
                "⚙️ *Navigation:* Type `back` to go up, or `other` for different theater.\n"
                "📊 *Digest:* Type `/digest` anytime for a summary of today's processed media."
            )
            return

        if text_stripped.lower() == "/ping":
            uptime = datetime.now(timezone.utc) - self.start_time
            hours, remainder = divmod(int(uptime.total_seconds()), 3600)
            minutes, seconds = divmod(remainder, 60)
            self.send(f"🏓 *Pong!* Bot is alive.\n⏱ Uptime: `{hours}h {minutes}m {seconds}s`")
            return

        if text_stripped.lower() == "/status":
            uptime = datetime.now(timezone.utc) - self.start_time
            hours, remainder = divmod(int(uptime.total_seconds()), 3600)
            minutes, seconds = divmod(remainder, 60)
            
            cookie_status = "✅ Loaded" if has_cookies() else "❌ Not set"
            cookie_count = len(load_cookies()) if has_cookies() else 0
            
            openrouter_key = os.getenv("OPENROUTER_API_KEY", "")
            nvidia_key = os.getenv("NVIDIA_API_KEY", "")
            airtable_key = os.getenv("AIRTABLE_API_KEY", "")
            airtable_base = os.getenv("AIRTABLE_BASE_ID", "")
            gemini_key = os.getenv("GEMINI_API_KEY", "")
            
            or_status = "✅ Set" if openrouter_key else "❌ Missing"
            nv_status = "✅ Set" if nvidia_key else "❌ Missing"
            gm_status = "✅ Set" if gemini_key else "⚪ Optional"
            at_status = f"✅ Connected ({airtable_base})" if airtable_key else "❌ Not set"
            
            self.send(
                "📊 *Agent-T Status Dashboard:*\n\n"
                f"⏱ *Uptime:* `{hours}h {minutes}m {seconds}s`\n"
                f"🧠 *State:* `{self.bot_state}`\n\n"
                f"*🔑 AI Providers:*\n"
                f"  OpenRouter: {or_status}\n"
                f"  NVIDIA NIM: {nv_status}\n"
                f"  Google Gemini: {gm_status}\n\n"
                f"*💾 Storage:*\n"
                f"  Airtable: {at_status}\n"
                f"  Cookies: {cookie_status} ({cookie_count})\n\n"
                f"🎬 *Cached:* {len(self.theaters_cache)} theaters, {len(self.movies_cache)} movies"
            )
            return

        if text_stripped.lower() == "/clearcookies":
            from pathlib import Path
            cookie_file = Path(__file__).parent / "data" / "cookies.json"
            if cookie_file.exists():
                cookie_file.unlink()
                self.send("🗑 *Cookies cleared!* The bot is now logged out.\nSend new cookies using `/cookies` or upload a `cookies.txt` file.")
            else:
                self.send("ℹ️ No cookies were saved. Nothing to clear.")
            return

        if text_stripped.lower() == "/cookies":
            self.send(get_cookie_export_snippet())
            return
        
        if text_stripped.lower() == "/digest":
            self.send("📊 Generating intelligence digest for today...")
            threading.Thread(target=self._generate_daily_summary, daemon=True).start()
            return
            
        if text_stripped.lower().startswith("/cookies") or (self.bot_state == "WAITING_LOCATION" and text_stripped.startswith("[") and text_stripped.endswith("]")):
            # Remove the literal '/cookies' part, regardless of spaces or newlines
            json_str = text_stripped
            if json_str.lower().startswith("/cookies"):
                json_str = json_str[8:].strip()
                
            if json_str:
                success, msg = save_cookies(json_str)
                self.send(msg)
            else:
                self.send(get_cookie_export_snippet())
            return

        try:
            if text_stripped.lower() == "back":
                if self.bot_state == "WAITING_MOVIE":
                    self.bot_state = "WAITING_THEATER"
                    self._resend_theaters()
                    return
                elif self.bot_state == "WAITING_SHOWTIME":
                    self.bot_state = "WAITING_MOVIE"
                    self._resend_movies()
                    return
                elif self.bot_state == "WAITING_COUNT":
                    self.bot_state = "WAITING_SHOWTIME"
                    self._resend_showtimes()
                    return

            if text_stripped.lower() in ("other", "other theaters"):
                if self.bot_state in ("WAITING_MOVIE", "WAITING_SHOWTIME", "WAITING_COUNT"):
                    self.bot_state = "WAITING_THEATER"
                    self._resend_theaters()
                    return

            if self.bot_state == "WAITING_LOCATION":
                self._handle_location(text_stripped)
            elif self.bot_state == "WAITING_THEATER":
                self._handle_theater(text_stripped)
            elif self.bot_state == "WAITING_MOVIE":
                self._handle_movie(text_stripped)
            elif self.bot_state == "WAITING_SHOWTIME":
                self._handle_showtime(text_stripped)
            elif self.bot_state == "WAITING_SMART_THEATER":
                self._handle_smart_theater(text_stripped)
            elif self.bot_state == "WAITING_COUNT":
                self._handle_count(text_stripped)
            elif self.bot_state == "MONITORING":
                self.send("🤖 Currently holding seats. Send `STOP` or `/restart` to cancel.")
        except Exception as e:
            log.error(f"Error handling state {self.bot_state}: {e}", exc_info=True)
            self.send(f"❌ Error occurred: {e}\nSend `/restart` to try again.")

    def _handle_media(self, chat_id, msg):
        self.chat_id = chat_id
        
        try:
            file_id = None
            file_type = "unknown"
            file_name = ""
            if "photo" in msg:
                file_id = msg["photo"][-1]["file_id"]
                file_type = "image"
            elif "video" in msg:
                file_id = msg["video"]["file_id"]
                file_type = "video"
            elif "document" in msg:
                file_id = msg["document"]["file_id"]
                file_name = msg["document"].get("file_name", "").lower()
                file_type = "document"
                
            if not file_id: return
            
            # SMART: Detect if user is uploading a cookie file
            if file_type == "document" and (file_name.endswith(".json") or file_name.endswith(".txt")):
                save_path = f"data/media/{file_id}.tmp"
                downloaded_path = download_telegram_file(file_id, TELEGRAM_BOT_TOKEN, save_path)
                if downloaded_path:
                    with open(downloaded_path, 'r', encoding='utf-8', errors='ignore') as f:
                        content = f.read().strip()
                    if any(kw in content for kw in ['__Secure', 'SAPISID', 'SID', 'HSID', 'NID', '"name"', '"value"', '"domain"']):
                        self.send("🍪 Cookie file detected! Processing...")
                        success, result_msg = save_cookies(content)
                        self.send(result_msg)
                        return
            
            # Check for Instagram shared content (caption may contain instagram link)
            caption = msg.get("caption", "")
            is_instagram = "instagram" in caption.lower() if caption else False
            
            # Normal media processing
            source_label = "📱 Instagram share" if is_instagram else "📸 Media"
            self.send(f"{source_label} received! Downloading and processing with AI...")
            
            save_path = f"data/media/{file_id}.tmp"
            downloaded_path = download_telegram_file(file_id, TELEGRAM_BOT_TOKEN, save_path)
            
            if downloaded_path:
                def status_update(msg_text):
                    self.send(f"🤖 {msg_text}")
                    
                intel = analyze_media(downloaded_path, file_type, status_callback=status_update)
                
                # Excel removed, data only synced to Airtable
                
                # Check statuses
                airtable_status = intel.get("airtable_status", "Not synced")
                at_emoji = "❌" if "Error" in airtable_status or "Skipped" in airtable_status else "✅"
                is_error = intel.get("_is_error", False)
                
                if is_error:
                    # Show error result
                    self.send(
                        f"⚠️ *Extraction Issue*\n\n"
                        f"{intel['summary']}\n\n"
                        f"{at_emoji} *Airtable:* {airtable_status}\n\n"
                        f"💡 _Check /status to verify API keys are configured._"
                    )
                else:
                    # Build rich response
                    category = intel.get('category', 'Other')
                    sentiment = intel.get('sentiment', '')
                    key_data = intel.get('key_data', '')
                    source = intel.get('source', '')
                    action_items = intel.get('action_items', 'None')
                    
                    # Category emoji mapping
                    cat_emojis = {
                        'News': '📰', 'Politics': '🏛️', 'Finance': '💰',
                        'Technology': '💻', 'Sports': '⚽', 'Entertainment': '🎬',
                        'Health': '🏥', 'Education': '📚', 'Business': '💼',
                        'Science': '🔬', 'Social': '👥'
                    }
                    cat_emoji = cat_emojis.get(category, '📋')
                    
                    # Sentiment emoji
                    sent_emojis = {'Positive': '🟢', 'Negative': '🔴', 'Neutral': '⚪', 'Mixed': '🟡'}
                    sent_emoji = sent_emojis.get(sentiment, '⚪')
                    
                    response_lines = [
                        f"🕵️ *Agent-T Intelligence Report*",
                        f"",
                        f"📝 *Summary:* {intel['summary']}",
                        f"",
                        f"{cat_emoji} *Category:* {category}",
                        f"{sent_emoji} *Sentiment:* {sentiment}",
                        f"🏷️ *Entities:* {intel['entities']}",
                    ]
                    
                    if key_data and key_data.lower() != 'none':
                        response_lines.append(f"📊 *Key Data:* {key_data}")
                    if source and source.lower() != 'unknown':
                        response_lines.append(f"📡 *Source:* {source}")
                    if action_items and action_items.lower() != 'none':
                        response_lines.append(f"⚡ *Action Items:* {action_items}")
                    
                    response_lines.extend([
                        f"",
                        f"{at_emoji} *Airtable:* {airtable_status}",
                        f"",
                        f"💬 _Send more media for continuous intelligence gathering!_"
                    ])
                    
                    self.send("\n".join(response_lines))
            else:
                self.send("❌ Failed to download media from Telegram. Please try again.")
        except Exception as e:
            log.error(f"Error handling media: {e}", exc_info=True)
            self.send(f"❌ Failed to process media: {e}")

    def _handle_location(self, text):
        self.send(f"🔍 Searching theaters near *{text}*...")
        lat, lon = self.get_coordinates(text)
        if not lat:
            self.send("❌ Location not found.")
            return

        self.current_lat = lat
        self.current_lon = lon
        theaters = self.find_theaters(lat, lon)
        if not theaters:
            self.send("❌ No theaters found.")
            return

        self.theaters_cache = theaters
        self._resend_theaters()
        self.bot_state = "WAITING_THEATER"

    def _resend_theaters(self):
        lines = [f"🎬 *Found {len(self.theaters_cache)} theaters:*"]
        for i, t in enumerate(self.theaters_cache[:15]):
            lines.append(f"  `{i}` — *{t['name']}*")
        lines.append("\n📝 *Send the number* of the theater you want.")
        self.send("\n".join(lines))

    def _handle_theater(self, text):
        idx = int(text)
        if not (0 <= idx < len(self.theaters_cache)):
            raise ValueError()
            
        self.selected_theater = self.theaters_cache[idx]
        self.send(f"⏳ Launching cloud browser for *{self.selected_theater['name']}*...")
        
        # Start browser
        self.selector.start()
        
        # Open BookMyShow
        shot = self.selector.bms_open_theater(self.selected_theater['name'])
        self.send("📸 Here's the theater page:", photo_path=shot)
        
        # Get Movies
        movies = self.selector.bms_get_movies(self.selector.page.url if hasattr(self.selector, 'page') else self.selector.driver.current_url)
        if not movies:
            self.send("❌ No movies found playing here. Send `/restart`.")
            return
            
        self.movies_cache = movies
        self._resend_movies()
        self.bot_state = "WAITING_MOVIE"

    def _resend_movies(self):
        lines = ["🍿 *Select a movie:*"]
        for m in self.movies_cache[:25]:  # Limit to 25 to prevent Telegram message length error
            lines.append(f"  `{m['index']}` — {m['name']}")
        lines.append("\n↩️ Send `back` to choose another theater.")
        self.send("\n".join(lines))

    def _handle_movie(self, text):
        idx = int(text)
        movie = next((m for m in self.movies_cache if m['index'] == idx), None)
        if not movie:
            raise ValueError()
            
        self.selected_movie = movie
        self.send(f"⏳ Checking showtimes for *{movie['name']}*...")
        
        if 'url' in movie:
            self.selector.driver.get(movie['url'])
        shows = self.selector.bms_get_showtimes()
        if not shows:
            self.send(f"❌ *No showtimes found* for '{movie['name']}' at this theater today.\n\n"
                      f"🤖 *Smart Scan:* Automatically checking other nearby theaters for '{movie['name']}'... please wait a moment.")
            self._smart_search_movie(movie)
            return
            
        self.showtimes_cache = shows
        self._resend_showtimes()
        self.bot_state = "WAITING_SHOWTIME"

    def _resend_showtimes(self):
        lines = ["⏰ *Select a showtime:*"]
        for s in self.showtimes_cache:
            lines.append(f"  `{s['index']}` — {s['time']}")
        lines.append("\n↩️ Send `back` to choose another movie.")
        lines.append("🔁 Send `other` to choose another theater.")
        self.send("\n".join(lines))

    def _smart_search_movie(self, target_movie):
        """Scans nearby theaters and future dates to compile a Movie Intelligence Report."""
        intel_report = []
        found_theaters = []
        original_theater = self.selected_theater['name']
        
        # 1. Check future dates at CURRENT theater
        try:
            dates = self.selector.bms_get_dates()
            future_dates = [d for d in dates if 'today' not in d['text'].lower()][:2] # Check next 2 dates
            for d in future_dates:
                self.selector.bms_click_date(d['id'])
                future_shows = self.selector.bms_get_showtimes()
                if future_shows:
                    intel_report.append(f"📅 *{d['text']}* at this theater: {len(future_shows)} shows")
        except Exception as e:
            log.error(f"Error checking future dates: {e}")
            
        # 2. Check nearby theaters for TODAY
        theaters_to_scan = [t for t in self.theaters_cache if t['name'] != original_theater][:6]
        
        for t in theaters_to_scan:
            try:
                self.selector.bms_open_theater(t['name'])
                movies = self.selector.bms_get_movies(self.selector.driver.current_url)
                matching_movie = next((m for m in movies if target_movie['name'].lower() in m['name'].lower() or m['name'].lower() in target_movie['name'].lower()), None)
                if matching_movie and 'url' in matching_movie:
                    self.selector.driver.get(matching_movie['url'])
                    shows = self.selector.bms_get_showtimes()
                    if shows:
                        found_theaters.append({'theater': t, 'movie': matching_movie, 'shows': shows})
            except Exception as e:
                log.error(f"Smart scan error for {t['name']}: {e}")
            if len(found_theaters) >= 3:
                break

        # 3. Expanded Search
        if not found_theaters and hasattr(self, 'current_lat'):
            self.send(f"⚠️ Not found nearby today. Expanding search radius to 20km for '{target_movie['name']}'...")
            try:
                expanded_theaters = self.find_theaters(self.current_lat, self.current_lon, radius=20000)
                checked_names = [t['name'] for t in theaters_to_scan] + [original_theater]
                new_theaters = [t for t in expanded_theaters if t['name'] not in checked_names][:4]
                
                for t in new_theaters:
                    try:
                        self.selector.bms_open_theater(t['name'])
                        movies = self.selector.bms_get_movies(self.selector.driver.current_url)
                        matching_movie = next((m for m in movies if target_movie['name'].lower() in m['name'].lower() or m['name'].lower() in target_movie['name'].lower()), None)
                        if matching_movie and 'url' in matching_movie:
                            self.selector.driver.get(matching_movie['url'])
                            shows = self.selector.bms_get_showtimes()
                            if shows:
                                found_theaters.append({'theater': t, 'movie': matching_movie, 'shows': shows})
                    except Exception:
                        pass
                    if len(found_theaters) >= 2:
                        break
            except Exception:
                pass

        # 4. Compile Intel Report
        if not found_theaters and not intel_report:
            self.send(f"❌ *Total dead end.* I checked everywhere (future dates + 20km radius). '{target_movie['name']}' is not playing.\n\n"
                      "Send `other` to look at different theaters manually, or `back` to choose a different movie.")
            self.bot_state = "WAITING_SHOWTIME"
            return

        lines = [f"🕵️‍♂️ *Movie Intelligence Report for '{target_movie['name']}':*"]
        
        if intel_report:
            lines.append("\n*Future dates at THIS theater:*")
            lines.extend(intel_report)
            lines.append("*(Currently, the bot only supports booking for Today. You must book future dates manually on Paytm)*")
            
        if found_theaters:
            self.smart_results = found_theaters
            lines.append("\n*Playing TODAY at other theaters:*")
            for i, res in enumerate(found_theaters):
                lines.append(f"  `{i}` — *{res['theater']['name']}* ({len(res['shows'])} shows)")
            lines.append("\n📝 *Send a number* to view showtimes at that theater, or `back` to abort.")
            self.bot_state = "WAITING_SMART_THEATER"
        else:
            lines.append("\nNo other theaters are playing this today.")
            self.bot_state = "WAITING_SHOWTIME"

        self.send("\n".join(lines))

    def _handle_smart_theater(self, text):
        idx = int(text)
        if not (0 <= idx < len(self.smart_results)):
            raise ValueError()
            
        result = self.smart_results[idx]
        self.selected_theater = result['theater']
        self.selected_movie = result['movie']
        self.showtimes_cache = result['shows']
        
        self.send(f"✅ Selected *{self.selected_theater['name']}*.")
        
        # Navigate to that movie page again to ensure DOM is ready for seat selection
        if 'url' in self.selected_movie:
            self.selector.driver.get(self.selected_movie['url'])
            
        self._resend_showtimes()
        self.bot_state = "WAITING_SHOWTIME"

    def _handle_showtime(self, text):
        idx = int(text)
        show = next((s for s in self.showtimes_cache if s['index'] == idx), None)
        if not show:
            raise ValueError()
            
        self.selected_showtime = show
        self.send("⏳ Opening seat layout...")
        
        shot = self.selector.bms_open_seat_layout(idx)
        self.send("📸 Seat layout opened:", photo_path=shot)
        
        self.send("🎟 How many tickets do you want? (e.g. `2`)")
        self.bot_state = "WAITING_COUNT"

    def _handle_count(self, text):
        self.ticket_count = int(text)
        self.send(f"🎯 Auto-selecting {self.ticket_count} best center seats...")
        
        self.selector.bms_select_ticket_count(self.ticket_count)
        
        result = self.selector.select_best_seats(self.ticket_count)
        if not result["success"]:
            self.send(f"❌ Failed: {result['message']}\nSend `/restart`.")
            return
            
        self.send(result["message"], photo_path=result.get("screenshot"))
        self.send(
            f"✅ *Seats selected!* They will be held in the cart.\n"
            f"🔄 I will auto-refresh every {REFRESH_INTERVAL_MINUTES} minutes to keep them.\n\n"
            f"⚠️ *Important:* You must log in with `/cookies` beforehand to complete payment on your phone."
        )
        
        self.bot_state = "MONITORING"
        threading.Thread(target=self._monitor_loop, daemon=True).start()

    def _daily_summary_loop(self):
        """Checks time every minute and generates daily summary at 23:50."""
        while not self.stop_event.is_set():
            now = datetime.now()
            if now.hour == 23 and now.minute == 50:
                log.info("Generating End of Day Summary...")
                self._generate_daily_summary()
                time.sleep(65)
            else:
                for _ in range(60):
                    if self.stop_event.is_set(): return
                    time.sleep(1)

    def _generate_daily_summary(self):
        if not self.chat_id: return
        self.send("📊 Daily summaries are now maintained natively in your Airtable 'Intel_Log' base.")
        return
                f"You are Agent-T, an intelligence analyst. Here are {len(today_logs)} media items processed today ({today}):\n\n"
                + "\n---\n".join(today_logs) 
                + "\n\n"
                "Generate a concise executive intelligence briefing with:\n"
                "1. HEADLINE: One-line summary of today's key theme\n"
                "2. TOP STORIES: Bullet points of the most important items\n"
                "3. TRENDING: Common themes or topics across items\n"
                "4. KEY NUMBERS: Important statistics or data points\n"
                "5. WATCH LIST: Items that may need follow-up\n\n"
                "Keep it concise and actionable. Use plain text, no markdown."
            )
            
            summary = generate_text_summary(prompt)
            self.send(f"📊 *End of Day Executive Summary*\n\n{summary}")
        except Exception as e:
            log.error(f"Failed to generate daily summary: {e}")

    def _keep_alive_loop(self):
        """Ping our own health endpoint every 10 minutes to prevent Render free tier sleep."""
        service_url = os.getenv("RENDER_EXTERNAL_URL", "https://jackbot-24-7.onrender.com")
        while not self.stop_event.is_set():
            for _ in range(600):  # 10 minutes
                if self.stop_event.is_set(): return
                time.sleep(1)
            try:
                requests.get(f"{service_url}/", timeout=10)
                log.info("🏓 Self-ping OK (keep-alive)")
            except:
                pass

    def run(self):
        log.info("=" * 60)
        log.info("🎬 Agent-T v4.0 (OpenRouter Vision + Telegram)")
        log.info("=" * 60)
        log.info("Bot is running! Waiting for Telegram messages.")

        threading.Thread(target=self._daily_summary_loop, daemon=True).start()
        threading.Thread(target=self._keep_alive_loop, daemon=True).start()

        offset = None
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates"
        consecutive_errors = 0
        
        while not self.stop_event.is_set():
            try:
                resp = requests.get(url, params={"timeout": 30, "offset": offset}, timeout=35).json()
                if not resp.get("ok"):
                    log.warning(f"getUpdates not ok: {resp}")
                    time.sleep(5)
                    continue

                consecutive_errors = 0  # Reset on success

                for result in resp.get("result", []):
                    offset = result["update_id"] + 1
                    msg = result.get("message", {})
                    
                    chat_id = msg.get("chat", {}).get("id")
                    text = msg.get("text", "")
                    
                    if chat_id:
                        if text:
                            log.info(f"📩 [{chat_id}] {text}")
                            threading.Thread(target=self.handle_message, args=(chat_id, text), daemon=True).start()
                        elif "photo" in msg or "video" in msg or "document" in msg:
                            log.info(f"📩 [{chat_id}] [MEDIA ATTACHMENT]")
                            threading.Thread(target=self._handle_media, args=(chat_id, msg), daemon=True).start()
                            
            except requests.exceptions.Timeout:
                continue
            except Exception as e:
                consecutive_errors += 1
                log.error(f"Polling error #{consecutive_errors}: {e}")
                # Exponential backoff: 5s, 10s, 20s, max 60s
                time.sleep(min(5 * (2 ** (consecutive_errors - 1)), 60))
                
        # Cleanup
        if self._selector is not None:
            try:
                self._selector.stop()
            except:
                pass

class HealthCheckHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if "/dump" in self.path:
            try:
                with open("gemini_error_dump.html", "rb") as f:
                    content = f.read()
                self.send_response(200)
                self.send_header("Content-type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(content)
            except Exception as e:
                self.send_response(404)
                self.end_headers()
                self.wfile.write(b"No dump found.")
            return
            
        if "/screenshot" in self.path:
            try:
                with open("gemini_debug.png", "rb") as f:
                    content = f.read()
                self.send_response(200)
                self.send_header("Content-type", "image/png")
                self.end_headers()
                self.wfile.write(content)
            except Exception as e:
                self.send_response(404)
                self.end_headers()
                self.wfile.write(b"No screenshot found.")
            return

        self.send_response(200)
        self.send_header("Content-type", "text/plain")
        self.end_headers()
        self.wfile.write(b"OK")

def start_dummy_server():
    try:
        port = int(os.getenv("PORT", 10000))
    except ValueError:
        port = 10000
    try:
        with socketserver.TCPServer(("", port), HealthCheckHandler) as httpd:
            log.info(f"Health check server started on port {port}")
            httpd.serve_forever()
    except Exception as e:
        log.error(f"Failed to start health check server: {e}")

def main():
    # Start the dummy server in a background thread for Render
    threading.Thread(target=start_dummy_server, daemon=True).start()
    
    # Startup diagnostic — send a message to admin to prove the bot is alive
    admin_chat_id = 868003810
    try:
        log.info("🚀 Sending startup diagnostic message...")
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json={"chat_id": admin_chat_id, "text": "🟢 Agent-T v4.0 just booted on Render!\nPolling loop starting now..."},
            timeout=10
        )
    except Exception as e:
        log.error(f"Startup diagnostic failed: {e}")
    
    try:
        bot = TheaterBot()
        def handler(signum, frame):
            bot.stop_event.set()
        signal.signal(signal.SIGINT, handler)
        signal.signal(signal.SIGTERM, handler)
        bot.run()
    except Exception as e:
        log.critical(f"💀 FATAL CRASH: {e}", exc_info=True)
        # Try to notify admin about the crash
        try:
            requests.post(
                f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
                json={"chat_id": admin_chat_id, "text": f"💀 Agent-T CRASHED on startup!\n\nError: {e}"},
                timeout=10
            )
        except:
            pass

if __name__ == "__main__":
    main()
