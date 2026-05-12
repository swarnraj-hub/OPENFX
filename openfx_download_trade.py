"""
OpenFX — Robust Trade Export Automation
Windows Self-Hosted Runner
Chrome 147 Stable
"""

import json
import os
import pickle
import sys
import time
import traceback

from pathlib import Path
from datetime import date, datetime, timedelta

import pyotp

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service

from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

from selenium.common.exceptions import (
    TimeoutException,
    NoSuchElementException,
    StaleElementReferenceException,
    WebDriverException,
    ElementClickInterceptedException,
)

from selenium_stealth import stealth


# =========================================================
# CONFIG
# =========================================================

EMAIL = os.getenv("OPENFX_EMAIL", "")
PASSWORD = os.getenv("OPENFX_PASSWORD", "")

HEADLESS = os.getenv(
    "OPENFX_HEADLESS",
    "false"
).lower() == "true"

BASE_URL = "https://app.openfx.com"
TRADE_URL = f"{BASE_URL}/trade"

DOWNLOAD_DIR = str(Path.cwd() / "downloads")

SESSION_FILE = os.getenv(
    "OPENFX_SESSION_FILE",
    "openfx_session.pkl"
)

SCREENSHOT_DIR = os.getenv(
    "OPENFX_SCREENSHOT_DIR",
    "screenshots"
)

Path(SCREENSHOT_DIR).mkdir(exist_ok=True)
Path(DOWNLOAD_DIR).mkdir(exist_ok=True)

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


# =========================================================
# HELPERS
# =========================================================

def log(msg):
    print(msg, flush=True)


def screenshot(driver, name):

    filename = (
        f"{SCREENSHOT_DIR}/"
        f"{name}_{datetime.now().strftime('%H%M%S')}.png"
    )

    try:
        driver.save_screenshot(filename)
    except Exception:
        pass

    return filename


def safe_click(driver, element):

    try:
        element.click()
        return True

    except Exception:
        pass

    try:
        driver.execute_script(
            "arguments[0].click();",
            element
        )
        return True

    except Exception:
        return False


def wait_for_element(
    driver,
    by,
    selector,
    timeout=30,
    clickable=False
):

    wait = WebDriverWait(driver, timeout)

    if clickable:
        return wait.until(
            EC.element_to_be_clickable((by, selector))
        )

    return wait.until(
        EC.presence_of_element_located((by, selector))
    )


def wait_for_all(
    driver,
    by,
    selector,
    timeout=30
):
    return WebDriverWait(driver, timeout).until(
        EC.presence_of_all_elements_located((by, selector))
    )


def get_totp():

    if not TOTP_SECRET:
        raise Exception("OPENFX_TOTP_SECRET missing")

    totp = pyotp.TOTP(TOTP_SECRET)

    remain = (
        totp.interval
        - (int(time.time()) % totp.interval)
    )

    if remain < 5:
        log(f"[INFO] Waiting {remain}s for fresh TOTP")
        time.sleep(remain + 1)

    return totp.now()


# =========================================================
# SESSION
# =========================================================

def save_session(driver):

    try:

        cookies = driver.get_cookies()

        with open(SESSION_FILE, "wb") as f:
            pickle.dump(cookies, f)

        log("[INFO] Session saved")

    except Exception as e:
        log(f"[WARN] Save session failed: {e}")


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

                if "sameSite" in cookie:
                    if cookie["sameSite"] not in [
                        "Strict",
                        "Lax",
                        "None"
                    ]:
                        cookie["sameSite"] = "Lax"

                driver.add_cookie(cookie)

            except Exception:
                pass

        log("[INFO] Session restored")

        return True

    except Exception as e:

        log(f"[WARN] Session restore failed: {e}")

        return False


# =========================================================
# DRIVER
# =========================================================

def build_driver():

    log("[INFO] Starting Chrome")

    options = Options()

    options.add_argument("--start-maximized")

    options.add_argument("--window-size=1920,1080")

    options.add_argument("--disable-blink-features=AutomationControlled")

    options.add_argument("--disable-dev-shm-usage")

    options.add_argument("--no-sandbox")

    options.add_argument("--disable-gpu")

    options.add_argument("--disable-popup-blocking")

    options.add_argument("--disable-notifications")

    options.add_argument("--disable-infobars")

    options.add_argument("--ignore-certificate-errors")

    options.add_argument("--disable-extensions")

    options.add_argument("--disable-background-networking")

    options.add_argument("--disable-sync")

    options.add_argument("--metrics-recording-only")

    options.add_argument("--mute-audio")

    options.add_argument("--lang=en-US")

    options.add_argument("--remote-allow-origins=*")

    # IMPORTANT
    # REMOVE persistent profile
    # Chrome 147 crashes often with reused automation profiles

    # options.add_argument(
    #     f"--user-data-dir={Path.cwd() / 'chrome-profile'}"
    # )

    if HEADLESS:
        options.add_argument("--headless=new")

    prefs = {
        "credentials_enable_service": False,
        "profile.password_manager_enabled": False,
        "download.prompt_for_download": False,
        "profile.default_content_setting_values.notifications": 2,
    }

    options.add_experimental_option(
        "prefs",
        prefs
    )

    options.add_experimental_option(
        "excludeSwitches",
        [
            "enable-automation",
            "enable-logging"
        ]
    )

    options.add_experimental_option(
        "useAutomationExtension",
        False
    )

    driver = webdriver.Chrome(options=options)

    # STEALTH
    stealth(
        driver,
        languages=["en-US", "en"],
        vendor="Google Inc.",
        platform="Win32",
        webgl_vendor="Intel Inc.",
        renderer="Intel Iris OpenGL Engine",
        fix_hairline=True,
    )

    # REMOVE webdriver fingerprint
    driver.execute_script("""
        Object.defineProperty(navigator, 'webdriver', {
            get: () => undefined
        })

        Object.defineProperty(navigator, 'platform', {
            get: () => 'Win32'
        })

        Object.defineProperty(navigator, 'vendor', {
            get: () => 'Google Inc.'
        })
    """)

    driver.set_page_load_timeout(180)

    driver.implicitly_wait(10)

    return driver


# =========================================================
# CLOUDFLARE
# =========================================================

def wait_cloudflare(driver, timeout=180):

    log("[INFO] Waiting Cloudflare verification")

    end = time.time() + timeout

    while time.time() < end:

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
                log("[INFO] Cloudflare passed")
                return True

        except Exception:
            pass

        time.sleep(1)

    return False


# =========================================================
# LOGIN
# =========================================================

def login(driver):

    log("[INFO] Opening login page")

    driver.get(f"{BASE_URL}/sign-in")

    # Allow Cloudflare + React hydration
    time.sleep(15)

    screenshot(driver, "01_login_page_loaded")

    # DEBUG INFO
    try:
        log(f"[DEBUG] Current URL: {driver.current_url}")
        log(f"[DEBUG] Page Title: {driver.title}")

        html_preview = driver.page_source[:5000]

        with open("debug_login_page.html", "w", encoding="utf-8") as f:
            f.write(driver.page_source)

        log("[DEBUG] Saved debug_login_page.html")

    except Exception as e:
        log(f"[WARN] Failed debug dump: {e}")

    # -------------------------------------------------
    # CHECK FOR CLOUDFLARE / ACCESS BLOCK
    # -------------------------------------------------

    lower_source = driver.page_source.lower()

    blocked_keywords = [
        "access denied",
        "attention required",
        "verify you are human",
        "cloudflare",
        "captcha",
        "blocked",
        "checking your browser",
    ]

    for keyword in blocked_keywords:

        if keyword in lower_source:

            screenshot(driver, "cloudflare_blocked")

            raise Exception(
                f"Blocked by Cloudflare / Bot Protection ({keyword})"
            )

    # -------------------------------------------------
    # FIND EMAIL INPUT
    # -------------------------------------------------

    email_selectors = [
        "input[type='email']",
        "input[name='email']",
        "input[placeholder*='email' i]",
        "input[id*='email' i]",
    ]

    email_input = None

    for selector in email_selectors:

        try:

            email_input = WebDriverWait(driver, 15).until(
                EC.visibility_of_element_located(
                    (By.CSS_SELECTOR, selector)
                )
            )

            log(f"[INFO] Email field found: {selector}")

            break

        except Exception:
            pass

    if not email_input:

        screenshot(driver, "email_field_not_found")

        raise Exception(
            "Email input not found after page load"
        )

    # -------------------------------------------------
    # ENTER EMAIL
    # -------------------------------------------------

    try:

        email_input.clear()

        time.sleep(1)

        email_input.send_keys(EMAIL)

        log("[INFO] Email entered")

    except Exception as e:

        screenshot(driver, "email_entry_failed")

        raise Exception(f"Failed entering email: {e}")

    # -------------------------------------------------
    # FIND PASSWORD FIELD
    # -------------------------------------------------

    password_selectors = [
        "input[type='password']",
        "input[name='password']",
        "input[placeholder*='password' i]",
        "input[id*='password' i]",
    ]

    password_input = None

    for selector in password_selectors:

        try:

            password_input = WebDriverWait(driver, 15).until(
                EC.visibility_of_element_located(
                    (By.CSS_SELECTOR, selector)
                )
            )

            log(f"[INFO] Password field found: {selector}")

            break

        except Exception:
            pass

    if not password_input:

        screenshot(driver, "password_field_not_found")

        raise Exception(
            "Password input not found"
        )

    # -------------------------------------------------
    # ENTER PASSWORD
    # -------------------------------------------------

    try:

        password_input.clear()

        time.sleep(1)

        password_input.send_keys(PASSWORD)

        log("[INFO] Password entered")

    except Exception as e:

        screenshot(driver, "password_entry_failed")

        raise Exception(f"Failed entering password: {e}")

    screenshot(driver, "02_credentials_entered")

    # -------------------------------------------------
    # WAIT FOR CLOUDFLARE
    # -------------------------------------------------

    if not wait_cloudflare(driver):

        screenshot(driver, "cloudflare_timeout")

        raise Exception(
            "Cloudflare verification timeout"
        )

    # -------------------------------------------------
    # CONTINUE BUTTON
    # -------------------------------------------------

    continue_selectors = [
        "[data-testid='sign-in-continue-button']",
        "button[type='submit']",
        "button",
    ]

    continue_btn = None

    for selector in continue_selectors:

        try:

            buttons = driver.find_elements(
                By.CSS_SELECTOR,
                selector
            )

            for btn in buttons:

                try:

                    text = btn.text.lower()

                    if (
                        "continue" in text
                        or "sign in" in text
                        or "login" in text
                    ):

                        continue_btn = btn

                        break

                except Exception:
                    pass

            if continue_btn:
                break

        except Exception:
            pass

    if not continue_btn:

        screenshot(driver, "continue_button_missing")

        raise Exception(
            "Continue button not found"
        )

    # -------------------------------------------------
    # CLICK CONTINUE
    # -------------------------------------------------

    try:

        driver.execute_script(
            "arguments[0].scrollIntoView({block:'center'})",
            continue_btn
        )

        time.sleep(1)

        safe_click(driver, continue_btn)

        log("[INFO] Login submitted")

    except Exception as e:

        screenshot(driver, "continue_click_failed")

        raise Exception(
            f"Failed clicking continue: {e}"
        )

    # -------------------------------------------------
    # WAIT AFTER LOGIN
    # -------------------------------------------------

    time.sleep(10)

    screenshot(driver, "03_after_login_submit")

    # -------------------------------------------------
    # GENERATE OTP
    # -------------------------------------------------

    code = get_totp()

    log(f"[INFO] OTP Generated")

    otp_done = False

    # -------------------------------------------------
    # MULTI OTP BOX
    # -------------------------------------------------

    try:

        boxes = driver.find_elements(
            By.CSS_SELECTOR,
            "input[maxlength='1']"
        )

        if len(boxes) >= 6:

            for i, digit in enumerate(code[:6]):

                boxes[i].clear()

                boxes[i].send_keys(digit)

                time.sleep(0.2)

            otp_done = True

            log("[INFO] OTP entered using multi-box")

    except Exception:
        pass

    # -------------------------------------------------
    # SINGLE OTP INPUT
    # -------------------------------------------------

    if not otp_done:

        otp_selectors = [
            "input[maxlength='6']",
            "input[name*='otp']",
            "input[name*='code']",
            "input[placeholder*='code' i]",
            "input[placeholder*='otp' i]",
            "input[autocomplete='one-time-code']",
        ]

        for selector in otp_selectors:

            try:

                otp_input = WebDriverWait(driver, 5).until(
                    EC.visibility_of_element_located(
                        (By.CSS_SELECTOR, selector)
                    )
                )

                otp_input.clear()

                otp_input.send_keys(code)

                otp_done = True

                log(
                    f"[INFO] OTP entered using selector: {selector}"
                )

                break

            except Exception:
                pass

    if not otp_done:

        screenshot(driver, "otp_not_found")

        raise Exception(
            "OTP input field not found"
        )

    screenshot(driver, "04_otp_entered")

    # -------------------------------------------------
    # SUBMIT OTP
    # -------------------------------------------------

    try:

        submit_buttons = driver.find_elements(
            By.CSS_SELECTOR,
            "button"
        )

        for btn in submit_buttons:

            try:

                text = btn.text.lower()

                if (
                    "verify" in text
                    or "submit" in text
                    or "continue" in text
                ):

                    safe_click(driver, btn)

                    log("[INFO] OTP submitted")

                    break

            except Exception:
                pass

    except Exception:
        pass

    # -------------------------------------------------
    # WAIT FOR LOGIN COMPLETE
    # -------------------------------------------------

    time.sleep(15)

    screenshot(driver, "05_after_otp_submit")

    current_url = driver.current_url.lower()

    if (
        "login" in current_url
        or "sign-in" in current_url
    ):

        screenshot(driver, "login_failed")

        raise Exception(
            "Login failed after OTP submit"
        )

    # -------------------------------------------------
    # SAVE SESSION
    # -------------------------------------------------

    save_session(driver)

    log("[INFO] Login successful")


# =========================================================
# EXPORT
# =========================================================

def click_export(driver):

    selectors = [
        "[data-testid*='export' i]",
        "[aria-label*='export' i]",
        "[title*='export' i]",
        "button"
    ]

    for selector in selectors:

        try:

            buttons = driver.find_elements(
                By.CSS_SELECTOR,
                selector
            )

            for btn in buttons:

                text = btn.text.lower()

                if (
                    "export" in text
                    or "download" in text
                ):

                    driver.execute_script(
                        "arguments[0].scrollIntoView({block:'center'})",
                        btn
                    )

                    time.sleep(1)

                    safe_click(driver, btn)

                    log("[INFO] Export clicked")

                    return True

        except Exception:
            pass

    return False


# =========================================================
# MAIN
# =========================================================

def main():

    result = {
        "success": False,
        "message": "",
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

        time.sleep(8)

        current = driver.current_url.lower()

        if (
            not restored
            or "sign-in" in current
            or "login" in current
        ):

            log("[INFO] Fresh login required")

            login(driver)

            driver.get(TRADE_URL)

            time.sleep(8)

        result["screenshots"].append(
            screenshot(driver, "trade_page")
        )

        if not click_export(driver):
            raise Exception("Export button not found")

        time.sleep(5)

        result["screenshots"].append(
            screenshot(driver, "after_export")
        )

        result["success"] = True

        result["message"] = "Export completed"

        log("[INFO] SUCCESS")

    except Exception as e:

        result["error"] = str(e)

        log(f"[ERROR] {e}")

        log(traceback.format_exc())

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
