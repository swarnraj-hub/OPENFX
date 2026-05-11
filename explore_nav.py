"""Quick script to screenshot the dashboard and dump all nav/link hrefs after session restore."""
import pickle, time, sys
from pathlib import Path
import undetected_chromedriver as uc
from selenium.webdriver.common.by import By

SESSION_FILE = "openfx_session.pkl"
BASE_URL = "https://app.openfx.com"

options = uc.ChromeOptions()
options.add_argument("--window-size=1280,900")
driver = uc.Chrome(options=options, use_subprocess=True)
driver.implicitly_wait(5)

try:
    # restore session
    driver.get(BASE_URL)
    time.sleep(2)
    for c in pickle.loads(Path(SESSION_FILE).read_bytes()):
        try: driver.add_cookie(c)
        except: pass

    # go to dashboard
    driver.get(BASE_URL)
    time.sleep(3)
    driver.save_screenshot("screenshots/dashboard.png")
    print(f"URL: {driver.current_url}", file=sys.stderr)

    # Dump all links
    links = driver.find_elements(By.TAG_NAME, "a")
    print("\n=== All page links ===", file=sys.stderr)
    for l in links:
        href = l.get_attribute("href") or ""
        text = l.text.strip()
        if href and ("openfx" in href or href.startswith("/")):
            print(f"  {text!r:30s}  {href}", file=sys.stderr)

    # Also look for security/settings keywords in page source
    src = driver.page_source.lower()
    for kw in ["security", "2fa", "totp", "authenticator", "two-factor", "settings"]:
        if kw in src:
            print(f"[FOUND keyword] '{kw}' exists in page source", file=sys.stderr)

    # Try clicking Settings if visible
    time.sleep(2)
    driver.save_screenshot("screenshots/dashboard2.png")
finally:
    driver.quit()
