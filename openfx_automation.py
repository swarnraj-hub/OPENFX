"""
OpenFX Automation Script
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Uses undetected-chromedriver to bypass Cloudflare Turnstile.
First run  →  browser opens visibly, session saved to openfx_session.json
Subsequent →  session auto-loaded, runs headless (no CAPTCHA)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Usage:
  python openfx_automation.py              # normal / n8n run
  python openfx_automation.py --setup      # force fresh session setup

n8n env overrides:
  OPENFX_EMAIL, OPENFX_PASSWORD, OPENFX_TOTP_SECRET
  OPENFX_HEADLESS (true/false, default: false)
  OPENFX_SESSION_FILE (default: openfx_session.json)
  OPENFX_SCREENSHOT_DIR (default: screenshots)
"""

import json
import os
import pickle
import sys
import time
from datetime import datetime
from pathlib import Path

import pyotp
import undetected_chromedriver as uc
from selenium.common.exceptions import TimeoutException
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

# ── Config ────────────────────────────────────────────────────────────────────
EMAIL        = os.getenv("OPENFX_EMAIL",        "amitkumar@tazapay.com")
PASSWORD     = os.getenv("OPENFX_PASSWORD",     "Sep*19912021")
TOTP_SECRET  = os.getenv("OPENFX_TOTP_SECRET",  "IVFGC63TJNYDA6L3EVSDK23OENCD4V2INZXXQYK2G4SE4PDYKQRVCW3WKUZEWLBXKZPG64R6GMXFAJLSPNBSMJJYJVPHQOKKEFTHC2A")
HEADLESS     = os.getenv("OPENFX_HEADLESS",     "false").lower() == "true"
SESSION_FILE = os.getenv("OPENFX_SESSION_FILE", "openfx_session.pkl")
SCREENSHOT_DIR = os.getenv("OPENFX_SCREENSHOT_DIR", "screenshots")
LOGIN_URL    = "https://app.openfx.com/sign-in"
SETUP_MODE   = "--setup" in sys.argv


# ── Helpers ───────────────────────────────────────────────────────────────────

def get_totp_code(secret: str) -> str:
    return pyotp.TOTP(secret).now()


def get_totp_remaining(secret: str) -> int:
    t = pyotp.TOTP(secret)
    return t.interval - (int(time.time()) % t.interval)


def take_screenshot(driver, name: str) -> str:
    os.makedirs(SCREENSHOT_DIR, exist_ok=True)
    path = os.path.join(SCREENSHOT_DIR, f"{name}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png")
    driver.save_screenshot(path)
    return path


def save_cookies(driver):
    cookies = driver.get_cookies()
    Path(SESSION_FILE).write_bytes(pickle.dumps(cookies))
    print(f"[INFO] Session saved → {SESSION_FILE}", file=sys.stderr)


def load_cookies(driver):
    cookies = pickle.loads(Path(SESSION_FILE).read_bytes())
    driver.get("https://app.openfx.com")
    time.sleep(2)
    for cookie in cookies:
        try:
            driver.add_cookie(cookie)
        except Exception:
            pass
    print(f"[INFO] Session loaded ← {SESSION_FILE}", file=sys.stderr)


def wait_for_signin_button(driver, timeout: int = 120) -> bool:
    """Wait until Sign In button is enabled (Cloudflare cleared)."""
    deadline = time.time() + timeout
    warned = False
    while time.time() < deadline:
        try:
            btn = driver.find_element(By.CSS_SELECTOR, "[data-testid='sign-in-continue-button']")
            disabled = btn.get_attribute("disabled")
            aria     = btn.get_attribute("aria-disabled")
            if not disabled and aria != "true":
                return True
        except Exception:
            pass

        if not warned:
            try:
                driver.find_element(By.XPATH, "//*[contains(text(),'Verify you are human')]")
                print(
                    "\n⚠️  CLOUDFLARE CAPTCHA — please click the checkbox in the browser window!\n",
                    file=sys.stderr,
                )
                warned = True
            except Exception:
                pass

        time.sleep(0.5)
    return False


# ── Login flow ────────────────────────────────────────────────────────────────

def login_openfx(driver) -> dict:
    result = {"success": False, "step": "", "error": "", "screenshots": []}
    wait  = WebDriverWait(driver, 15)

    try:
        # Step 1 — Navigate
        result["step"] = "navigate"
        driver.get(LOGIN_URL)
        time.sleep(2)
        shot = take_screenshot(driver, "01_login_page")
        result["screenshots"].append(shot)

        # Step 2 — Fill email
        result["step"] = "enter_email"
        email_field = wait.until(EC.presence_of_element_located((
            By.CSS_SELECTOR,
            "input[type='email'], input[name='email'], input[placeholder*='mail' i]",
        )))
        email_field.click()
        email_field.clear()
        email_field.send_keys(EMAIL)

        # Step 3 — Fill password
        result["step"] = "enter_password"
        pw_field = wait.until(EC.presence_of_element_located((
            By.CSS_SELECTOR, "input[type='password']",
        )))
        pw_field.click()
        pw_field.clear()
        pw_field.send_keys(PASSWORD)
        time.sleep(1)
        shot = take_screenshot(driver, "02_credentials_filled")
        result["screenshots"].append(shot)

        # Step 4 — Cloudflare wait + Sign In
        result["step"] = "cloudflare_wait"
        print("[INFO] Waiting for Cloudflare to clear (up to 120s)...", file=sys.stderr)
        cleared = wait_for_signin_button(driver, timeout=120)
        if not cleared:
            shot = take_screenshot(driver, "cf_timeout")
            result["screenshots"].append(shot)
            result["error"] = "Cloudflare not resolved in 120s"
            return result

        result["step"] = "click_signin"
        btn = driver.find_element(By.CSS_SELECTOR, "[data-testid='sign-in-continue-button']")
        btn.click()
        time.sleep(4)
        shot = take_screenshot(driver, "03_after_signin_click")
        result["screenshots"].append(shot)

        # Step 5 — TOTP 2FA
        result["step"] = "check_2fa"
        totp_selectors = [
            "input[placeholder*='code' i]",
            "input[placeholder*='otp' i]",
            "input[placeholder*='authenticator' i]",
            "input[placeholder*='2fa' i]",
            "input[name*='otp']",
            "input[name*='code']",
            "input[name*='totp']",
            "input[maxlength='6']",
        ]

        totp_field = None
        for sel in totp_selectors:
            try:
                totp_field = WebDriverWait(driver, 4).until(
                    EC.visibility_of_element_located((By.CSS_SELECTOR, sel))
                )
                break
            except TimeoutException:
                continue

        if totp_field:
            result["step"] = "enter_totp"
            remaining = get_totp_remaining(TOTP_SECRET)
            if remaining < 5:
                print(f"[INFO] TOTP expiring in {remaining}s — waiting...", file=sys.stderr)
                time.sleep(remaining + 1)

            code = get_totp_code(TOTP_SECRET)
            print(f"[INFO] Entering TOTP: {code}", file=sys.stderr)
            totp_field.click()
            totp_field.send_keys(code)
            time.sleep(1)
            shot = take_screenshot(driver, "04_totp_entered")
            result["screenshots"].append(shot)

            try:
                verify_btn = WebDriverWait(driver, 6).until(EC.element_to_be_clickable((
                    By.CSS_SELECTOR,
                    "button[type='submit'], button",
                )))
                verify_btn.click()
            except TimeoutException:
                totp_field.send_keys(Keys.RETURN)

            time.sleep(5)
            shot = take_screenshot(driver, "05_after_totp")
            result["screenshots"].append(shot)

        # Step 6 — Confirm + save session
        result["step"] = "verify_login"
        current_url = driver.current_url
        result["url_after_login"] = current_url

        auth_pages = ("sign-in", "login", "verify-2fa", "2fa", "otp")
        if not any(p in current_url.lower() for p in auth_pages):
            save_cookies(driver)
            result["success"] = True
            result["message"] = f"Login successful — landed on: {current_url}"
        else:
            result["error"] = f"Still on auth page. URL: {current_url}"

        shot = take_screenshot(driver, "06_final_state")
        result["screenshots"].append(shot)

    except Exception as e:
        result["error"] = str(e)
        try:
            shot = take_screenshot(driver, "error_state")
            result["screenshots"].append(shot)
        except Exception:
            pass

    return result


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    session_exists = Path(SESSION_FILE).exists() and not SETUP_MODE
    totp_code = get_totp_code(TOTP_SECRET)

    print(f"[INFO] Email        : {EMAIL}", file=sys.stderr)
    print(f"[INFO] TOTP Code    : {totp_code}  (expires in {get_totp_remaining(TOTP_SECRET)}s)", file=sys.stderr)
    print(f"[INFO] Session      : {'FOUND — loading saved cookies' if session_exists else 'NOT FOUND — first run, browser will open'}", file=sys.stderr)
    print(f"[INFO] Headless     : {HEADLESS}", file=sys.stderr)

    options = uc.ChromeOptions()
    options.add_argument("--window-size=1280,800")
    options.add_argument("--no-sandbox")
    if HEADLESS:
        options.add_argument("--headless=new")

    driver = uc.Chrome(options=options, use_subprocess=True)
    driver.implicitly_wait(5)

    try:
        if session_exists:
            load_cookies(driver)

        result = login_openfx(driver)
    finally:
        driver.quit()

    print(json.dumps(result, indent=2))
    sys.exit(0 if result["success"] else 1)


if __name__ == "__main__":
    main()
