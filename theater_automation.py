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

from seat_selector import SeatSelector
from cookie_manager import save_cookies, load_cookies, has_cookies, get_cookie_export_snippet
from excel_logger import log_media_to_excel, get_excel_path
from media_intel import download_telegram_file, analyze_media, generate_text_summary

# Try to load .env file
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

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
SEARCH_RADIUS_METERS = int(os.getenv("SEARCH_RADIUS_METERS", "8000"))
REFRESH_INTERVAL_MINUTES = int(os.getenv("REFRESH_INTERVAL_MINUTES", "15"))

if not TELEGRAM_BOT_TOKEN:
    log.error("❌ TELEGRAM_BOT_TOKEN is not set!")
    sys.exit(1)


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
        
        self.selector = SeatSelector()

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
            else:
                resp = requests.post(
                    url_text,
                    json={"chat_id": target, "text": text, "parse_mode": "Markdown"},
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
        self.stop_event.set()
        time.sleep(1)
        self.stop_event.clear()
        
        self.bot_state = "WAITING_LOCATION"
        self.theaters_cache = []
        self.movies_cache = []
        self.showtimes_cache = []
        
        try:
            self.selector.stop()
        except Exception:
            pass
            
        self.selector = SeatSelector()
        log.info("Bot state reset.")

    def handle_message(self, current_chat_id, text):
        if not self.chat_id:
            self.chat_id = current_chat_id
            self.send(
                "🎬 *Welcome to Agent-T — Smart Theater Bot* 🍿\n\n"
                "I can do the following:\n"
                "🎭 *Find Theaters* — Send a location (e.g. `Mumbai`)\n"
                "📸 *Image Intel* — Send any photo to extract data\n"
                "🍪 *Login* — Send a `cookies.txt` file for Gemini access\n\n"
                "_Type /commands to see all available commands._"
            )
            return

        text_stripped = text.strip()
        text_upper = text_stripped.upper()

        if text_upper == "STOP":
            self.send("🛑 *Shutting down...* Goodbye!")
            self.stop_event.set()
            return
            
        if text_stripped.lower() in ("/start", "hey", "hi", "hello", "/menu", "menu", "start"):
            self.reset()
            self.send(
                "🎬 *Welcome to Agent-T — Smart Theater Bot* 🍿\n\n"
                "I can do the following:\n"
                "🎭 *Find Theaters* — Send a location (e.g. `Mumbai`)\n"
                "📸 *Image Intel* — Send any photo to extract data\n"
                "🍪 *Login* — Send a `cookies.txt` file for Gemini access\n\n"
                "_Type /commands to see all available commands._"
            )
            return

        if text_stripped.lower() == "/commands":
            self.send(
                "📚 *All Available Commands:*\n\n"
                "🔹 `/start` — Restart bot with a fresh session\n"
                "🔹 `/help` — How to use the theater booking flow\n"
                "🔹 `/commands` — Show this list of all commands\n"
                "🔹 `/status` — Check bot health, cookie status & uptime\n"
                "🔹 `/ping` — Quick check if the bot is alive\n"
                "🔹 `/cookies` — Instructions to export browser cookies\n"
                "🔹 `/clearcookies` — Wipe all saved cookies\n"
                "🔹 `/restart` — Reset current booking session\n"
                "🔹 `STOP` — Shut down the bot completely\n\n"
                "_You can also send me any photo/video for AI data extraction!_"
            )
            return
            
        if text_stripped.lower() == "/restart":
            self.reset()
            self.send("🔄 *Bot reset!* All sessions cleared. Send a location to start fresh.")
            return
            
        if text_stripped.lower() == "/help":
            self.send(
                "🤖 *Theater Booking Guide:*\n\n"
                "1️⃣ *Send Location* — Type any city or area (e.g. `Nerul`, `Mumbai`)\n"
                "2️⃣ *Pick Theater* — Reply with the number from the list\n"
                "3️⃣ *Pick Movie & Time* — Choose from the available options\n"
                "4️⃣ *Pick Seats* — Tell me how many tickets. I auto-select the best center seats!\n\n"
                "📸 *Image Intel:* Send any photo and I will extract a summary, entities & key data using AI.\n\n"
                "⚙️ *Navigation:* Type `back` to go to the previous step, or `other` to pick a different theater.\n\n"
                "⚠️ *Important:* For image processing via Gemini, you must first send your cookies. Type `/cookies` for instructions."
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
            
            or_status = "✅ Set" if openrouter_key else "❌ Missing"
            nv_status = "✅ Set" if nvidia_key else "❌ Missing"
            at_status = "✅ Connected" if airtable_key else "❌ Not set"
            
            self.send(
                "📊 *Bot Status Dashboard:*\n\n"
                f"⏱ *Uptime:* `{hours}h {minutes}m {seconds}s`\n"
                f"🧠 *State:* `{self.bot_state}`\n"
                f"🍪 *Cookies:* {cookie_status} ({cookie_count} cookies)\n"
                f"🔑 *OpenRouter API:* {or_status}\n"
                f"🔑 *NVIDIA API:* {nv_status}\n"
                f"📝 *Airtable:* {at_status}\n\n"
                f"🎬 *Cached Theaters:* {len(self.theaters_cache)}\n"
                f"🎬 *Cached Movies:* {len(self.movies_cache)}\n"
                f"🎬 *Cached Showtimes:* {len(self.showtimes_cache)}"
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
                # Could be a cookie file! Download and check.
                save_path = f"data/media/{file_id}.tmp"
                downloaded_path = download_telegram_file(file_id, TELEGRAM_BOT_TOKEN, save_path)
                if downloaded_path:
                    with open(downloaded_path, 'r', encoding='utf-8', errors='ignore') as f:
                        content = f.read().strip()
                    # Heuristic: if it looks like cookies (has cookie-like keys)
                    if any(kw in content for kw in ['__Secure', 'SAPISID', 'SID', 'HSID', 'NID', '"name"', '"value"', '"domain"']):
                        self.send("🍪 Cookie file detected! Processing...")
                        success, result_msg = save_cookies(content)
                        self.send(result_msg)
                        return
            
            # Normal media processing
            self.send("📸 Media received! Downloading and processing data...")
            
            save_path = f"data/media/{file_id}.tmp"
            downloaded_path = download_telegram_file(file_id, TELEGRAM_BOT_TOKEN, save_path)
            
            if downloaded_path:
                def status_update(msg_text):
                    self.send(f"🤖 {msg_text}")
                    
                intel = analyze_media(downloaded_path, file_type, status_callback=status_update)
                
                # Excel Logging
                excel_status = "Not logged"
                try:
                    excel_file = log_media_to_excel(chat_id, intel)
                    excel_status = f"Saved to {os.path.basename(excel_file)}"
                except Exception as ex:
                    log.error(f"Failed to log to Excel: {ex}")
                    excel_status = f"Error: {ex}"
                
                # Check airtable status
                airtable_status = intel.get("airtable_status", "Not synced")
                if "Error" in airtable_status:
                    status_emoji = "❌"
                else:
                    status_emoji = "✅"
                
                excel_emoji = "❌" if "Error" in excel_status else "✅"
                
                self.send(f"🤖 *Extraction Complete!*\n\n*Summary:* {intel['summary']}\n\n*Entities:* {intel['entities']}\n\n{status_emoji} *Airtable:* {airtable_status}\n{excel_emoji} *Excel:* {excel_status}\n\n💬 *You can now continue to chat and send more media!*")
        except Exception as e:
            log.error(f"Error handling media: {e}")
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
        excel_file = get_excel_path(self.chat_id)
        if not os.path.exists(excel_file):
            return
            
        try:
            import openpyxl
            wb = openpyxl.load_workbook(excel_file)
            ws = wb.active
            today = datetime.now().strftime('%Y-%m-%d')
            today_logs = []
            
            for row in list(ws.iter_rows(values_only=True))[1:]: # Skip header
                if row[0] and str(row[0]).startswith(today):
                    today_logs.append(f"Time: {row[0]}, Type: {row[2]}\nSummary: {row[3]}\nEntities: {row[4]}")
            
            if not today_logs:
                return # No logs today
                
            prompt = f"Here are the logs of media processed today ({today}):\n\n" + "\n---\n".join(today_logs) + "\n\nPlease write a concise executive summary of all the information captured today."
            
            summary = generate_text_summary(prompt)
            self.send(f"📊 *End of Day Executive Summary*\n\n{summary}")
        except Exception as e:
            log.error(f"Failed to generate daily summary: {e}")

    def run(self):
        log.info("=" * 60)
        log.info("🎬 Theater Automator v3.0 (Playwright)")
        log.info("=" * 60)
        log.info("Bot is running! Waiting for Telegram messages.")

        threading.Thread(target=self._daily_summary_loop, daemon=True).start()

        offset = None
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates"
        
        while not self.stop_event.is_set():
            try:
                resp = requests.get(url, params={"timeout": 30, "offset": offset}, timeout=35).json()
                if not resp.get("ok"):
                    time.sleep(5)
                    continue

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
                log.error(f"Polling error: {e}")
                time.sleep(5)
                
        # Cleanup
        try:
            self.selector.stop()
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
    port = int(os.getenv("PORT", 10000))
    try:
        with socketserver.TCPServer(("", port), HealthCheckHandler) as httpd:
            log.info(f"Health check server started on port {port}")
            httpd.serve_forever()
    except Exception as e:
        log.error(f"Failed to start health check server: {e}")

def main():
    # Start the dummy server in a background thread for Render
    threading.Thread(target=start_dummy_server, daemon=True).start()
    
    bot = TheaterBot()
    def handler(signum, frame):
        bot.stop_event.set()
    signal.signal(signal.SIGINT, handler)
    signal.signal(signal.SIGTERM, handler)
    bot.run()

if __name__ == "__main__":
    main()
