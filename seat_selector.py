"""
🎭 Seat Selector Engine — Selenium Edition (Alpine Compatible)

Controls a stealth Chromium browser to navigate booking platforms,
parse seat layouts, and select optimal center seats.
"""

import logging
import os
import time
from pathlib import Path

from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

from cookie_manager import load_cookies

log = logging.getLogger("TheaterBot")

SCREENSHOTS_DIR = Path(__file__).parent / "data" / "screenshots"
SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)


class SeatSelector:
    def __init__(self):
        self.driver = None
        self._started = False

    def start(self):
        """Launch the stealth browser."""
        if self._started:
            return

        options = Options()
        options.add_argument("--headless=new")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--disable-gpu")
        options.add_argument("--window-size=1920,1080")
        options.add_argument("--disable-blink-features=AutomationControlled")
        options.add_experimental_option("excludeSwitches", ["enable-automation"])
        options.add_experimental_option("useAutomationExtension", False)

        # Detect system Chromium (for Alpine Linux compatibility)
        executable_path = None
        for path in ["/usr/bin/chromium-browser", "/usr/bin/chromium"]:
            if os.path.exists(path):
                executable_path = path
                break
                
        if executable_path:
            options.binary_location = executable_path
            log.info(f"Using system Chromium at {executable_path}")

        service = Service()
        for path in ["/usr/bin/chromedriver", "/usr/lib/chromium/chromedriver"]:
            if os.path.exists(path):
                service = Service(executable_path=path)
                break

        self.driver = webdriver.Chrome(service=service, options=options)
        
        # Additional stealth via CDP
        self.driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {
            "source": """
                Object.defineProperty(navigator, 'webdriver', {
                  get: () => undefined
                })
            """
        })

        self.driver.get("https://in.bookmyshow.com")
        
        # Load saved cookies
        cookies = load_cookies()
        if cookies:
            for c in cookies:
                try:
                    self.driver.add_cookie({
                        "name": c["name"],
                        "value": c["value"],
                        "domain": c["domain"],
                        "path": c.get("path", "/")
                    })
                except Exception as e:
                    pass
            log.info(f"Loaded cookies into browser session")

        self._started = True
        log.info("🎭 Stealth browser started successfully")

    def stop(self):
        """Close the browser."""
        if self.driver:
            self.driver.quit()
        self._started = False
        log.info("Browser closed")

    def screenshot(self, name: str = "page") -> str:
        """Take a screenshot and return the file path."""
        filepath = str(SCREENSHOTS_DIR / f"{name}_{int(time.time())}.png")
        if self.driver:
            self.driver.save_screenshot(filepath)
            log.info(f"📸 Screenshot saved: {filepath}")
            return filepath
        return ""

    def bms_open_theater(self, theater_name: str, city: str = ""):
        """Open BookMyShow and search for a theater."""
        city_slug = city.lower().replace(" ", "-") if city else "mumbai"
        url = f"https://in.bookmyshow.com/explore/cinemas-{city_slug}"
        self.driver.get(url)
        time.sleep(3)
        return self.screenshot("bms_theaters")

    def bms_get_movies(self, theater_url: str) -> list[dict]:
        """Get list of movies playing at a theater."""
        self.driver.get(theater_url)
        time.sleep(3)
        
        movies = self.driver.execute_script("""
            const movies = [];
            document.querySelectorAll('a[href*="/movies/"], a[href*="buytickets"]').forEach((el, i) => {
                const name = el.textContent?.trim()?.split('\\n')[0] || '';
                const href = el.getAttribute('href') || '';
                if (name && name.length > 2 && name.length < 100) {
                    if (!movies.find(m => m.name === name)) {
                        movies.push({ index: i, name: name, url: href });
                    }
                }
            });
            return movies;
        """)
        return movies

    def bms_get_showtimes(self) -> list[dict]:
        """Extract showtimes from the current page."""
        time.sleep(2)
        shows = self.driver.execute_script("""
            const shows = [];
            document.querySelectorAll('[data-online="Y"], .showtime-pill, ._showtime, [class*="showtime"]').forEach((el, i) => {
                const time = el.textContent?.trim() || '';
                if (time && /\\d{1,2}[:.:]\\d{2}/.test(time)) {
                    shows.push({ index: i, time: time });
                }
            });
            return shows;
        """)
        return shows

    def bms_open_seat_layout(self, showtime_element_index: int) -> str:
        """Click a showtime to open the seat layout. Returns screenshot path."""
        try:
            elements = self.driver.find_elements(By.CSS_SELECTOR, '[data-online="Y"], .showtime-pill, ._showtime, [class*="showtime"]')
            if showtime_element_index < len(elements):
                elements[showtime_element_index].click()
                time.sleep(4)
        except Exception as e:
            log.error(f"Error clicking showtime: {e}")
            
        return self.screenshot("seat_layout")

    def bms_select_ticket_count(self, count: int):
        """Select the number of tickets in the popup."""
        try:
            btn = self.driver.find_elements(By.CSS_SELECTOR, f'[data-value="{count}"], button:has-text("{count}")')
            if btn:
                btn[0].click()
                time.sleep(1)
            
            select_btn = self.driver.find_elements(By.CSS_SELECTOR, 'button:has-text("Select Seats"), button:has-text("Proceed"), [id*="proceed"]')
            if select_btn:
                select_btn[0].click()
                time.sleep(3)
        except Exception:
            pass

    def bms_get_seat_layout(self) -> dict:
        """Parse the seat layout and return available seats with positions."""
        time.sleep(2)
        return self.driver.execute_script("""
            const seats = [];
            document.querySelectorAll('a[id^="s_"], div[data-seat-number]').forEach(el => {
                const rect = el.getBoundingClientRect();
                const classes = el.className || '';
                const id = el.id || el.getAttribute('data-seat-number') || '';
                
                const isAvailable = !classes.includes('sold') && !classes.includes('blocked');
                
                if (rect.width > 5) {
                    seats.push({
                        id: id,
                        text: el.textContent?.trim() || '',
                        x: rect.x + rect.width / 2,
                        y: rect.y + rect.height / 2,
                        width: rect.width,
                        height: rect.height,
                        available: isAvailable
                    });
                }
            });
            return {
                seats: seats,
                screenCenter: null // Simplification
            };
        """)

    def select_best_seats(self, count: int) -> dict:
        """Select the best center seats."""
        layout = self.bms_get_seat_layout()
        available = [s for s in layout["seats"] if s["available"]]

        if len(available) < count:
            return {"success": False, "message": f"Only {len(available)} seats available."}

        all_x = [s["x"] for s in layout["seats"]]
        center_x = (min(all_x) + max(all_x)) / 2 if all_x else 0

        rows = {}
        for seat in available:
            row_key = round(seat["y"] / 30) * 30
            if row_key not in rows: rows[row_key] = []
            rows[row_key].append(seat)

        for r in rows: rows[r].sort(key=lambda s: s["x"])

        best_group = None
        best_score = float("inf")

        for row_key, seats in rows.items():
            if len(seats) < count: continue
            for i in range(len(seats) - count + 1):
                group = seats[i: i + count]
                group_center_x = sum(s["x"] for s in group) / len(group)
                score = abs(group_center_x - center_x)

                if score < best_score:
                    best_score = score
                    best_group = group

        if not best_group:
            return {"success": False, "message": "No consecutive seats found."}

        selected_names = []
        for seat in best_group:
            try:
                el = self.driver.find_element(By.ID, seat["id"])
                el.click()
                selected_names.append(seat.get("text") or seat.get("id"))
                time.sleep(0.5)
            except Exception:
                pass

        time.sleep(1)
        screenshot_path = self.screenshot("seats_selected")

        return {
            "success": True,
            "seats": selected_names,
            "count": len(selected_names),
            "screenshot": screenshot_path,
            "message": f"✅ Selected {len(selected_names)} seats: {', '.join(selected_names)}"
        }

    def refresh_and_reselect(self, count: int) -> dict:
        self.driver.refresh()
        time.sleep(4)
        return self.select_best_seats(count)
