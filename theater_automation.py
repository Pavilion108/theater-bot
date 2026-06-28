"""
🎬 Smart Theater Seat Automator — Telegram Bot Edition
======================================================
A Telegram-driven bot that finds nearby theaters and provides booking links.
Designed to run headlessly in Docker / GitHub Codespaces.

Usage:
  1. Copy .env.example to .env and add your TELEGRAM_BOT_TOKEN
  2. Run: python theater_automation.py
  3. Open Telegram and send any message to your bot to start

Telegram Commands:
  - Send a location name (e.g. "Nerul") to search for theaters
  - /restart  — Reset bot state and start fresh
  - /status   — Check bot health
  - STOP      — Shut down the bot
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

# Try to load .env file (python-dotenv is optional but recommended)
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # .env loading is optional; env vars can be set directly

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
# CONFIGURATION (from environment variables)
# ==============================================================================
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
GOOGLE_PLACES_API_KEY = os.getenv("GOOGLE_PLACES_API_KEY", "")
SEARCH_RADIUS_METERS = int(os.getenv("SEARCH_RADIUS_METERS", "8000"))
REFRESH_INTERVAL_MINUTES = int(os.getenv("REFRESH_INTERVAL_MINUTES", "15"))
HEARTBEAT_INTERVAL_MINUTES = int(os.getenv("HEARTBEAT_INTERVAL_MINUTES", "60"))

if not TELEGRAM_BOT_TOKEN:
    log.error("❌ TELEGRAM_BOT_TOKEN is not set! Copy .env.example to .env and add your token.")
    sys.exit(1)

# ==============================================================================
# BOOKING PLATFORMS — search templates for Indian theaters
# ==============================================================================
BOOKING_PLATFORMS = {
    "BookMyShow": "https://in.bookmyshow.com/explore/cinemas-{city}",
    "Paytm Movies": "https://www.google.com/search?q=paytm+movies+{theater}",
    "Google Search": "https://www.google.com/search?q={theater}+movie+tickets+book+online",
}


class TheaterBot:
    """Encapsulates all bot state and logic in a clean, restartable class."""

    def __init__(self):
        self.stop_event = threading.Event()
        self.chat_id = None
        self.bot_state = "WAITING_LOCATION"
        self.theaters_cache = []
        self.selected_theaters = []
        self.start_time = datetime.now(timezone.utc)
        self._heartbeat_thread = None

    # ──────────────────────────────────────────────────────────────────────
    # Telegram Messaging
    # ──────────────────────────────────────────────────────────────────────
    def send(self, text, target_chat=None):
        """Send a message via Telegram Bot API."""
        target = target_chat or self.chat_id
        if not target:
            log.warning("No chat_id set yet — cannot send message.")
            return
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        try:
            resp = requests.post(
                url,
                json={"chat_id": target, "text": text, "parse_mode": "Markdown"},
                timeout=10,
            )
            if not resp.json().get("ok"):
                log.warning(f"Telegram API error: {resp.text}")
        except requests.RequestException as e:
            log.error(f"Failed to send Telegram message: {e}")

    # ──────────────────────────────────────────────────────────────────────
    # Geocoding
    # ──────────────────────────────────────────────────────────────────────
    @staticmethod
    def get_coordinates(location_name):
        """Resolve a place name to (lat, lon) using Nominatim."""
        geolocator = Nominatim(user_agent="TheaterAutomator/2.0")
        try:
            location = geolocator.geocode(location_name)
            if location:
                return location.latitude, location.longitude
        except Exception as e:
            log.error(f"Geocoding failed for '{location_name}': {e}")
        return None, None

    # ──────────────────────────────────────────────────────────────────────
    # Theater Search — Google Places API (preferred) or Overpass (free)
    # ──────────────────────────────────────────────────────────────────────
    def find_theaters(self, lat, lon, radius=None):
        """Find theaters near coordinates. Tries Google Places first, falls back to Overpass."""
        radius = radius or SEARCH_RADIUS_METERS
        theaters = []

        # Try Google Places first if key is set
        if GOOGLE_PLACES_API_KEY:
            theaters = self._search_google_places(lat, lon, radius)
            if theaters:
                self.send(f"🗺 Found {len(theaters)} theaters via Google Places API")
                return theaters
            else:
                self.send("⚠️ Google Places returned no results — trying OpenStreetMap...")

        # Fallback to Overpass (or primary if no Google key)
        theaters = self._search_overpass(lat, lon, radius)
        if theaters:
            self.send(f"🗺 Found {len(theaters)} theaters via OpenStreetMap")
        return theaters

    @staticmethod
    def _search_google_places(lat, lon, radius):
        """Search using Google Places API — richer data, faster, more reliable."""
        url = "https://maps.googleapis.com/maps/api/place/nearbysearch/json"
        params = {
            "location": f"{lat},{lon}",
            "radius": radius,
            "type": "movie_theater",
            "key": GOOGLE_PLACES_API_KEY,
        }
        try:
            resp = requests.get(url, params=params, timeout=15)
            data = resp.json()

            # Check for API errors
            status = data.get("status", "UNKNOWN")
            if status != "OK" and status != "ZERO_RESULTS":
                log.error(f"Google Places API returned status: {status}")
                log.error(f"Error message: {data.get('error_message', 'none')}")
                return []

            theaters = []
            for place in data.get("results", []):
                theaters.append({
                    "name": place.get("name", "Unknown"),
                    "lat": place["geometry"]["location"]["lat"],
                    "lon": place["geometry"]["location"]["lng"],
                    "address": place.get("vicinity", ""),
                    "rating": place.get("rating", "N/A"),
                    "website": None,
                    "source": "google_places",
                })
            return theaters
        except Exception as e:
            log.error(f"Google Places API error: {e}")
            return []

    @staticmethod
    def _search_overpass(lat, lon, radius):
        """Fallback: Search using free OpenStreetMap Overpass API."""
        overpass_url = "http://overpass-api.de/api/interpreter"
        query = f"""
        [out:json];
        (
          node["amenity"="cinema"](around:{radius},{lat},{lon});
          way["amenity"="cinema"](around:{radius},{lat},{lon});
          relation["amenity"="cinema"](around:{radius},{lat},{lon});
        );
        out center;
        """
        try:
            headers = {"User-Agent": "TheaterAutomator/2.0 (Python/Requests)"}
            resp = requests.get(overpass_url, params={"data": query}, headers=headers, timeout=30)
            data = resp.json()

            theaters = []
            for el in data.get("elements", []):
                tags = el.get("tags", {})
                name = tags.get("name", "Unknown Theater")
                website = tags.get("website")
                t_lat = el.get("lat") or el.get("center", {}).get("lat")
                t_lon = el.get("lon") or el.get("center", {}).get("lon")
                theaters.append({
                    "name": name,
                    "lat": t_lat,
                    "lon": t_lon,
                    "address": tags.get("addr:street", ""),
                    "rating": "N/A",
                    "website": website,
                    "source": "overpass",
                })

            # Deduplicate by name
            unique = {t["name"]: t for t in theaters}
            return list(unique.values())
        except Exception as e:
            log.error(f"Overpass API error: {e}")
            return []

    # ──────────────────────────────────────────────────────────────────────
    # Build Booking Links
    # ──────────────────────────────────────────────────────────────────────
    @staticmethod
    def build_booking_links(theater, city=""):
        """Generate booking platform links for a theater."""
        theater_name = theater["name"].replace(" ", "+")
        city_slug = city.lower().replace(" ", "-") if city else ""
        lines = []
        for platform, url_template in BOOKING_PLATFORMS.items():
            url = url_template.format(theater=theater_name, city=city_slug)
            lines.append(f"  🔗 [{platform}]({url})")
        if theater.get("website"):
            lines.append(f"  🌐 [Official Site]({theater['website']})")
        return "\n".join(lines)

    # ──────────────────────────────────────────────────────────────────────
    # Heartbeat — periodic "I'm alive" messages
    # ──────────────────────────────────────────────────────────────────────
    def _heartbeat_loop(self):
        """Send periodic heartbeat messages so you know the bot is alive."""
        if HEARTBEAT_INTERVAL_MINUTES <= 0:
            return
        interval_sec = HEARTBEAT_INTERVAL_MINUTES * 60
        while not self.stop_event.wait(interval_sec):
            uptime = datetime.now(timezone.utc) - self.start_time
            hours, remainder = divmod(int(uptime.total_seconds()), 3600)
            minutes, _ = divmod(remainder, 60)
            self.send(f"💓 *Heartbeat* — Bot is alive\n⏱ Uptime: {hours}h {minutes}m\n📍 State: `{self.bot_state}`")

    # ──────────────────────────────────────────────────────────────────────
    # Monitoring Loop — periodically check theater sites
    # ──────────────────────────────────────────────────────────────────────
    def _monitor_loop(self):
        """Periodically check theater websites for availability changes (headless, no browser)."""
        cycle = 1
        while not self.stop_event.is_set():
            for theater in self.selected_theaters:
                if self.stop_event.is_set():
                    return
                url = theater.get("website")
                if not url:
                    continue
                try:
                    resp = requests.get(url, timeout=15, headers={"User-Agent": "TheaterBot/2.0"})
                    status = "✅ UP" if resp.status_code == 200 else f"⚠️ {resp.status_code}"
                    log.info(f"[Cycle {cycle}] {theater['name']}: {status}")
                    # Only notify on errors (to avoid spam)
                    if resp.status_code != 200:
                        self.send(f"⚠️ *{theater['name']}* returned HTTP {resp.status_code}")
                except Exception as e:
                    log.error(f"[Cycle {cycle}] {theater['name']}: ❌ {e}")
                    self.send(f"❌ *{theater['name']}* is unreachable: {e}")

            # Wait for next cycle
            for _ in range(REFRESH_INTERVAL_MINUTES * 60):
                if self.stop_event.is_set():
                    return
                time.sleep(1)
            cycle += 1

    # ──────────────────────────────────────────────────────────────────────
    # Reset bot state
    # ──────────────────────────────────────────────────────────────────────
    def reset(self):
        """Reset bot to initial state (keeps chat_id)."""
        self.stop_event.set()  # Stop any running monitor loop
        time.sleep(1)
        self.stop_event.clear()
        self.bot_state = "WAITING_LOCATION"
        self.theaters_cache = []
        self.selected_theaters = []
        log.info("Bot state reset.")

    # ──────────────────────────────────────────────────────────────────────
    # Handle Incoming Messages
    # ──────────────────────────────────────────────────────────────────────
    def handle_message(self, current_chat_id, text):
        """Process a single incoming Telegram message."""
        # First message — establish link
        if not self.chat_id:
            self.chat_id = current_chat_id
            self.send(
                "✅ *Link established!*\n\n"
                "Send me a location (e.g. `Nerul`, `Andheri West`) to search for nearby theaters.\n\n"
                "*Commands:*\n"
                "  /restart — Reset and search again\n"
                "  /status — Check bot health\n"
                "  STOP — Shut down the bot",
                current_chat_id,
            )
            return

        text_stripped = text.strip()
        text_upper = text_stripped.upper()

        # ── Global Commands ──
        if text_upper == "STOP":
            self.send("🛑 *Shutting down...* Goodbye!")
            self.stop_event.set()
            return

        if text_stripped.lower() == "/restart":
            self.reset()
            self.send("🔄 *Bot reset!* Send me a new location to search.")
            return

        if text_stripped.lower() == "/status":
            uptime = datetime.now(timezone.utc) - self.start_time
            hours, remainder = divmod(int(uptime.total_seconds()), 3600)
            minutes, _ = divmod(remainder, 60)
            api_source = "Google Places" if GOOGLE_PLACES_API_KEY else "OpenStreetMap (free)"
            self.send(
                f"📊 *Bot Status*\n"
                f"  ⏱ Uptime: {hours}h {minutes}m\n"
                f"  📍 State: `{self.bot_state}`\n"
                f"  🗺 API: {api_source}\n"
                f"  🎬 Theaters cached: {len(self.theaters_cache)}\n"
                f"  👁 Monitoring: {len(self.selected_theaters)} theaters"
            )
            return

        # ── State Machine ──
        if self.bot_state == "WAITING_LOCATION":
            self._handle_location(text_stripped)

        elif self.bot_state == "WAITING_SELECTION":
            self._handle_selection(text_stripped)

        elif self.bot_state == "MONITORING":
            self.send(
                "🤖 I'm currently monitoring theaters.\n"
                "Send `/restart` to search a new location, or `STOP` to shut down."
            )

    def _handle_location(self, location_text):
        """Handle location input — geocode and search for theaters."""
        self.send(f"🔍 Searching for theaters near *{location_text}*...")

        lat, lon = self.get_coordinates(location_text)
        if lat is None:
            self.send(
                "❌ Could not find that location.\n"
                "Try spelling it differently or add the city (e.g. `Nerul, Navi Mumbai`)."
            )
            return

        self.send(f"📍 Found coordinates: `{lat:.4f}, {lon:.4f}`\nScanning for theaters within {SEARCH_RADIUS_METERS // 1000}km...")

        theaters = self.find_theaters(lat, lon)
        if not theaters:
            self.send("❌ No theaters found in that area. Try a different location or increase the search radius.")
            return

        self.theaters_cache = theaters

        # Build the theater list message
        lines = [f"🎬 *Found {len(theaters)} theaters:*\n"]
        for i, t in enumerate(theaters):
            rating = f" ⭐ {t['rating']}" if t.get("rating") and t["rating"] != "N/A" else ""
            address = f"\n     📫 {t['address']}" if t.get("address") else ""
            lines.append(f"  `{i}` — *{t['name']}*{rating}{address}")

        lines.append("\n📝 *Send the numbers* of the theaters to monitor (e.g. `0,2,3`)")
        lines.append("Or send `all` to select all theaters.")
        self.send("\n".join(lines))

        self.bot_state = "WAITING_SELECTION"

    def _handle_selection(self, selection_text):
        """Handle theater selection input."""
        try:
            if selection_text.lower() == "all":
                self.selected_theaters = list(self.theaters_cache)
            else:
                indices = [int(x.strip()) for x in selection_text.split(",")]
                self.selected_theaters = [self.theaters_cache[i] for i in indices if 0 <= i < len(self.theaters_cache)]

            if not self.selected_theaters:
                self.send("❌ No valid theaters selected. Send numbers like `0,1,2` or `all`.")
                return

            # Send booking links for each selected theater
            self.send(f"✅ *Selected {len(self.selected_theaters)} theaters!*\n\nHere are your booking links:\n")

            for theater in self.selected_theaters:
                links = self.build_booking_links(theater)
                msg = f"🎬 *{theater['name']}*\n{links}"
                self.send(msg)
                time.sleep(0.5)  # Avoid Telegram rate limiting

            # Start monitoring
            self.bot_state = "MONITORING"
            monitor_thread = threading.Thread(target=self._monitor_loop, daemon=True)
            monitor_thread.start()

            self.send(
                f"🚀 *Monitoring started!*\n"
                f"I'll check these {len(self.selected_theaters)} theater sites every {REFRESH_INTERVAL_MINUTES} minutes "
                f"and alert you if anything changes.\n\n"
                f"Send `/restart` to search a new location or `STOP` to shut down."
            )

        except (ValueError, IndexError):
            self.send("❌ Invalid input. Send theater numbers separated by commas (e.g. `0,2`) or `all`.")

    # ──────────────────────────────────────────────────────────────────────
    # Main Polling Loop
    # ──────────────────────────────────────────────────────────────────────
    def run(self):
        """Main entry point — polls Telegram for updates and dispatches messages."""
        log.info("=" * 60)
        log.info("🎬 Smart Theater Automator v2.0")
        log.info("=" * 60)
        log.info("Bot is running! Send any message to your Telegram bot to start.")
        log.info(f"API source: {'Google Places' if GOOGLE_PLACES_API_KEY else 'OpenStreetMap (free)'}")
        log.info(f"Search radius: {SEARCH_RADIUS_METERS}m | Refresh: every {REFRESH_INTERVAL_MINUTES}min")
        log.info("Press Ctrl+C to stop.\n")

        # Start heartbeat thread
        self._heartbeat_thread = threading.Thread(target=self._heartbeat_loop, daemon=True)
        self._heartbeat_thread.start()

        offset = None
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates"
        consecutive_errors = 0

        while not self.stop_event.is_set():
            try:
                params = {"timeout": 30, "offset": offset}
                response = requests.get(url, params=params, timeout=35)
                data = response.json()

                if not data.get("ok"):
                    log.warning(f"Telegram API returned error: {data}")
                    consecutive_errors += 1
                    if consecutive_errors > 10:
                        log.error("Too many consecutive Telegram errors. Sleeping 60s...")
                        time.sleep(60)
                    continue

                consecutive_errors = 0

                for result in data.get("result", []):
                    offset = result["update_id"] + 1
                    message = result.get("message", {})
                    if not message:
                        continue

                    current_chat_id = message.get("chat", {}).get("id")
                    text = message.get("text", "").strip()

                    if not current_chat_id or not text:
                        continue

                    log.info(f"📩 Message from {current_chat_id}: {text}")
                    self.handle_message(current_chat_id, text)

            except requests.exceptions.Timeout:
                # Long polling timeout is normal — just retry
                continue
            except requests.RequestException as e:
                log.error(f"Network error: {e}")
                consecutive_errors += 1
                backoff = min(30, 2 ** consecutive_errors)
                log.info(f"Retrying in {backoff}s...")
                time.sleep(backoff)
            except Exception as e:
                log.error(f"Unexpected error in main loop: {e}", exc_info=True)
                time.sleep(5)

        log.info("👋 Bot stopped gracefully.")


def main():
    bot = TheaterBot()

    # Handle Ctrl+C and SIGTERM (Docker stop) gracefully
    def shutdown_handler(signum, frame):
        log.info("Received shutdown signal...")
        bot.stop_event.set()

    signal.signal(signal.SIGINT, shutdown_handler)
    signal.signal(signal.SIGTERM, shutdown_handler)

    bot.run()


if __name__ == "__main__":
    main()
