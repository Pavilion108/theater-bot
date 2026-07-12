import os
import time
import logging
import undetected_chromedriver as uc
from selenium.webdriver.common.by import By

from cookie_manager import load_cookies

log = logging.getLogger("TheaterBot")

def _inject_cookies(driver, domain):
    cookies = load_cookies()
    domain_cookies = [c for c in cookies if domain in c.get('domain', '')]
    if not domain_cookies:
        log.warning(f"No cookies found for {domain}. Gemini may require login.")
        return False
        
    for c in domain_cookies:
        try:
            driver.add_cookie(c)
        except Exception as e:
            pass
    return True

def query_gemini_web(file_path: str, prompt: str) -> str:
    """Uses Selenium to query gemini.google.com directly to bypass API limits/issues."""
    options = uc.ChromeOptions()
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--window-size=1920,1080")
    
    # Try finding system chromium first for linux compatibility
    executable_path = None
    
    # Also search Playwright's internal directory if present in Docker
    import glob
    pw_paths = glob.glob("/ms-playwright/chromium-*/chrome-linux/chrome")
    
    for path in ["/usr/bin/chromium-browser", "/usr/bin/chromium"] + pw_paths:
        if os.path.exists(path):
            executable_path = path
            break
            
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

    try:
        driver = uc.Chrome(**kwargs)
    except Exception as e:
        log.error(f"Failed to launch Chrome: {e}")
        return f"Error: Failed to launch browser: {e}"
        
    try:
        # Load google to set cookies
        driver.get("https://google.com/")
        has_cookies = _inject_cookies(driver, "google.com")
        
        # Navigate to Gemini
        driver.get("https://gemini.google.com/app")
        time.sleep(5)
        
        if not has_cookies:
            # Maybe check if we are on login screen
            if "signin" in driver.current_url.lower():
                return "Error: Gemini Web requires authentication. Please export your google.com cookies using /cookies command."
        
        # Try to find file input and upload
        try:
            # First, try to click the "+" or "Upload" button to ensure the file input is in the DOM
            try:
                upload_btn = driver.find_element(By.CSS_SELECTOR, 'button[aria-label*="Upload"], button[aria-label*="Attach"], button.upload-button, span.upload-icon')
                driver.execute_script("arguments[0].click();", upload_btn)
                time.sleep(1)
            except:
                pass
                
            file_inputs = driver.find_elements(By.CSS_SELECTOR, 'input[type="file"]')
            for fi in file_inputs:
                try:
                    fi.send_keys(os.path.abspath(file_path))
                except:
                    pass
            log.info("Image attached, waiting 8 seconds for upload to process...")
            time.sleep(8) # Wait for image upload thumbnail to render
        except Exception as e:
            log.warning(f"Could not interact with file input on Gemini web: {e}")
            
        # Find prompt input
        try:
            chat_input = driver.find_element(By.CSS_SELECTOR, "div.text-input-field p, div.ql-editor, div[contenteditable='true']")
            chat_input.send_keys(prompt)
            time.sleep(1)
        except Exception as e:
            return f"Error: Could not interact with Gemini chat input: {e}"
            
        # Submit
        try:
            # Usually there is a button with an aria label for sending
            send_btn = driver.find_element(By.CSS_SELECTOR, 'button[aria-label*="Send"], button[aria-label*="Submit"], .send-button')
            driver.execute_script("arguments[0].click();", send_btn)
        except:
            # Fallback to pressing enter
            try:
                from selenium.webdriver.common.keys import Keys
                chat_input.send_keys(Keys.ENTER)
            except:
                pass
            
        log.info("Prompt sent to Gemini Web, waiting for response...")
        
        # Wait up to 45 seconds for a response to appear
        for i in range(45):
            time.sleep(1)
            # More robust selectors for Gemini's response
            selectors = [
                "message-content", 
                ".model-response-text",
                "div[data-message-author-role='model']",
                ".markdown"
            ]
            
            for selector in selectors:
                responses = driver.find_elements(By.CSS_SELECTOR, selector)
                # Check if it actually has text and we've waited a bit for it to type out
                if responses and len(responses[-1].text) > 10:
                    # Found a response! Let's wait another 5 seconds for it to finish typing completely
                    time.sleep(5)
                    # Re-fetch the final text
                    final_responses = driver.find_elements(By.CSS_SELECTOR, selector)
                    return final_responses[-1].text
        
        # If we reach here, we failed. Let's dump the HTML for debugging!
        try:
            log.error("Failed to find response. Dumping page source snippet for debugging:")
            log.error(driver.page_source[-1000:]) # log last 1000 chars or save to file
            with open("gemini_error_dump.html", "w", encoding="utf-8") as f:
                f.write(driver.page_source)
        except:
            pass
            
        return "Error: No response generated by Gemini web after waiting. (DOM might have changed). View HTML dump at: https://jackbot-24-7.onrender.com/dump"
            
    except Exception as e:
        log.error(f"Error interacting with Gemini web: {e}")
        return f"Error: {e}"
        
    finally:
        try:
            driver.quit()
        except:
            pass
