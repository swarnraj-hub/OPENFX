"""
Step 1  - logs into OpenFX (email + password, Cloudflare handled by UC)
Step 2  - navigates to 2FA settings page and extracts the FULL TOTP secret
          (reads QR code from page + also reads raw input field value via JS)
Step 3  - generates a fresh OTP and fills the verify-2fa boxes
Step 4  - saves the full secret to totp_secret.txt for future runs
"""

import json
import os
import pickle
import re
import sys
import time
import urllib.parse
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np
import pyotp
import undetected_chromedriver as uc
from selenium.common.exceptions import TimeoutException, NoSuchElementException
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

# ── Config ────────────────────────────────────────────────────────────────────
EMAIL        = os.getenv("OPENFX_EMAIL",    "amitkumar@tazapay.com")
PASSWORD     = os.getenv("OPENFX_PASSWORD", "Sep*19912021")
SESSION_FILE = os.getenv("OPENFX_SESSION_FILE", "openfx_session.pkl")
SECRET_FILE  = "totp_secret.txt"
SCREENSHOT_DIR = "screenshots"
BASE_URL     = "https://app.openfx.com"
LOGIN_URL    = f"{BASE_URL}/sign-in"

# Try saved full secret first, fall back to partial
if Path(SECRET_FILE).exists():
    SAVED_SECRET = Path(SECRET_FILE).read_text().strip()
else:
    SAVED_SECRET = "IVFGC63TJNYDA6L3EVSDK23OENCD4V2INZXXQYK2G4SE4PDYKQRVCW3WKUZEWLBXKZPG64R6GMXFAJLSPNBSMJJYJVPHQOKKEFTHC2A"


# ── Helpers ───────────────────────────────────────────────────────────────────

def get_totp(secret):
    return pyotp.TOTP(secret).now()

def totp_remaining(secret):
    t = pyotp.TOTP(secret)
    return t.interval - (int(time.time()) % t.interval)

def shot(driver, name):
    os.makedirs(SCREENSHOT_DIR, exist_ok=True)
    p = f"{SCREENSHOT_DIR}/{name}_{datetime.now().strftime('%H%M%S')}.png"
    driver.save_screenshot(p)
    return p

def decode_qr_from_screenshot(driver) -> str | None:
    """Screenshot the current page and decode any QR code using OpenCV."""
    img_path = shot(driver, "qr_page")
    img = cv2.imread(img_path)

    detector = cv2.QRCodeDetector()

    # Try on full image first
    data, _, _ = detector.detectAndDecode(img)
    if data and "otpauth" in data:
        print(f"[QR] Decoded (full image): {data[:80]}", file=sys.stderr)
        return data

    # Also try enhanced versions (greyscale, higher contrast)
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    for scale in [1.0, 1.5, 2.0]:
        w = int(gray.shape[1] * scale)
        h = int(gray.shape[0] * scale)
        resized = cv2.resize(gray, (w, h), interpolation=cv2.INTER_CUBIC)
        _, thresh = cv2.threshold(resized, 128, 255, cv2.THRESH_BINARY)
        data, _, _ = detector.detectAndDecode(thresh)
        if data and "otpauth" in data:
            print(f"[QR] Decoded (scale={scale}): {data[:80]}", file=sys.stderr)
            return data

    print("[WARN] No QR code decoded from screenshot", file=sys.stderr)
    return None

def extract_secret_from_otpauth(uri: str) -> str | None:
    """Parse secret from otpauth://totp/...?secret=XXX&..."""
    parsed = urllib.parse.urlparse(uri)
    params = urllib.parse.parse_qs(parsed.query)
    return params.get("secret", [None])[0]

def extract_secret_from_dom(driver) -> str | None:
    """Try to read the raw secret value from any input/pre/code element on the page."""
    scripts = [
        # Most likely: hidden input or readonly input with the secret
        "return document.querySelector('input[readonly]')?.value",
        "return document.querySelector('input[type=text]')?.value",
        "return document.querySelector('[data-testid*=\"secret\"]')?.textContent",
        "return document.querySelector('[data-testid*=\"key\"]')?.textContent",
        "return document.querySelector('[class*=\"secret\"]')?.textContent",
        "return document.querySelector('[class*=\"key\"]')?.textContent",
        "return document.querySelector('code')?.textContent",
        "return document.querySelector('pre')?.textContent",
        # Try all inputs and find the one that looks like a base32 secret
        """
        const inputs = Array.from(document.querySelectorAll('input'));
        for (const inp of inputs) {
          const v = inp.value || '';
          if (/^[A-Z2-7]{16,}$/i.test(v.replace(/\\s/g,''))) return v.replace(/\\s/g,'');
        }
        return null;
        """,
        # Try all spans/divs with text that looks like a base32 secret
        """
        const all = Array.from(document.querySelectorAll('span,div,p,td,code,pre'));
        for (const el of all) {
          const v = (el.textContent || '').trim().replace(/\\s/g,'');
          if (/^[A-Z2-7]{20,}$/i.test(v)) return v;
        }
        return null;
        """
    ]
    for script in scripts:
        try:
            val = driver.execute_script(script)
            if val and len(val) >= 16 and re.match(r'^[A-Z2-7]+$', val.upper()):
                return val.upper().strip()
        except Exception:
            pass
    return None


def find_2fa_setup_url(driver) -> str | None:
    """Navigate around settings to find the 2FA setup / add-key page."""
    candidates = [
        f"{BASE_URL}/settings/security",
        f"{BASE_URL}/settings/two-factor-authentication",
        f"{BASE_URL}/settings/2fa",
        f"{BASE_URL}/account/security",
        f"{BASE_URL}/account/2fa",
        f"{BASE_URL}/profile/security",
        f"{BASE_URL}/security",
        f"{BASE_URL}/settings",
    ]
    for url in candidates:
        try:
            driver.get(url)
            time.sleep(2)
            page = driver.page_source.lower()
            if any(k in page for k in ("2fa", "two-factor", "authenticator", "qr", "totp")):
                print(f"[INFO] Found 2FA page at: {url}", file=sys.stderr)
                return url
        except Exception:
            pass
    return None


# ── Cloudflare wait ───────────────────────────────────────────────────────────

def wait_signin_enabled(driver, timeout=120):
    deadline = time.time() + timeout
    warned = False
    while time.time() < deadline:
        try:
            btn = driver.find_element(By.CSS_SELECTOR, "[data-testid='sign-in-continue-button']")
            if not btn.get_attribute("disabled") and btn.get_attribute("aria-disabled") != "true":
                return True
        except Exception:
            pass
        if not warned:
            try:
                driver.find_element(By.XPATH, "//*[contains(text(),'Verify you are human')]")
                print("\n⚠️  CLOUDFLARE: Please click the checkbox in the browser window!\n", file=sys.stderr)
                warned = True
            except Exception:
                pass
        time.sleep(0.5)
    return False


# ── Login ─────────────────────────────────────────────────────────────────────

def do_login(driver):
    wait = WebDriverWait(driver, 15)
    driver.get(LOGIN_URL)
    time.sleep(2)

    # Email
    f = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR,
        "input[type='email'],input[name='email'],input[placeholder*='mail' i]")))
    f.click(); f.clear(); f.send_keys(EMAIL)

    # Password
    p = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "input[type='password']")))
    p.click(); p.clear(); p.send_keys(PASSWORD)
    time.sleep(1)
    shot(driver, "login_filled")

    print("[INFO] Waiting for Cloudflare...", file=sys.stderr)
    if not wait_signin_enabled(driver):
        print("[ERROR] Cloudflare not cleared", file=sys.stderr)
        return False

    driver.find_element(By.CSS_SELECTOR, "[data-testid='sign-in-continue-button']").click()
    time.sleep(4)
    shot(driver, "after_signin")
    return True


# ── Cookies ───────────────────────────────────────────────────────────────────

def save_session(driver):
    Path(SESSION_FILE).write_bytes(pickle.dumps(driver.get_cookies()))
    print(f"[INFO] Session saved → {SESSION_FILE}", file=sys.stderr)

def load_session(driver):
    driver.get(BASE_URL)
    time.sleep(2)
    for c in pickle.loads(Path(SESSION_FILE).read_bytes()):
        try:
            driver.add_cookie(c)
        except Exception:
            pass
    print(f"[INFO] Session loaded ← {SESSION_FILE}", file=sys.stderr)


# ── Fill OTP boxes ────────────────────────────────────────────────────────────

def fill_otp_boxes(driver, code: str) -> bool:
    """Fill 6 individual digit boxes or a single OTP input."""
    wait = WebDriverWait(driver, 10)
    shot(driver, "before_otp_fill")

    # Option A: individual digit inputs (each maxlength=1)
    try:
        boxes = driver.find_elements(By.CSS_SELECTOR, "input[maxlength='1']")
        if len(boxes) >= 6:
            print(f"[INFO] Filling {len(boxes)} individual digit boxes with: {code}", file=sys.stderr)
            for i, digit in enumerate(code[:len(boxes)]):
                boxes[i].click()
                boxes[i].clear()
                boxes[i].send_keys(digit)
                time.sleep(0.15)
            shot(driver, "otp_digits_filled")
            return True
    except Exception as e:
        print(f"[DEBUG] Individual boxes: {e}", file=sys.stderr)

    # Option B: single input accepting all 6 digits
    selectors = [
        "input[maxlength='6']",
        "input[placeholder*='code' i]",
        "input[placeholder*='otp' i]",
        "input[name*='otp']",
        "input[name*='code']",
        "input[name*='totp']",
        "input[type='text']",
        "input[type='number']",
    ]
    for sel in selectors:
        try:
            f = WebDriverWait(driver, 3).until(EC.visibility_of_element_located((By.CSS_SELECTOR, sel)))
            f.click(); f.clear(); f.send_keys(code)
            print(f"[INFO] OTP filled in single input ({sel}): {code}", file=sys.stderr)
            shot(driver, "otp_single_filled")
            return True
        except TimeoutException:
            continue

    print("[WARN] No OTP input found", file=sys.stderr)
    return False


def submit_otp(driver):
    try:
        btn = WebDriverWait(driver, 5).until(EC.element_to_be_clickable((
            By.CSS_SELECTOR,
            "button[type='submit'],button:contains('Verify'),button:contains('Confirm')"
        )))
        btn.click()
    except Exception:
        try:
            driver.find_element(By.CSS_SELECTOR, "input").send_keys(Keys.RETURN)
        except Exception:
            pass
    time.sleep(4)
    shot(driver, "after_otp_submit")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    result = {"success": False, "totp_secret": None, "otp_used": None, "url": None, "error": ""}

    options = uc.ChromeOptions()
    options.add_argument("--window-size=1280,800")
    options.add_argument("--no-sandbox")

    driver = uc.Chrome(options=options, use_subprocess=True)
    driver.implicitly_wait(5)

    try:
        # ── Phase 1: Login ────────────────────────────────────────────────────
        session_exists = Path(SESSION_FILE).exists()
        if session_exists:
            print("[INFO] Restoring saved session...", file=sys.stderr)
            load_session(driver)
        else:
            print("[INFO] No saved session — performing fresh login...", file=sys.stderr)
            if not do_login(driver):
                result["error"] = "Login failed"
                print(json.dumps(result)); return
            save_session(driver)

        # ── Phase 2: Find 2FA setup page and extract full TOTP secret ─────────
        print("[INFO] Looking for 2FA setup page to extract full secret...", file=sys.stderr)
        full_secret = None

        setup_url = find_2fa_setup_url(driver)
        if setup_url:
            shot(driver, "2fa_setup_page")

            # Try QR code decode first
            uri = decode_qr_from_screenshot(driver)
            if uri:
                full_secret = extract_secret_from_otpauth(uri)
                if full_secret:
                    print(f"[INFO] ✅ Secret from QR code: {full_secret}", file=sys.stderr)

            # Fallback: extract from DOM
            if not full_secret:
                full_secret = extract_secret_from_dom(driver)
                if full_secret:
                    print(f"[INFO] ✅ Secret from DOM: {full_secret}", file=sys.stderr)

            if full_secret:
                Path(SECRET_FILE).write_text(full_secret)
                print(f"[INFO] Full secret saved → {SECRET_FILE}", file=sys.stderr)
        else:
            print("[WARN] 2FA setup page not found — using known partial secret", file=sys.stderr)

        totp_secret = full_secret or SAVED_SECRET
        result["totp_secret"] = totp_secret[:8] + "..." + totp_secret[-4:]  # masked for JSON output

        # ── Phase 3: Navigate to verify-2fa and fill OTP ──────────────────────
        driver.get(f"{BASE_URL}/verify-2fa")
        time.sleep(3)
        shot(driver, "verify_2fa_page")

        # Wait for fresh code if about to expire
        rem = totp_remaining(totp_secret)
        if rem < 5:
            print(f"[INFO] TOTP expiring in {rem}s — waiting for next window...", file=sys.stderr)
            time.sleep(rem + 1)

        code = get_totp(totp_secret)
        print(f"[INFO] Using TOTP: {code}  (expires in {totp_remaining(totp_secret)}s)", file=sys.stderr)
        result["otp_used"] = code

        if not fill_otp_boxes(driver, code):
            result["error"] = "Could not find OTP input fields"
            print(json.dumps(result)); return

        time.sleep(1)
        submit_otp(driver)

        # ── Phase 4: Verify success ───────────────────────────────────────────
        current = driver.current_url
        result["url"] = current
        auth_pages = ("sign-in", "login", "verify-2fa", "2fa")
        if not any(p in current.lower() for p in auth_pages):
            save_session(driver)
            result["success"] = True
            result["message"] = f"Login complete — {current}"
            print(f"[SUCCESS] Logged in: {current}", file=sys.stderr)
        else:
            page_text = driver.find_element(By.TAG_NAME, "body").text[:300]
            result["error"] = f"Still on auth page: {current}\nPage: {page_text}"

        shot(driver, "final_state")

    except Exception as e:
        result["error"] = str(e)
        try:
            shot(driver, "exception")
        except Exception:
            pass
    finally:
        try:
            driver.quit()
        except Exception:
            pass

    print(json.dumps(result, indent=2))
    sys.exit(0 if result["success"] else 1)


if __name__ == "__main__":
    main()
