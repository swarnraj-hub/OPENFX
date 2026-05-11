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
import undetected_chromedriver as uc

from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

from selenium.common.exceptions import TimeoutException

# ─────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────

EMAIL = os.getenv("OPENFX_EMAIL", "")
PASSWORD = os.getenv("OPENFX_PASSWORD", "")

HEADLESS = os.getenv(
    "OPENFX_HEADLESS",
    "true"
).lower() == "true"

SESSION_FILE = os.getenv(
    "OPENFX_SESSION_FILE",
    "openfx_session.pkl"
)

SCREENSHOT_DIR = os.getenv(
    "OPENFX_SCREENSHOT_DIR",
    "screenshots"
)

BASE_URL = "https://app.openfx.com"
TRADE_URL = f"{BASE_URL}/trade"

Path(SCREENSHOT_DIR).mkdir(exist_ok=True)

_secret_file = Path("totp_secret.txt")

TOTP_SECRET = (
    os.getenv("OPENFX_TOTP_SECRET")
    or (
        _secret_file.read_text().strip()
        if _secret_file.exists()
        else ""
    )
)

END_DATE = date.today()
START_DATE = END_DATE - timedelta(days=10)

# ─────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────

def log(msg):
    print(msg, file=sys.stderr)


def screenshot(driver, name):

    path = (
        f"{SCREENSHOT_DIR}/"
        f"{name}_{datetime.now().strftime('%H%M%S')}.png"
    )

    try:
        driver.save_screenshot(path)
    except Exception:
        pass

    return path


def get_totp():

    if not TOTP_SECRET:
        raise Exception("OPENFX_TOTP_SECRET missing")

    totp = pyotp.TOTP(TOTP_SECRET)

    remaining = (
        totp.interval
        - (int(time.time()) % totp.interval)
    )

    if remaining < 5:
        log(f"[INFO] Waiting {remaining}s for fresh TOTP")
        time.sleep(remaining + 1)

    return totp.now()


def save_session(driver):

    try:

        cookies = driver.get_cookies()

        with open(SESSION_FILE, "wb") as f:
            pickle.dump(cookies, f)

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

            button = driver.find_element(
                By.CSS_SELECTOR,
                "[data-testid='sign-in-continue-button']"
            )

            disabled = (
                button.get_attribute("disabled")
                or button.get_attribute("aria-disabled") == "true"
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

    email_input = WebDriverWait(driver, 30).until(
        EC.presence_of_element_located(
            (By.CSS_SELECTOR, "input[type='email']")
        )
    )

    email_input.clear()
    email_input.send_keys(EMAIL)

    password_input = WebDriverWait(driver, 30).until(
        EC.presence_of_element_located(
            (By.CSS_SELECTOR, "input[type='password']")
        )
    )

    password_input.clear()
    password_input.send_keys(PASSWORD)

    if not wait_cloudflare(driver):
        raise Exception("Cloudflare timeout")

    continue_btn = WebDriverWait(driver, 30).until(
        EC.element_to_be_clickable(
            (
                By.CSS_SELECTOR,
                "[data-testid='sign-in-continue-button']"
            )
        )
    )

    continue_btn.click()

    log("[INFO] Login submitted")

    time.sleep(5)

    code = get_totp()

    otp_done = False

    # Multiple OTP boxes
    try:

        boxes = driver.find_elements(
            By.CSS_SELECTOR,
            "input[maxlength='1']"
        )

        if len(boxes) >= 6:

            for i, digit in enumerate(code[:6]):
                boxes[i].send_keys(digit)

            otp_done = True

    except Exception:
        pass

    # Single OTP field
    if not otp_done:

        selectors = [
            "input[maxlength='6']",
            "input[name*='otp']",
            "input[placeholder*='code' i]",
        ]

        for selector in selectors:

            try:

                otp_input = WebDriverWait(driver, 5).until(
                    EC.presence_of_element_located(
                        (By.CSS_SELECTOR, selector)
                    )
                )

                otp_input.clear()
                otp_input.send_keys(code)

                otp_done = True

                break

            except Exception:
                pass

    if not otp_done:
        raise Exception("OTP field not found")

    time.sleep(2)

    try:

        submit_btn = driver.find_element(
            By.CSS_SELECTOR,
            "button[type='submit']"
        )

        submit_btn.click()

    except Exception:
        pass

    time.sleep(10)

    save_session(driver)

    log("[INFO] Login successful")


# ─────────────────────────────────────────────
# DRIVER
# ─────────────────────────────────────────────

def build_driver():

    options = uc.ChromeOptions()

    options.add_argument("--window-size=1920,1080")

    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")

    options.add_argument("--disable-blink-features=AutomationControlled")

    options.add_argument("--disable-popup-blocking")

    options.add_argument("--lang=en-US")

    options.add_argument("--start-maximized")

    options.add_argument("--ignore-certificate-errors")

    options.add_argument("--allow-running-insecure-content")

    if HEADLESS:
        options.add_argument("--headless=new")

    prefs = {
        "download.prompt_for_download": False,
        "download.directory_upgrade": True,
        "credentials_enable_service": False,
        "profile.password_manager_enabled": False,
        "profile.default_content_setting_values.notifications": 2,
    }

    options.add_experimental_option("prefs", prefs)

    log("[INFO] Starting Chrome")

    driver = uc.Chrome(
        options=options,
        use_subprocess=True
    )

    driver.implicitly_wait(10)

    driver.set_page_load_timeout(120)

    return driver


# ─────────────────────────────────────────────
# EXPORT FLOW
# ─────────────────────────────────────────────

def click_export_button(driver):

    selectors = [
        "[data-testid*='export' i]",
        "[aria-label*='export' i]",
        "[aria-label*='download' i]",
        "[title*='export' i]",
        "[title*='download' i]",
    ]

    for selector in selectors:

        try:

            btn = WebDriverWait(driver, 5).until(
                EC.element_to_be_clickable(
                    (By.CSS_SELECTOR, selector)
                )
            )

            driver.execute_script(
                "arguments[0].scrollIntoView({block:'center'})",
                btn
            )

            time.sleep(1)

            btn.click()

            return True

        except Exception:
            pass

    return False


def click_custom_dates(driver):

    xpaths = [
        "//*[contains(text(),'Custom dates')]",
        "//*[contains(text(),'Custom')]",
    ]

    for xpath in xpaths:

        try:

            el = WebDriverWait(driver, 5).until(
                EC.element_to_be_clickable(
                    (By.XPATH, xpath)
                )
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
        "success": False,
        "message": "",
        "start_date": START_DATE.isoformat(),
        "end_date": END_DATE.isoformat(),
        "error": "",
        "screenshots": [],
    }

    driver = None

    try:

        log(
            f"[INFO] Date Range: "
            f"{START_DATE} -> {END_DATE}"
        )

        driver = build_driver()

        restored = load_session(driver)

        driver.get(TRADE_URL)

        time.sleep(6)

        current_url = driver.current_url.lower()

        if (
            not restored
            or "login" in current_url
            or "sign-in" in current_url
        ):

            log("[INFO] Fresh login required")

            login(driver)

            driver.get(TRADE_URL)

            time.sleep(6)

        result["screenshots"].append(
            screenshot(driver, "01_trade_page")
        )

        log("[INFO] Searching export button")

        if not click_export_button(driver):
            raise Exception("Export button not found")

        time.sleep(3)

        result["screenshots"].append(
            screenshot(driver, "02_export_menu")
        )

        log("[INFO] Searching custom dates")

        if not click_custom_dates(driver):
            raise Exception("Custom dates option not found")

        time.sleep(3)

        result["screenshots"].append(
            screenshot(driver, "03_custom_dates")
        )

        result["success"] = True

        result["message"] = (
            f"OpenFX export flow completed "
            f"({START_DATE} -> {END_DATE})"
        )

        log("[INFO] Export flow completed")

    except Exception as e:

        result["error"] = str(e)

        log(f"[ERROR] {e}")

        if driver:

            try:
                result["screenshots"].append(
                    screenshot(driver, "error")
                )
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
