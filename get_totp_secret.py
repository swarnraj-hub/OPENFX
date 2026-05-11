"""
Logs in (email+password), then tries every possible path to find the
2FA setup/QR page and extract the full TOTP secret.
Also tries to decode QR code from any screenshot taken.
"""
import json, pickle, re, sys, time, urllib.parse
from datetime import datetime
from pathlib import Path

import cv2
import pyotp
import undetected_chromedriver as uc
from selenium.common.exceptions import TimeoutException
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

EMAIL    = "amitkumar@tazapay.com"
PASSWORD = "Sep*19912021"
BASE_URL = "https://app.openfx.com"
LOGIN_URL = f"{BASE_URL}/sign-in"
SCREENSHOT_DIR = "screenshots"
SECRET_FILE = "totp_secret.txt"
Path(SCREENSHOT_DIR).mkdir(exist_ok=True)

def shot(driver, name):
    p = f"{SCREENSHOT_DIR}/{name}_{datetime.now().strftime('%H%M%S')}.png"
    driver.save_screenshot(p)
    return p

def decode_qr(img_path):
    img = cv2.imread(img_path)
    if img is None:
        return None
    detector = cv2.QRCodeDetector()
    # Try multiple preprocessing strategies
    attempts = [img]
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    for scale in [1.0, 1.5, 2.0, 3.0]:
        w, h = int(gray.shape[1]*scale), int(gray.shape[0]*scale)
        resized = cv2.resize(gray, (w, h), interpolation=cv2.INTER_CUBIC)
        _, thresh = cv2.threshold(resized, 0, 255, cv2.THRESH_BINARY+cv2.THRESH_OTSU)
        attempts.extend([resized, thresh])
    for attempt in attempts:
        data, _, _ = detector.detectAndDecode(attempt)
        if data and "otpauth" in data:
            return data
    return None

def extract_secret(uri):
    params = urllib.parse.parse_qs(urllib.parse.urlparse(uri).query)
    return (params.get("secret") or params.get("Secret") or [None])[0]

def dom_secret(driver):
    scripts = [
        # All inputs — find base32 one
        "const ins=Array.from(document.querySelectorAll('input'));for(const i of ins){const v=(i.value||'').replace(/\\s/g,'');if(/^[A-Z2-7]{20,}$/i.test(v))return v.toUpperCase();}return null;",
        # All text nodes — find base32 string
        "const all=Array.from(document.querySelectorAll('*'));for(const e of all){const v=(e.textContent||'').trim().replace(/\\s/g,'');if(/^[A-Z2-7]{20,}$/.test(v))return v;}return null;",
        # data attributes
        "const el=document.querySelector('[data-secret],[data-key],[data-totp]');return el?(el.dataset.secret||el.dataset.key||el.dataset.totp):null;",
    ]
    for s in scripts:
        try:
            v = driver.execute_script(s)
            if v and re.match(r'^[A-Z2-7]{20,}$', v):
                return v
        except:
            pass
    return None

def wait_cf(driver, timeout=120):
    deadline = time.time() + timeout
    warned = False
    while time.time() < deadline:
        try:
            btn = driver.find_element(By.CSS_SELECTOR, "[data-testid='sign-in-continue-button']")
            if not btn.get_attribute("disabled") and btn.get_attribute("aria-disabled") != "true":
                return True
        except:
            pass
        if not warned:
            try:
                driver.find_element(By.XPATH, "//*[contains(text(),'Verify you are human')]")
                print("\n⚠️  Please click the Cloudflare checkbox in the browser!\n", file=sys.stderr)
                warned = True
            except:
                pass
        time.sleep(0.5)
    return False

options = uc.ChromeOptions()
options.add_argument("--window-size=1280,900")
options.add_argument("--no-sandbox")
driver = uc.Chrome(options=options, use_subprocess=True)
driver.implicitly_wait(3)
wait = WebDriverWait(driver, 15)

try:
    # Login
    driver.get(LOGIN_URL)
    time.sleep(2)

    shot(driver, "login_start")
    # Try each selector separately
    email_field = None
    for sel in ["input[type='email']", "input[name='email']", "input[placeholder*='mail' i]", "input"]:
        try:
            email_field = WebDriverWait(driver, 5).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, sel)))
            break
        except:
            continue
    if not email_field:
        print(f"[ERROR] Email field not found. URL: {driver.current_url}", file=sys.stderr)
        shot(driver, "email_not_found")
        sys.exit(1)
    email_field.click(); email_field.clear(); email_field.send_keys(EMAIL)

    pw_field = None
    for sel in ["input[type='password']", "input[name='password']"]:
        try:
            pw_field = WebDriverWait(driver, 5).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, sel)))
            break
        except:
            continue
    if not pw_field:
        print(f"[ERROR] Password field not found. URL: {driver.current_url}", file=sys.stderr)
        sys.exit(1)
    pw_field.click(); pw_field.clear(); pw_field.send_keys(PASSWORD)
    time.sleep(1)

    print("[INFO] Waiting for Cloudflare...", file=sys.stderr)
    if not wait_cf(driver):
        print("[ERROR] Cloudflare not cleared", file=sys.stderr)
        sys.exit(1)

    driver.find_element(By.CSS_SELECTOR, "[data-testid='sign-in-continue-button']").click()
    time.sleep(4)

    current = driver.current_url
    print(f"[INFO] After login: {current}", file=sys.stderr)
    shot(driver, "after_login")

    # Try navigating to possible 2FA setup URLs
    setup_urls = [
        f"{BASE_URL}/secure-account",
        f"{BASE_URL}/setup-2fa",
        f"{BASE_URL}/add-2fa",
        f"{BASE_URL}/account/two-factor-authentication",
        f"{BASE_URL}/account/2fa/setup",
        f"{BASE_URL}/settings/security",
        f"{BASE_URL}/settings/2fa",
        f"{BASE_URL}/security/2fa",
        f"{BASE_URL}/two-factor-authentication",
        f"{BASE_URL}/two-factor-setup",
    ]

    full_secret = None

    # First check current page (might already be QR setup page)
    for url in [current] + setup_urls:
        if url != current:
            driver.get(url)
            time.sleep(2)

        curr = driver.current_url
        src = driver.page_source.lower()

        has_qr = any(k in src for k in ["qr", "otpauth", "authenticator", "scan", "secret"])
        print(f"[SCAN] {curr}  has_qr_keywords={has_qr}", file=sys.stderr)

        if has_qr:
            p = shot(driver, f"qr_candidate_{hash(curr)%9999}")
            print(f"[INFO] Trying QR decode on: {p}", file=sys.stderr)

            # 1) Screenshot decode
            uri = decode_qr(p)
            if uri:
                full_secret = extract_secret(uri)
                print(f"[QR ✅] URI: {uri[:100]}", file=sys.stderr)
                print(f"[QR ✅] Secret: {full_secret}", file=sys.stderr)
                break

            # 2) Try to find QR img element and decode its src
            try:
                imgs = driver.find_elements(By.TAG_NAME, "img")
                for img in imgs:
                    src_attr = img.get_attribute("src") or ""
                    if "qr" in src_attr.lower() or "data:image" in src_attr:
                        print(f"[IMG] Found QR img: {src_attr[:80]}", file=sys.stderr)
                        # If data URI, save it and decode
                        if src_attr.startswith("data:image"):
                            import base64
                            b64 = src_attr.split(",", 1)[1]
                            img_bytes = base64.b64decode(b64)
                            qr_path = f"{SCREENSHOT_DIR}/qr_extracted.png"
                            Path(qr_path).write_bytes(img_bytes)
                            uri = decode_qr(qr_path)
                            if uri:
                                full_secret = extract_secret(uri)
                                print(f"[IMG ✅] Secret: {full_secret}", file=sys.stderr)
                                break
            except Exception as e:
                print(f"[IMG ERR] {e}", file=sys.stderr)

            if full_secret:
                break

            # 3) DOM extraction
            secret = dom_secret(driver)
            if secret:
                full_secret = secret
                print(f"[DOM ✅] Secret: {full_secret}", file=sys.stderr)
                break

    if full_secret:
        Path(SECRET_FILE).write_text(full_secret)
        print(f"\n✅ Full TOTP secret saved to {SECRET_FILE}: {full_secret}", file=sys.stderr)
        code = pyotp.TOTP(full_secret).now()
        print(f"✅ Current OTP: {code}", file=sys.stderr)
        print(json.dumps({"success": True, "secret": full_secret, "current_otp": code}))
    else:
        # Dump ALL text content from the current page as a last resort
        print("\n[DUMP] Page text (looking for secret manually):", file=sys.stderr)
        body = driver.find_element(By.TAG_NAME, "body").text
        print(body[:2000], file=sys.stderr)
        print(json.dumps({"success": False, "error": "Could not extract TOTP secret"}))

finally:
    try:
        driver.quit()
    except:
        pass
