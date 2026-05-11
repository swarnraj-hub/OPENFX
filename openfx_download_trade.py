"""
OpenFX — Trade History Export Automation
"""

import json
import os
import pickle
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import pyotp

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException
from selenium_stealth import stealth
from webdriver_manager.chrome import ChromeDriverManager

# ─────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────

EMAIL    = os.getenv("OPENFX_EMAIL",    "")
PASSWORD = os.getenv("OPENFX_PASSWORD", "")

HEADLESS = os.getenv("OPENFX_HEADLESS", "true").lower() == "true"

SESSION_FILE   = os.getenv("OPENFX_SESSION_FILE",   "openfx_session.pkl")
SCREENSHOT_DIR = os.getenv("OPENFX_SCREENSHOT_DIR", "screenshots")

BASE_URL  = "https://app.openfx.com"
TRADE_URL = f"{BASE_URL}/trade"

Path(SCREENSHOT_DIR).mkdir(exist_ok=True)

_secret_file = Path("totp_secret.txt")
TOTP_SECRET  = (
    os.getenv("OPENFX_TOTP_SECRET")
    or (_secret_file.read_text().strip() if _secret_file.exists() else "")
)

END_DATE   = date.today()
START_DATE = END_DATE - timedelta(days=10)


# ─────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────

def log(msg):
    print(msg, file=sys.stderr)


def screenshot(driver, name):
    path = f"{SCREENSHOT_DIR}/{name}_{datetime.now().strftime('%H%M%S')}.png"
    try:
        driver.save_screenshot(path)
    except Exception:
        pass
    return path


def get_totp():
    if not TOTP_SECRET:
        raise Exception("OPENFX_TOTP_SECRET missing")
    totp      = pyotp.TOTP(TOTP_SECRET)
    remaining = totp.interval - (int(time.time()) % totp.interval)
    if remaining < 5:
        log(f"[INFO] Waiting {remaining}s for fresh TOTP")
        time.sleep(remaining + 1)
    return totp.now()


def save_session(driver):
    try:
        with open(SESSION_FILE, "wb") as f:
            pickle.dump(driver.get_cookies(), f)
        log("[INFO] Session saved")
    except Exception as e:
        log(f"[WARN] Failed saving session: {e}")


def load_session(driver):
    if not Path(SESSION_FILE).exists():
        return False
    try:
        driver.get(BASE_URL)
        time.sleep(3)
        with open(SESSION_FILE, "rb") as f:
            cookies = pickle.load(f)
        for cookie in cookies:
            try:
                driver.add_cookie(cookie)
            except Exception:
                pass
        log("[INFO] Session restored")
        return True
    except Exception as e:
        log(f"[WARN] Session restore failed: {e}")
        return False


def wait_cloudflare(driver, timeout=120):
    log("[INFO] Waiting for Cloudflare")
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            btn = driver.find_element(
                By.CSS_SELECTOR,
                "[data-testid='sign-in-continue-button']"
            )
            disabled = (
                btn.get_attribute("disabled")
                or btn.get_attribute("aria-disabled") == "true"
            )
            if not disabled:
                return True
        except Exception:
            pass
        time.sleep(1)
    return False


# ─────────────────────────────────────────────
# LOGIN
# ─────────────────────────────────────────────

def login(driver):
    log("[INFO] Opening login page")
    driver.get(f"{BASE_URL}/sign-in")
    time.sleep(5)

    WebDriverWait(driver, 30).until(
        EC.presence_of_element_located((By.CSS_SELECTOR, "input[type='email']"))
    ).send_keys(EMAIL)

    WebDriverWait(driver, 30).until(
        EC.presence_of_element_located((By.CSS_SELECTOR, "input[type='password']"))
    ).send_keys(PASSWORD)

    if not wait_cloudflare(driver):
        raise Exception("Cloudflare timeout")

    WebDriverWait(driver, 30).until(
        EC.element_to_be_clickable(
            (By.CSS_SELECTOR, "[data-testid='sign-in-continue-button']")
        )
    ).click()

    log("[INFO] Login submitted")
    time.sleep(5)

    code     = get_totp()
    otp_done = False

    # Option A: 6 individual digit boxes
    try:
        boxes = driver.find_elements(By.CSS_SELECTOR, "input[maxlength='1']")
        if len(boxes) >= 6:
            for i, digit in enumerate(code[:6]):
                boxes[i].send_keys(digit)
            otp_done = True
    except Exception:
        pass

    # Option B: single OTP input
    if not otp_done:
        for sel in ["input[maxlength='6']", "input[name*='otp']",
                    "input[placeholder*='code' i]"]:
            try:
                f = WebDriverWait(driver, 5).until(
                    EC.presence_of_element_located((By.CSS_SELECTOR, sel))
                )
                f.clear()
                f.send_keys(code)
                otp_done = True
                break
            except Exception:
                pass

    if not otp_done:
        raise Exception("OTP field not found")

    time.sleep(2)
    try:
        driver.find_element(By.CSS_SELECTOR, "button[type='submit']").click()
    except Exception:
        pass

    time.sleep(10)
    save_session(driver)
    log("[INFO] Login successful")


# ─────────────────────────────────────────────
# DRIVER
# ─────────────────────────────────────────────

def build_driver():
    options = Options()

    options.add_argument("--window-size=1920,1080")
    options.add_argument("--start-maximized")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("--disable-features=VizDisplayCompositor")
    options.add_argument("--disable-features=IsolateOrigins,site-per-process")
    options.add_argument("--disable-popup-blocking")
    options.add_argument("--lang=en-US")
    options.add_argument("--ignore-certificate-errors")
    options.add_argument("--allow-running-insecure-content")

    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)

    # No headless — xvfb provides the virtual display on CI.
    # --headless=new causes Chrome renderer crashes on ubuntu-latest.

    options.add_experimental_option("prefs", {
        "download.prompt_for_download": False,
        "download.directory_upgrade": True,
        "credentials_enable_service": False,
        "profile.password_manager_enabled": False,
        "profile.default_content_setting_values.notifications": 2,
    })

    log("[INFO] Starting Chrome")

    driver = webdriver.Chrome(
        service=Service(ChromeDriverManager().install()),
        options=options,
    )

    # Remove navigator.webdriver fingerprint
    stealth(
        driver,
        languages=["en-US", "en"],
        vendor="Google Inc.",
        platform="Win32",
        webgl_vendor="Intel Inc.",
        renderer="Intel Iris OpenGL Engine",
        fix_hairline=True,
    )

    driver.execute_script(
        "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
    )

    driver.implicitly_wait(10)
    driver.set_page_load_timeout(120)

    return driver


# ─────────────────────────────────────────────
# EXPORT FLOW
# ─────────────────────────────────────────────

def click_export_button(driver):
    for sel in ["[data-testid*='export' i]", "[aria-label*='export' i]",
                "[aria-label*='download' i]", "[title*='export' i]",
                "[title*='download' i]"]:
        try:
            btn = WebDriverWait(driver, 5).until(
                EC.element_to_be_clickable((By.CSS_SELECTOR, sel))
            )
            driver.execute_script("arguments[0].scrollIntoView({block:'center'})", btn)
            time.sleep(1)
            btn.click()
            return True
        except Exception:
            pass
    return False


def click_custom_dates(driver):
    for xpath in ["//*[contains(text(),'Custom dates')]",
                  "//*[contains(text(),'Custom')]"]:
        try:
            el = WebDriverWait(driver, 5).until(
                EC.element_to_be_clickable((By.XPATH, xpath))
            )
            el.click()
            return True
        except Exception:
            pass
    return False


# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────

def main():
    result = {
        "success":    False,
        "message":    "",
        "start_date": START_DATE.isoformat(),
        "end_date":   END_DATE.isoformat(),
        "error":      "",
        "screenshots": [],
    }

    driver = None

    try:
        log(f"[INFO] Date Range: {START_DATE} -> {END_DATE}")

        driver = build_driver()

        restored = load_session(driver)

        driver.get(TRADE_URL)
        time.sleep(6)

        current_url = driver.current_url.lower()

        if not restored or "login" in current_url or "sign-in" in current_url:
            log("[INFO] Fresh login required")
            login(driver)
            driver.get(TRADE_URL)
            time.sleep(6)

        result["screenshots"].append(screenshot(driver, "01_trade_page"))

        log("[INFO] Searching export button")
        if not click_export_button(driver):
            raise Exception("Export button not found")

        time.sleep(3)
        result["screenshots"].append(screenshot(driver, "02_export_menu"))

        log("[INFO] Searching custom dates")
        if not click_custom_dates(driver):
            raise Exception("Custom dates option not found")

        time.sleep(3)
        result["screenshots"].append(screenshot(driver, "03_custom_dates"))

        result["success"] = True
        result["message"] = (
            f"OpenFX export flow completed ({START_DATE} -> {END_DATE})"
        )
        log("[INFO] Export flow completed")

    except Exception as e:
        result["error"] = str(e)
        log(f"[ERROR] {e}")
        if driver:
            try:
                result["screenshots"].append(screenshot(driver, "error"))
            except Exception:
                pass
    finally:
        if driver:
            try:
                driver.quit()
            except Exception:
                pass

    print(json.dumps(result, indent=2))
    sys.exit(0 if result["success"] else 1)


if __name__ == "__main__":
    main()
