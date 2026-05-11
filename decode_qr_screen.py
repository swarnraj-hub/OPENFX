"""
1. Finds the Chrome window containing the OpenFX QR/2FA page
2. Brings it to the foreground
3. Screenshots it and decodes the QR code
4. Extracts full TOTP secret → saves to totp_secret.txt
"""
import sys, time, urllib.parse
from pathlib import Path
import ctypes
import ctypes.wintypes
import cv2
import numpy as np
from PIL import ImageGrab

SECRET_FILE    = "totp_secret.txt"
SCREENSHOT_DIR = "screenshots"
Path(SCREENSHOT_DIR).mkdir(exist_ok=True)

# ── Windows API helpers ───────────────────────────────────────────────────────
user32 = ctypes.windll.user32

EnumWindows          = user32.EnumWindows
EnumWindowsProc      = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM)
GetWindowTextW       = user32.GetWindowTextW
GetWindowTextLengthW = user32.GetWindowTextLengthW
IsWindowVisible      = user32.IsWindowVisible
SetForegroundWindow  = user32.SetForegroundWindow
ShowWindow           = user32.ShowWindow
SW_RESTORE           = 9


def get_all_windows():
    windows = []
    def callback(hwnd, _):
        if IsWindowVisible(hwnd):
            length = GetWindowTextLengthW(hwnd)
            if length:
                buf = ctypes.create_unicode_buffer(length + 1)
                GetWindowTextW(hwnd, buf, length + 1)
                windows.append((hwnd, buf.value))
        return True
    EnumWindows(EnumWindowsProc(callback), 0)
    return windows


def bring_window_to_front(hwnd):
    ShowWindow(hwnd, SW_RESTORE)
    time.sleep(0.3)
    SetForegroundWindow(hwnd)
    time.sleep(0.8)


# ── QR decoder ────────────────────────────────────────────────────────────────
def decode_qr(img_bgr):
    detector = cv2.QRCodeDetector()
    gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)

    attempts = [img_bgr, gray]
    for scale in [1.0, 1.5, 2.0, 3.0, 4.0]:
        w = int(gray.shape[1] * scale)
        h = int(gray.shape[0] * scale)
        resized  = cv2.resize(gray, (w, h), interpolation=cv2.INTER_CUBIC)
        _, otsu  = cv2.threshold(resized, 0,   255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        _, fixed = cv2.threshold(resized, 128, 255, cv2.THRESH_BINARY)
        attempts += [resized, otsu, fixed]

    for img in attempts:
        data, _, _ = detector.detectAndDecode(img)
        if data and "otpauth" in data:
            return data

    # Also try cropped sub-regions
    h, w = gray.shape[:2]
    crops = [
        img_bgr[0:h//2, 0:w//2],
        img_bgr[0:h//2, w//2:w],
        img_bgr[h//2:h, 0:w//2],
        img_bgr[h//2:h, w//2:w],
        img_bgr[h//4:3*h//4, w//4:3*w//4],
    ]
    for crop in crops:
        data, _, _ = detector.detectAndDecode(crop)
        if data and "otpauth" in data:
            return data

    return None


def extract_secret(uri: str):
    params = urllib.parse.parse_qs(urllib.parse.urlparse(uri).query)
    return (params.get("secret") or params.get("Secret") or [None])[0]


# ── Main ──────────────────────────────────────────────────────────────────────
print("[INFO] Searching for Chrome window with OpenFX...", file=sys.stderr)

windows = get_all_windows()
keywords = ["openfx", "secure your account", "2fa", "authenticator", "totp", "sign in"]

target = None
for hwnd, title in windows:
    tl = title.lower()
    if any(k in tl for k in keywords) and "chrome" in tl:
        target = (hwnd, title)
        print(f"[FOUND] {title!r}", file=sys.stderr)
        break

# Fallback: any Chrome window
if not target:
    for hwnd, title in windows:
        if "chrome" in title.lower() and "google" in title.lower():
            target = (hwnd, title)
            print(f"[FALLBACK Chrome] {title!r}", file=sys.stderr)
            break

if not target:
    print("[ERROR] No Chrome window found. Is Chrome open with the QR page?", file=sys.stderr)
    sys.exit(1)

hwnd, title = target
print(f"[INFO] Bringing to front: {title!r}", file=sys.stderr)
bring_window_to_front(hwnd)
time.sleep(1.5)

# Screenshot
print("[INFO] Taking screenshot...", file=sys.stderr)
pil_img = ImageGrab.grab()
screen  = cv2.cvtColor(np.array(pil_img), cv2.COLOR_RGB2BGR)
cv2.imwrite(f"{SCREENSHOT_DIR}/chrome_screen.png", screen)
print(f"[INFO] Saved → {SCREENSHOT_DIR}/chrome_screen.png", file=sys.stderr)

uri = decode_qr(screen)

if uri:
    secret = extract_secret(uri)
    if secret:
        Path(SECRET_FILE).write_text(secret)
        import pyotp
        remaining = pyotp.TOTP(secret).interval - (int(time.time()) % pyotp.TOTP(secret).interval)
        print(f"\n✅ SECRET FOUND AND SAVED → {SECRET_FILE}")
        print(f"   Full secret : {secret}")
        print(f"   Current OTP : {pyotp.TOTP(secret).now()}  (valid for {remaining}s)")
    else:
        print(f"[WARN] QR decoded but no secret in URI: {uri}", file=sys.stderr)
        sys.exit(1)
else:
    print("\n❌ QR code not detected in screenshot.", file=sys.stderr)
    print("   Make sure the QR code is fully visible (not scrolled off) in Chrome.", file=sys.stderr)
    sys.exit(1)
