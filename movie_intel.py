"""
🎬 Movie Intelligence Module
============================
Enriches movie data using free public APIs:
- TMDb API: ratings, posters, genres, runtime, trailers
- OMDb API: IMDb / Rotten Tomatoes scores
- Open-Meteo: weather forecast for showtime decisions
- Nager.Date: public holiday detection (auto-tune refresh interval)
- goqr.me: QR code generation for booking URLs
"""

import logging
import os
import time
from datetime import datetime, timezone

import requests

log = logging.getLogger("TheaterBot")

TMDB_API_KEY = os.getenv("TMDB_API_KEY", "")
OMDB_API_KEY = os.getenv("OMDB_API_KEY", "")

_tmdb_cache: dict = {}
_weather_cache: dict = {}
_holiday_cache: dict = {}


# ──────────────────────────────────────────────────────────────────────────────
# TMDb — Movie ratings, poster, runtime, genres
# ──────────────────────────────────────────────────────────────────────────────

def get_tmdb_info(movie_name: str) -> dict:
    """Fetch movie metadata from TMDb (free API key required)."""
    if not TMDB_API_KEY:
        return {}

    cache_key = movie_name.lower().strip()
    if cache_key in _tmdb_cache:
        return _tmdb_cache[cache_key]

    try:
        resp = requests.get(
            "https://api.themoviedb.org/3/search/movie",
            params={"api_key": TMDB_API_KEY, "query": movie_name, "language": "en-US", "page": 1},
            timeout=8,
        ).json()

        results = resp.get("results", [])
        if not results:
            return {}

        movie = results[0]
        poster_path = movie.get("poster_path", "")
        info = {
            "tmdb_id": movie.get("id"),
            "title": movie.get("title", movie_name),
            "rating": round(movie.get("vote_average", 0), 1),
            "votes": movie.get("vote_count", 0),
            "overview": (movie.get("overview", "") or "")[:200],
            "release_date": movie.get("release_date", ""),
            "poster_url": f"https://image.tmdb.org/t/p/w500{poster_path}" if poster_path else "",
            "genre_ids": movie.get("genre_ids", []),
        }

        # Fetch runtime from details endpoint
        if info["tmdb_id"]:
            detail = requests.get(
                f"https://api.themoviedb.org/3/movie/{info['tmdb_id']}",
                params={"api_key": TMDB_API_KEY},
                timeout=8,
            ).json()
            info["runtime"] = detail.get("runtime", 0)
            info["genres"] = [g["name"] for g in detail.get("genres", [])]

        _tmdb_cache[cache_key] = info
        return info

    except Exception as e:
        log.warning(f"TMDb lookup failed for '{movie_name}': {e}")
        return {}


def get_omdb_info(movie_name: str) -> dict:
    """Fetch IMDb / Rotten Tomatoes scores from OMDb (free API key required)."""
    if not OMDB_API_KEY:
        return {}

    cache_key = f"omdb_{movie_name.lower().strip()}"
    if cache_key in _tmdb_cache:
        return _tmdb_cache[cache_key]

    try:
        resp = requests.get(
            "http://www.omdbapi.com/",
            params={"apikey": OMDB_API_KEY, "t": movie_name, "type": "movie"},
            timeout=8,
        ).json()

        if resp.get("Response") != "True":
            return {}

        ratings = {r["Source"]: r["Value"] for r in resp.get("Ratings", [])}
        info = {
            "imdb_rating": resp.get("imdbRating", "N/A"),
            "rt_score": ratings.get("Rotten Tomatoes", "N/A"),
            "metascore": resp.get("Metascore", "N/A"),
            "rated": resp.get("Rated", ""),
            "awards": resp.get("Awards", ""),
        }
        _tmdb_cache[cache_key] = info
        return info

    except Exception as e:
        log.warning(f"OMDb lookup failed for '{movie_name}': {e}")
        return {}


def format_movie_card(movie_name: str) -> str:
    """Build a rich Telegram-formatted movie card with ratings."""
    tmdb = get_tmdb_info(movie_name)
    omdb = get_omdb_info(movie_name)

    if not tmdb and not omdb:
        return f"🎬 *{movie_name}*"

    lines = [f"🎬 *{tmdb.get('title', movie_name)}*"]

    # Ratings row
    rating_parts = []
    if tmdb.get("rating"):
        stars = "⭐" * round(tmdb["rating"] / 2)
        rating_parts.append(f"TMDb {tmdb['rating']}/10 {stars}")
    if omdb.get("imdb_rating") and omdb["imdb_rating"] != "N/A":
        rating_parts.append(f"IMDb {omdb['imdb_rating']}")
    if omdb.get("rt_score") and omdb["rt_score"] != "N/A":
        rating_parts.append(f"🍅 {omdb['rt_score']}")
    if rating_parts:
        lines.append(" · ".join(rating_parts))

    # Runtime & genres
    meta_parts = []
    if tmdb.get("runtime"):
        h, m = divmod(tmdb["runtime"], 60)
        meta_parts.append(f"⏱ {h}h {m}m" if h else f"⏱ {m}m")
    if tmdb.get("genres"):
        meta_parts.append(" · ".join(tmdb["genres"][:3]))
    if meta_parts:
        lines.append(" | ".join(meta_parts))

    # Short overview
    if tmdb.get("overview"):
        lines.append(f"_{tmdb['overview']}..._")

    return "\n".join(lines)


# ──────────────────────────────────────────────────────────────────────────────
# Open-Meteo — Weather forecast (no API key needed)
# ──────────────────────────────────────────────────────────────────────────────

def get_weather_advisory(lat: float, lon: float) -> str:
    """
    Returns a short weather advisory string for the next 12 hours.
    Uses Open-Meteo (completely free, no key needed).
    """
    cache_key = f"{round(lat, 2)},{round(lon, 2)}"
    cached = _weather_cache.get(cache_key)
    if cached and time.time() - cached["ts"] < 1800:  # 30-min cache
        return cached["msg"]

    try:
        resp = requests.get(
            "https://api.open-meteo.com/v1/forecast",
            params={
                "latitude": lat,
                "longitude": lon,
                "hourly": "precipitation_probability,weathercode,temperature_2m",
                "forecast_days": 1,
                "timezone": "auto",
            },
            timeout=8,
        ).json()

        hourly = resp.get("hourly", {})
        times = hourly.get("time", [])
        precip = hourly.get("precipitation_probability", [])
        codes = hourly.get("weathercode", [])
        temps = hourly.get("temperature_2m", [])

        now_hour = datetime.now().hour
        # Look at next 6 hours
        upcoming = list(zip(times, precip, codes, temps))[now_hour: now_hour + 6]

        max_precip = max((p for _, p, _, _ in upcoming if p is not None), default=0)
        avg_temp = sum(t for _, _, _, t in upcoming if t is not None) / max(len(upcoming), 1)

        advisory = ""
        if max_precip >= 70:
            advisory = f"🌧 *Heavy rain expected* ({max_precip}% chance) — consider an earlier show!"
        elif max_precip >= 40:
            advisory = f"🌦 *Rain possible* ({max_precip}% chance) — carry an umbrella."
        elif avg_temp >= 38:
            advisory = f"🌡 *Very hot outside* ({avg_temp:.0f}°C) — stay cool inside the theater!"
        elif avg_temp <= 10:
            advisory = f"🧥 *Cold weather* ({avg_temp:.0f}°C) — dress warm for the commute."

        _weather_cache[cache_key] = {"msg": advisory, "ts": time.time()}
        return advisory

    except Exception as e:
        log.warning(f"Weather fetch failed: {e}")
        return ""


# ──────────────────────────────────────────────────────────────────────────────
# Nager.Date — Public holiday detection (no API key needed)
# ──────────────────────────────────────────────────────────────────────────────

def is_public_holiday(country_code: str = "IN") -> tuple[bool, str]:
    """
    Check if today or tomorrow is a public holiday.
    Returns (is_holiday, holiday_name).
    Uses Nager.Date API (free, no key).
    """
    today = datetime.now(timezone.utc)
    year = today.year
    cache_key = f"{country_code}_{year}"

    if cache_key not in _holiday_cache:
        try:
            resp = requests.get(
                f"https://date.nager.at/api/v3/PublicHolidays/{year}/{country_code}",
                timeout=8,
            ).json()
            _holiday_cache[cache_key] = {h["date"]: h["localName"] for h in resp}
        except Exception as e:
            log.warning(f"Holiday fetch failed: {e}")
            return False, ""

    holidays = _holiday_cache.get(cache_key, {})
    today_str = today.strftime("%Y-%m-%d")
    tomorrow_str = (today.replace(day=today.day + 1)).strftime("%Y-%m-%d")

    if today_str in holidays:
        return True, holidays[today_str]
    if tomorrow_str in holidays:
        return True, f"Tomorrow: {holidays[tomorrow_str]}"
    return False, ""


def get_smart_refresh_interval(base_minutes: int = 15, country_code: str = "IN") -> int:
    """
    Auto-tune the seat-hold refresh interval.
    On holidays/weekends, seats fill faster — refresh more aggressively.
    """
    is_holiday, name = is_public_holiday(country_code)
    weekday = datetime.now().weekday()  # 5=Sat, 6=Sun

    if is_holiday or weekday >= 5:
        tuned = max(5, base_minutes // 2)
        reason = name if is_holiday else "Weekend"
        log.info(f"🗓 High-demand day ({reason}) — refresh interval tuned to {tuned}m")
        return tuned

    return base_minutes


def get_holiday_advisory(country_code: str = "IN") -> str:
    """Return a Telegram-formatted holiday warning string."""
    is_holiday, name = is_public_holiday(country_code)
    weekday = datetime.now().weekday()

    if is_holiday:
        return f"🗓 *Public holiday detected* ({name}) — theaters will be packed! Seats may sell out fast."
    if weekday >= 5:
        return "📅 *Weekend* — expect high demand. Refresh interval has been halved automatically."
    return ""


# ──────────────────────────────────────────────────────────────────────────────
# goqr.me — QR Code generation (no API key needed)
# ──────────────────────────────────────────────────────────────────────────────

def get_booking_qr_url(booking_url: str, size: int = 200) -> str:
    """
    Generate a QR code image URL for a booking link using goqr.me (free).
    Returns a direct image URL that Telegram can display inline.
    """
    import urllib.parse
    encoded = urllib.parse.quote(booking_url)
    return f"https://api.qrserver.com/v1/create-qr-code/?size={size}x{size}&data={encoded}"


def download_qr_image(booking_url: str, save_path: str) -> str:
    """Download QR code image to disk and return the path."""
    try:
        qr_url = get_booking_qr_url(booking_url)
        resp = requests.get(qr_url, timeout=10)
        if resp.status_code == 200:
            with open(save_path, "wb") as f:
                f.write(resp.content)
            return save_path
    except Exception as e:
        log.warning(f"QR download failed: {e}")
    return ""
