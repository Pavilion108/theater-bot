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

import requests
from geopy.geocoders import Nominatim

from seat_selector import SeatSelector, run_async
from cookie_manager import save_cookies, get_cookie_export_snippet

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
        radius = radius or SEARCH_RADIUS_METERS
        if GOOGLE_PLACES_API_KEY:
            theaters = self._search_google_places(lat, lon, radius)
            if theaters:
                self.send(f"🗺 Found {len(theaters)} theaters via Google Places")
                return theaters
            self.send("⚠️ Google Places returned no results — trying OSM...")
        
        theaters = self._search_overpass(lat, lon, radius)
        if theaters:
            self.send(f"🗺 Found {len(theaters)} theaters via OSM")
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
                result = run_async(self.selector.refresh_and_reselect(self.ticket_count))
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
            run_async(self.selector.stop())
        except Exception:
            pass
            
        self.selector = SeatSelector()
        log.info("Bot state reset.")

    def handle_message(self, current_chat_id, text):
        if not self.chat_id:
            self.chat_id = current_chat_id
            self.send("✅ *Link established!*\n\nSend a location (e.g. `Nerul`) to start.\nSend `/cookies` to learn how to log in.")
            return

        text_stripped = text.strip()
        text_upper = text_stripped.upper()

        if text_upper == "STOP":
            self.send("🛑 *Shutting down...* Goodbye!")
            self.stop_event.set()
            return
            
        if text_stripped.lower() == "/restart":
            self.reset()
            self.send("🔄 *Bot reset!* Send a location.")
            return

        if text_stripped.lower() == "/cookies":
            self.send(get_cookie_export_snippet())
            return
            
        if text_stripped.startswith("/cookies ") or (self.bot_state == "WAITING_LOCATION" and text_stripped.startswith("[") and text_stripped.endswith("]")):
            json_str = text_stripped.replace("/cookies ", "").strip()
            success, msg = save_cookies(json_str)
            self.send(msg)
            return

        try:
            if self.bot_state == "WAITING_LOCATION":
                self._handle_location(text_stripped)
            elif self.bot_state == "WAITING_THEATER":
                self._handle_theater(text_stripped)
            elif self.bot_state == "WAITING_MOVIE":
                self._handle_movie(text_stripped)
            elif self.bot_state == "WAITING_SHOWTIME":
                self._handle_showtime(text_stripped)
            elif self.bot_state == "WAITING_COUNT":
                self._handle_count(text_stripped)
            elif self.bot_state == "MONITORING":
                self.send("🤖 Currently holding seats. Send `STOP` or `/restart` to cancel.")
        except Exception as e:
            log.error(f"Error handling state {self.bot_state}: {e}", exc_info=True)
            self.send(f"❌ Error occurred: {e}\nSend `/restart` to try again.")

    def _handle_location(self, text):
        self.send(f"🔍 Searching theaters near *{text}*...")
        lat, lon = self.get_coordinates(text)
        if not lat:
            self.send("❌ Location not found.")
            return

        theaters = self.find_theaters(lat, lon)
        if not theaters:
            self.send("❌ No theaters found.")
            return

        self.theaters_cache = theaters
        lines = [f"🎬 *Found {len(theaters)} theaters:*"]
        for i, t in enumerate(theaters[:15]):  # limit to 15
            lines.append(f"  `{i}` — *{t['name']}*")
        lines.append("\n📝 *Send the number* of the theater you want.")
        
        self.send("\n".join(lines))
        self.bot_state = "WAITING_THEATER"

    def _handle_theater(self, text):
        idx = int(text)
        if not (0 <= idx < len(self.theaters_cache)):
            raise ValueError()
            
        self.selected_theater = self.theaters_cache[idx]
        self.send(f"⏳ Launching cloud browser for *{self.selected_theater['name']}*...")
        
        # Start Playwright
        run_async(self.selector.start())
        
        # Open BookMyShow
        shot = run_async(self.selector.bms_open_theater(self.selected_theater['name']))
        self.send("📸 Here's the theater page:", photo_path=shot)
        
        # Get Movies
        movies = run_async(self.selector.bms_get_movies(self.selector.page.url))
        if not movies:
            self.send("❌ No movies found playing here. Send `/restart`.")
            return
            
        self.movies_cache = movies
        lines = ["🍿 *Select a movie:*"]
        for m in movies:
            lines.append(f"  `{m['index']}` — {m['name']}")
        self.send("\n".join(lines))
        
        self.bot_state = "WAITING_MOVIE"

    def _handle_movie(self, text):
        idx = int(text)
        movie = next((m for m in self.movies_cache if m['index'] == idx), None)
        if not movie:
            raise ValueError()
            
        self.selected_movie = movie
        self.send(f"⏳ Checking showtimes for *{movie['name']}*...")
        
        if 'url' in movie:
            run_async(self.selector.navigate(movie['url']))
        
        shows = run_async(self.selector.bms_get_showtimes())
        if not shows:
            self.send("❌ No showtimes found. Send `/restart`.")
            return
            
        self.showtimes_cache = shows
        lines = ["⏰ *Select a showtime:*"]
        for s in shows:
            lines.append(f"  `{s['index']}` — {s['time']}")
        self.send("\n".join(lines))
        
        self.bot_state = "WAITING_SHOWTIME"

    def _handle_showtime(self, text):
        idx = int(text)
        show = next((s for s in self.showtimes_cache if s['index'] == idx), None)
        if not show:
            raise ValueError()
            
        self.selected_showtime = show
        self.send("⏳ Opening seat layout...")
        
        shot = run_async(self.selector.bms_open_seat_layout(idx))
        self.send("📸 Seat layout opened:", photo_path=shot)
        
        self.send("🎟 How many tickets do you want? (e.g. `2`)")
        self.bot_state = "WAITING_COUNT"

    def _handle_count(self, text):
        self.ticket_count = int(text)
        self.send(f"🎯 Auto-selecting {self.ticket_count} best center seats...")
        
        run_async(self.selector.bms_select_ticket_count(self.ticket_count))
        
        result = run_async(self.selector.select_best_seats(self.ticket_count))
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

    def run(self):
        log.info("=" * 60)
        log.info("🎬 Theater Automator v3.0 (Playwright)")
        log.info("=" * 60)
        log.info("Bot is running! Waiting for Telegram messages.")

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
                    
                    if chat_id and text:
                        log.info(f"📩 [{chat_id}] {text}")
                        self.handle_message(chat_id, text)
                        
            except requests.exceptions.Timeout:
                continue
            except Exception as e:
                log.error(f"Polling error: {e}")
                time.sleep(5)
                
        # Cleanup
        try:
            run_async(self.selector.stop())
        except:
            pass

def main():
    bot = TheaterBot()
    def handler(signum, frame):
        bot.stop_event.set()
    signal.signal(signal.SIGINT, handler)
    signal.signal(signal.SIGTERM, handler)
    bot.run()

if __name__ == "__main__":
    main()
