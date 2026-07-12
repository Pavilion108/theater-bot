"""
🎭 Seat Selector Engine — Selenium Edition (Paytm Movies)

Controls a stealth Chromium browser to navigate Paytm Movies,
parse seat layouts, and select optimal center seats.
"""

import logging
import os
import time
from pathlib import Path
import urllib.parse

import undetected_chromedriver as uc
from selenium.webdriver.common.by import By

from cookie_manager import load_cookies

log = logging.getLogger("TheaterBot")

SCREENSHOTS_DIR = Path(__file__).parent / "data" / "screenshots"
SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)


class SeatSelector:
    def __init__(self):
        self.driver = None
        self._started = False
        self.platform = "paytm"

    def start(self):
        """Launch the stealth browser."""
        if self._started:
            return

        options = uc.ChromeOptions()
        # Paytm is less strict than BMS, but we still try to be stealthy
        options.add_argument("--headless=new")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--disable-gpu")
        options.add_argument("--window-size=1920,1080")
        
        # User agent spoofing
        options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

        # Detect system Chromium (for Alpine Linux compatibility)
        executable_path = None
        
        import glob
        pw_paths = glob.glob("/ms-playwright/chromium-*/chrome-linux/chrome")
        
        for path in ["/usr/bin/chromium-browser", "/usr/bin/chromium"] + pw_paths:
            if os.path.exists(path):
                executable_path = path
                break
                
        if executable_path:
            options.binary_location = executable_path
            log.info(f"Using system Chromium at {executable_path}")

        driver_executable_path = None
        for path in ["/usr/bin/chromedriver", "/usr/lib/chromium/chromedriver"]:
            if os.path.exists(path):
                driver_executable_path = path
                break

        kwargs = {
            "options": options,
            "headless": True,
            "use_subprocess": True,
            "version_main": 123
        }
        if executable_path:
            kwargs["browser_executable_path"] = executable_path
        if driver_executable_path:
            kwargs["driver_executable_path"] = driver_executable_path

        self.driver = uc.Chrome(**kwargs)
        
        # Additional stealth via CDP
        self.driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {
            "source": """
                Object.defineProperty(navigator, 'webdriver', { get: () => undefined })
            """
        })

        self._started = True
        log.info("🎭 Stealth browser (undetected_chromedriver) started successfully")

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
        """Open Paytm Movies search for a theater (Method name kept for compatibility)."""
        city_slug = city.lower().replace(" ", "") if city else "mumbai"
        query = urllib.parse.quote(theater_name)
        
        # Using Bing search to reliably find the theater without Google's login overlays
        url = f"https://www.bing.com/search?q=site:paytm.com/movies+{query}+{city_slug}"
        self.driver.get(url)
        time.sleep(3)
        
        try:
            links = self.driver.find_elements(By.CSS_SELECTOR, 'a[href*="paytm.com/movies"]')
            if links:
                # Bing sometimes opens in new tab, we just grab the URL and navigate manually
                target_url = links[0].get_attribute("href")
                if target_url:
                    self.driver.get(target_url)
                    time.sleep(4)
            else:
                self.driver.get("https://paytm.com/movies")
                time.sleep(4)
        except Exception as e:
            log.error(f"Failed to search: {e}")
            self.driver.get("https://paytm.com/movies")
            time.sleep(4)
            
        return self.screenshot("paytm_theaters")

    def bms_get_movies(self, theater_url: str) -> list[dict]:
        """Get list of movies (Generic heuristic parser)."""
        time.sleep(3)
        
        movies = self.driver.execute_script("""
            const movies = [];
            document.querySelectorAll('a[href*="/movies/"], [class*="movie-title"], h3').forEach((el, i) => {
                const name = el.textContent?.trim()?.split('\\n')[0] || '';
                const href = el.tagName === 'A' ? el.getAttribute('href') : '';
                if (name && name.length > 2 && name.length < 50) {
                    const badWords = ['Home', 'Movies', 'Events', 'Offers', 'Login', 'Sign', 'About', 'Contact', 'Paytm', 'Download'];
                    if (!badWords.some(w => name.toLowerCase().includes(w.toLowerCase())) && !movies.find(m => m.name === name)) {
                        movies.push({ index: movies.length, name: name, url: href });
                    }
                }
            });
            return movies;
        """)
        return movies

    def bms_get_dates(self) -> list[dict]:
        """Extract available date tabs."""
        time.sleep(1)
        return self.driver.execute_script("""
            const dates = [];
            document.querySelectorAll('a, button, div, li, span').forEach((el, i) => {
                const text = el.textContent?.trim() || '';
                const classes = (el.className || '').toLowerCase();
                if (text.length > 2 && text.length < 15 && (text.includes('Today') || text.includes('Tomorrow') || text.includes('Tom') || /^(Mon|Tue|Wed|Thu|Fri|Sat|Sun)/.test(text))) {
                    if (el.tagName === 'A' || el.tagName === 'BUTTON' || el.tagName === 'LI' || classes.includes('date') || classes.includes('day')) {
                        if (!dates.find(d => d.text === text) && el.getBoundingClientRect().width > 0) {
                            dates.push({ index: dates.length, text: text, id: i });
                        }
                    }
                }
            });
            return dates;
        """)

    def bms_click_date(self, date_id: int):
        """Click a specific date tab using its DOM element index."""
        self.driver.execute_script(f"""
            const els = document.querySelectorAll('a, button, div, li, span');
            if (els[{date_id}]) {{
                els[{date_id}].click();
            }}
        """)
        time.sleep(3)

    def bms_get_showtimes(self) -> list[dict]:
        """Extract showtimes from the current page."""
        time.sleep(3)
        shows = self.driver.execute_script("""
            const shows = [];
            document.querySelectorAll('a, button, div').forEach((el, i) => {
                const time = el.textContent?.trim() || '';
                // Look for things like "10:30 AM" or "14:45"
                if (time.length < 10 && /\\d{1,2}[:.:]\\d{2}/.test(time)) {
                    // Make sure it's clickable (has a click handler or is a button/link)
                    if (el.tagName === 'A' || el.tagName === 'BUTTON' || el.className.toLowerCase().includes('time')) {
                        shows.push({ index: shows.length, time: time, id: i });
                    }
                }
            });
            return shows;
        """)
        return shows

    def bms_open_seat_layout(self, showtime_element_index: int) -> str:
        """Click a showtime to open the seat layout."""
        try:
            # Re-run the same selector logic in python to find the element
            shows_script = """
                const els = [];
                document.querySelectorAll('a, button, div').forEach((el, i) => {
                    const time = el.textContent?.trim() || '';
                    if (time.length < 10 && /\\d{1,2}[:.:]\\d{2}/.test(time)) {
                        if (el.tagName === 'A' || el.tagName === 'BUTTON' || el.className.toLowerCase().includes('time')) {
                            els.push(el);
                        }
                    }
                });
                if(els.length > arguments[0]) {
                    els[arguments[0]].click();
                    return true;
                }
                return false;
            """
            self.driver.execute_script(shows_script, showtime_element_index)
            time.sleep(5)
        except Exception as e:
            log.error(f"Error clicking showtime: {e}")
            
        return self.screenshot("paytm_seat_layout")

    def bms_select_ticket_count(self, count: int):
        """Select the number of tickets in the popup."""
        try:
            # Generic clicker for ticket counts
            self.driver.execute_script(f"""
                document.querySelectorAll('li, div, button').forEach(el => {{
                    if (el.textContent.trim() === '{count}' && el.getBoundingClientRect().width > 10) {{
                        el.click();
                    }}
                }});
                
                setTimeout(() => {{
                    document.querySelectorAll('button').forEach(el => {{
                        if (el.textContent.toLowerCase().includes('proceed') || el.textContent.toLowerCase().includes('select')) {{
                            el.click();
                        }}
                    }});
                }}, 1000);
            """)
            time.sleep(3)
        except Exception:
            pass

    def bms_get_seat_layout(self) -> dict:
        """Parse the seat layout."""
        time.sleep(3)
        return self.driver.execute_script("""
            const seats = [];
            // Generic seat finder: looks for small boxy elements
            document.querySelectorAll('div, span, button, a').forEach(el => {
                const rect = el.getBoundingClientRect();
                const classes = (el.className || '').toLowerCase();
                const text = el.textContent?.trim() || '';
                
                // Usually seats are ~20-40px wide squares
                if (rect.width > 15 && rect.width < 50 && rect.height > 15 && rect.height < 50) {
                    // Ignore things that look like navigation/UI
                    if (!['+', '-', '<', '>'].includes(text)) {
                        const isAvailable = !classes.includes('sold') && !classes.includes('block') && !classes.includes('book');
                        
                        seats.push({
                            id: '', // DOM elements are harder to ID uniquely without standard classes
                            text: text,
                            x: rect.x + rect.width / 2,
                            y: rect.y + rect.height / 2,
                            width: rect.width,
                            height: rect.height,
                            available: isAvailable
                        });
                    }
                }
            });
            return {
                seats: seats,
                screenCenter: null
            };
        """)

    def select_best_seats(self, count: int) -> dict:
        """Select the best center seats."""
        layout = self.bms_get_seat_layout()
        available = [s for s in layout["seats"] if s["available"]]

        if len(available) < count:
            return {"success": False, "message": f"Only {len(available)} seats available (or parsing failed)."}

        all_x = [s["x"] for s in layout["seats"]]
        center_x = (min(all_x) + max(all_x)) / 2 if all_x else 0

        rows = {}
        for seat in available:
            row_key = round(seat["y"] / 20) * 20  # Group within 20px
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
                # Use JS to click by coordinates since generic parsing loses DOM references
                self.driver.execute_script(f"""
                    const el = document.elementFromPoint({seat['x']}, {seat['y']});
                    if (el) el.click();
                """)
                selected_names.append(seat.get("text") or "?")
                time.sleep(0.5)
            except Exception:
                pass

        time.sleep(2)
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
        time.sleep(5)
        return self.select_best_seats(count)
