"""
🎭 Seat Selector Engine — Playwright-based headless browser automation.

Controls a stealth Chromium browser to navigate booking platforms,
parse seat layouts, and select optimal center seats.
"""

import asyncio
import logging
import os
import time
from pathlib import Path

from playwright.async_api import async_playwright, Browser, BrowserContext, Page

from cookie_manager import load_cookies

log = logging.getLogger("TheaterBot")

SCREENSHOTS_DIR = Path(__file__).parent / "data" / "screenshots"
SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)


class SeatSelector:
    """Manages a stealth Playwright browser session for seat selection."""

    def __init__(self):
        self.playwright = None
        self.browser: Browser | None = None
        self.context: BrowserContext | None = None
        self.page: Page | None = None
        self._started = False

    async def start(self):
        """Launch the stealth browser."""
        if self._started:
            return

        self.playwright = await async_playwright().start()

        # Detect system Chromium (for Alpine Linux compatibility)
        executable_path = None
        for path in ["/usr/bin/chromium-browser", "/usr/bin/chromium"]:
            if os.path.exists(path):
                executable_path = path
                break
                
        launch_args = {
            "headless": True,
            "args": [
                "--no-sandbox",
                "--disable-blink-features=AutomationControlled",
                "--disable-dev-shm-usage",
                "--disable-gpu",
                "--window-size=1920,1080",
            ]
        }
        
        if executable_path:
            launch_args["executable_path"] = executable_path
            log.info(f"Using system Chromium at {executable_path}")

        # Launch Chromium with stealth settings
        self.browser = await self.playwright.chromium.launch(**launch_args)

        # Create context with realistic fingerprint
        self.context = await self.browser.new_context(
            viewport={"width": 1920, "height": 1080},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36"
            ),
            locale="en-IN",
            timezone_id="Asia/Kolkata",
            geolocation={"latitude": 19.0760, "longitude": 72.8777},
            permissions=["geolocation"],
        )

        # Inject stealth scripts to avoid detection
        await self.context.add_init_script("""
            // Override navigator.webdriver
            Object.defineProperty(navigator, 'webdriver', { get: () => false });
            
            // Override chrome runtime
            window.chrome = { runtime: {} };
            
            // Override permissions query
            const originalQuery = window.navigator.permissions.query;
            window.navigator.permissions.query = (parameters) => (
                parameters.name === 'notifications' ?
                Promise.resolve({ state: Notification.permission }) :
                originalQuery(parameters)
            );
            
            // Override plugins
            Object.defineProperty(navigator, 'plugins', {
                get: () => [1, 2, 3, 4, 5],
            });
            
            // Override languages
            Object.defineProperty(navigator, 'languages', {
                get: () => ['en-IN', 'en-US', 'en'],
            });
        """)

        # Load saved cookies
        cookies = load_cookies()
        if cookies:
            await self.context.add_cookies(cookies)
            log.info(f"Loaded {len(cookies)} cookies into browser session")

        self.page = await self.context.new_page()
        self._started = True
        log.info("🎭 Stealth browser started successfully")

    async def stop(self):
        """Close the browser."""
        if self.browser:
            await self.browser.close()
        if self.playwright:
            await self.playwright.stop()
        self._started = False
        log.info("Browser closed")

    async def screenshot(self, name: str = "page") -> str:
        """Take a screenshot and return the file path."""
        filepath = str(SCREENSHOTS_DIR / f"{name}_{int(time.time())}.png")
        await self.page.screenshot(path=filepath, full_page=False)
        log.info(f"📸 Screenshot saved: {filepath}")
        return filepath

    async def navigate(self, url: str, wait_for: str = "networkidle"):
        """Navigate to a URL and wait for it to load."""
        await self.page.goto(url, wait_until="domcontentloaded", timeout=30000)
        try:
            await self.page.wait_for_load_state(wait_for, timeout=15000)
        except Exception:
            pass  # Some pages never fully idle
        # Human-like delay
        await asyncio.sleep(2)

    async def get_page_title(self) -> str:
        """Get the current page title."""
        return await self.page.title()

    async def get_page_url(self) -> str:
        """Get the current page URL."""
        return self.page.url

    # ──────────────────────────────────────────────────────────────────────
    # BookMyShow Specific Methods
    # ──────────────────────────────────────────────────────────────────────

    async def bms_open_theater(self, theater_name: str, city: str = ""):
        """Open BookMyShow and search for a theater."""
        city_slug = city.lower().replace(" ", "-") if city else "mumbai"
        url = f"https://in.bookmyshow.com/explore/cinemas-{city_slug}"
        await self.navigate(url)
        return await self.screenshot("bms_theaters")

    async def bms_get_movies(self, theater_url: str) -> list[dict]:
        """Get list of movies playing at a theater."""
        await self.navigate(theater_url)
        await asyncio.sleep(2)

        movies = await self.page.evaluate("""
            () => {
                const movieElements = document.querySelectorAll('[data-movie-name], .movie-name, .__movie-name');
                const movies = [];
                movieElements.forEach((el, i) => {
                    const name = el.textContent?.trim() || el.getAttribute('data-movie-name') || '';
                    if (name && !movies.find(m => m.name === name)) {
                        movies.push({ index: i, name: name });
                    }
                });
                
                // Fallback: look for any card-like elements with movie info
                if (movies.length === 0) {
                    document.querySelectorAll('a[href*="/movies/"], a[href*="buytickets"]').forEach((el, i) => {
                        const name = el.textContent?.trim()?.split('\\n')[0] || '';
                        const href = el.getAttribute('href') || '';
                        if (name && name.length > 2 && name.length < 100) {
                            movies.push({ index: i, name: name, url: href });
                        }
                    });
                }
                return movies;
            }
        """)
        return movies

    async def bms_get_showtimes(self) -> list[dict]:
        """Extract showtimes from the current page."""
        await asyncio.sleep(2)
        showtimes = await self.page.evaluate("""
            () => {
                const shows = [];
                // BookMyShow showtime buttons
                document.querySelectorAll('[data-online="Y"], .showtime-pill, ._showtime, [class*="showtime"]').forEach((el, i) => {
                    const time = el.textContent?.trim() || '';
                    const dataId = el.getAttribute('data-id') || el.getAttribute('id') || '';
                    if (time && /\\d{1,2}[:.:]\\d{2}/.test(time)) {
                        shows.push({ index: i, time: time, id: dataId });
                    }
                });
                return shows;
            }
        """)
        return showtimes

    async def bms_open_seat_layout(self, showtime_element_index: int) -> str:
        """Click a showtime to open the seat layout. Returns screenshot path."""
        # Click the showtime
        elements = await self.page.query_selector_all(
            '[data-online="Y"], .showtime-pill, ._showtime, [class*="showtime"]'
        )
        if showtime_element_index < len(elements):
            await elements[showtime_element_index].click()
            await asyncio.sleep(3)

        # Handle ticket count popup if it appears
        return await self.screenshot("seat_layout")

    async def bms_select_ticket_count(self, count: int):
        """Select the number of tickets in the popup."""
        try:
            # Look for the ticket count selector
            count_btn = await self.page.query_selector(
                f'[data-value="{count}"], button:has-text("{count}")'
            )
            if count_btn:
                await count_btn.click()
                await asyncio.sleep(1)

            # Click "Select Seats" or equivalent button
            select_btn = await self.page.query_selector(
                'button:has-text("Select Seats"), button:has-text("Proceed"), '
                'button:has-text("select seat"), [class*="proceed"]'
            )
            if select_btn:
                await select_btn.click()
                await asyncio.sleep(3)
        except Exception as e:
            log.error(f"Error selecting ticket count: {e}")

    async def bms_get_seat_layout(self) -> dict:
        """Parse the seat layout and return available seats with positions."""
        await asyncio.sleep(2)
        layout = await self.page.evaluate("""
            () => {
                const seats = [];
                const seatElements = document.querySelectorAll(
                    '[class*="seat"]:not([class*="blocked"]):not([class*="sold"]), ' +
                    'a[id^="s_"], div[data-seat-number]'
                );
                
                seatElements.forEach(el => {
                    const rect = el.getBoundingClientRect();
                    const classes = el.className || '';
                    const id = el.id || el.getAttribute('data-seat-number') || '';
                    const text = el.textContent?.trim() || '';
                    
                    // Determine seat status
                    const isAvailable = !classes.includes('sold') && 
                                       !classes.includes('blocked') && 
                                       !classes.includes('unavailable') &&
                                       !classes.includes('booked');
                    const isSelected = classes.includes('selected') || 
                                      classes.includes('active');
                    
                    if (rect.width > 5 && rect.height > 5) {
                        seats.push({
                            id: id,
                            text: text,
                            x: rect.x + rect.width / 2,
                            y: rect.y + rect.height / 2,
                            width: rect.width,
                            height: rect.height,
                            available: isAvailable,
                            selected: isSelected,
                            classes: classes.substring(0, 100),
                        });
                    }
                });
                
                // Get screen/stage position for reference
                const screen = document.querySelector(
                    '[class*="screen"], [class*="Screen"], [class*="stage"]'
                );
                const screenRect = screen ? screen.getBoundingClientRect() : null;
                
                return {
                    seats: seats,
                    screenCenter: screenRect ? screenRect.x + screenRect.width / 2 : null,
                    totalSeats: seats.length,
                    availableSeats: seats.filter(s => s.available).length,
                };
            }
        """)
        return layout

    async def select_best_seats(self, count: int) -> dict:
        """Select the best center seats from the available layout.
        
        Algorithm:
        1. Get all available seats
        2. Group seats by row (similar Y coordinate)
        3. For each row, find all groups of `count` consecutive seats
        4. Score each group by distance from center (X) and ideal row (Y middle third)
        5. Click the best-scoring group
        
        Returns info about selected seats.
        """
        layout = await self.bms_get_seat_layout()
        available = [s for s in layout["seats"] if s["available"]]

        if len(available) < count:
            return {
                "success": False,
                "message": f"Only {len(available)} seats available, need {count}",
            }

        # Determine screen center (X-axis midpoint)
        if layout["screenCenter"]:
            center_x = layout["screenCenter"]
        else:
            all_x = [s["x"] for s in layout["seats"]]
            center_x = (min(all_x) + max(all_x)) / 2

        # Group seats by row (Y coordinate, with tolerance)
        rows = {}
        for seat in available:
            row_key = round(seat["y"] / 30) * 30  # Group within 30px
            if row_key not in rows:
                rows[row_key] = []
            rows[row_key].append(seat)

        # Sort seats within each row by X position
        for row_key in rows:
            rows[row_key].sort(key=lambda s: s["x"])

        # Calculate ideal Y (middle third of theater)
        all_y = sorted(rows.keys())
        if len(all_y) >= 3:
            third = len(all_y) // 3
            ideal_y_range = all_y[third: 2 * third + 1]
            ideal_y = sum(ideal_y_range) / len(ideal_y_range)
        else:
            ideal_y = sum(all_y) / len(all_y)

        # Find best consecutive group
        best_group = None
        best_score = float("inf")

        for row_key, seats in rows.items():
            if len(seats) < count:
                continue

            for i in range(len(seats) - count + 1):
                group = seats[i: i + count]

                # Check if seats are actually consecutive (within reasonable gap)
                consecutive = True
                for j in range(1, len(group)):
                    gap = group[j]["x"] - group[j - 1]["x"]
                    if gap > group[0]["width"] * 2.5:  # Allow some gap tolerance
                        consecutive = False
                        break

                if not consecutive:
                    continue

                # Score: distance from center (X) + distance from ideal row (Y)
                group_center_x = sum(s["x"] for s in group) / len(group)
                x_distance = abs(group_center_x - center_x)
                y_distance = abs(row_key - ideal_y)
                score = x_distance * 1.5 + y_distance  # Weight X more (center matters more)

                if score < best_score:
                    best_score = score
                    best_group = group

        if not best_group:
            return {
                "success": False,
                "message": f"Could not find {count} consecutive seats in any row.",
            }

        # Click each seat in the best group
        selected_names = []
        for seat in best_group:
            try:
                await self.page.mouse.click(seat["x"], seat["y"])
                selected_names.append(seat.get("text") or seat.get("id", "?"))
                await asyncio.sleep(0.3)
            except Exception as e:
                log.error(f"Failed to click seat: {e}")

        await asyncio.sleep(1)
        screenshot_path = await self.screenshot("seats_selected")

        return {
            "success": True,
            "seats": selected_names,
            "count": len(selected_names),
            "screenshot": screenshot_path,
            "message": f"✅ Selected {len(selected_names)} seats: {', '.join(selected_names)}",
        }

    async def refresh_and_reselect(self, count: int) -> dict:
        """Refresh the page and re-select seats (for the 15-min cycle)."""
        await self.page.reload(wait_until="domcontentloaded")
        await asyncio.sleep(3)
        return await self.select_best_seats(count)


# ──────────────────────────────────────────────────────────────────────
# Sync wrapper for use from threading context
# ──────────────────────────────────────────────────────────────────────

def run_async(coro):
    """Run an async coroutine from sync code (thread-safe)."""
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as pool:
                future = pool.submit(asyncio.run, coro)
                return future.result()
        else:
            return loop.run_until_complete(coro)
    except RuntimeError:
        return asyncio.run(coro)
