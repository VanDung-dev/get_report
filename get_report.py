import os
import re
import time
import json
import datetime
import pytz
from dateutil import parser
from dotenv import load_dotenv, find_dotenv

import gspread
from google.auth import default
from google.oauth2.service_account import Credentials

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
import undetected_chromedriver as uc

import platform
import subprocess
import re


# Tự động tìm và nạp file .env ở thư mục hiện tại hoặc thư mục cha
load_dotenv(find_dotenv())

# =========================
# PATCH UNDETECTED CHROMEDRIVER CLEANUP
# =========================
# Sửa lỗi OSError: [WinError 6] The handle is invalid khi Python giải phóng bộ nhớ (garbage collection)
def _patch_uc_del():
    def _safe_del(self):
        try:
            self.quit()
        except Exception:
            pass
    uc.Chrome.__del__ = _safe_del

_patch_uc_del()

# =========================
# CONFIG
# =========================
email = os.environ.get("TEAMS_EMAIL")
password = os.environ.get("TEAMS_PASSWORD")
chat = "GetReport"
local_tz = pytz.timezone("Asia/Ho_Chi_Minh")
SPREADSHEET_ID = "1_m7s-1-I-SOFfzlWe7CBf5fstFir7qXYAKW4j-8hKYM"
SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

MESSAGE_PATTERN = re.compile(r".*\+\s*(\d+)/.*", re.IGNORECASE | re.DOTALL)


# =========================
# GOOGLE SHEETS AUTHENTICATION
# =========================
def get_gspread_client(credentials_path="gcp-credentials.json"):
    """Khởi tạo và xác thực kết nối với Google Sheets API linh hoạt từ nhiều nguồn."""
    # 1. Ưu tiên đọc từ các biến môi trường dạng JSON trong file .env
    env_json = (
        os.getenv("GCP_SA_KEY")
        or os.getenv("GCP_CREDENTIALS_JSON")
        or os.getenv("GOOGLE_CREDENTIALS")
    )
    if env_json and env_json.strip().startswith("{"):
        try:
            info = json.loads(env_json)
            creds = Credentials.from_service_account_info(info, scopes=SCOPES)
            return gspread.authorize(creds)
        except Exception as e:
            print(f"⚠️ Lỗi đọc credentials từ biến môi trường: {e}")

    # 2. Đọc từ file JSON trên ổ đĩa (thử nhiều đường dẫn bao gồm file gcp-credentials.json ở thư mục get_message)
    possible_paths = [
        credentials_path,
        credentials_path + ".json",
        "gcp-credentials.json",
        "gcp-credentials.json.json",
        "credentials.json",
        os.path.join("..", "get_message", "gcp-credentials.json"),
        os.path.join("..", "get_message", "gcp-credentials.json.json"),
    ]

    for path in possible_paths:
        if os.path.exists(path):
            try:
                creds = Credentials.from_service_account_file(path, scopes=SCOPES)
                return gspread.authorize(creds)
            except Exception as e:
                print(f"⚠️ Lỗi đọc file credentials '{path}': {e}")

    # 3. Thử dùng default credentials
    try:
        creds, _ = default()
        return gspread.authorize(creds)
    except Exception as e:
        raise FileNotFoundError(
            "Không thể khởi tạo kết nối Google Sheets API. "
            "Vui lòng cấu hình biến GCP_SA_KEY / GCP_CREDENTIALS_JSON trong file .env hoặc thêm file 'gcp-credentials.json' vào thư mục."
        ) from e


def display_screenshot(driver: webdriver.Chrome, file_name: str = "screenshot.png"):
    """Chụp màn hình và hiển thị"""
    try:
        driver.save_screenshot(file_name)
        print(f"📸 Đã lưu ảnh màn hình '{file_name}'")
    except Exception as e:
        print(f"❌ Không thể lưu ảnh màn hình '{file_name}': {e}")
    time.sleep(3)


def send_message(driver, message):
    try:
        message_box = WebDriverWait(driver, 15).until(
            EC.presence_of_element_located((By.XPATH, '//div[@contenteditable="true"]'))
        )

        for line in message.split("\n"):
            message_box.send_keys(line)
            message_box.send_keys(Keys.SHIFT, Keys.ENTER)

        display_screenshot(driver, "after_typing_message.png")
        time.sleep(3)
        message_box.send_keys(Keys.ENTER)
        time.sleep(3)
        display_screenshot(driver, "after_sending_message.png")

    except Exception as e:
        print(f"❌ Lỗi khi gửi tin nhắn: {e}")


def open_chat(driver, chat_name):
    try:
        # Đã dùng normalize-space để khớp 100% tên, chống gửi nhầm nhóm
        chat_element = WebDriverWait(driver, 15).until(
            EC.element_to_be_clickable(
                (By.XPATH, f"//span[normalize-space(text())='{chat_name}']")
            )
        )
        chat_element.click()
        WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.XPATH, '//div[@contenteditable="true"]'))
        )
        display_screenshot(driver, "after_opening_chat.png")
    except Exception as e:
        display_screenshot(driver, "open_chat_error.png")
        print(f"❌ Lỗi khi mở chat '{chat_name}': {e}")


def combine_messages(messages_dict):
    combined = {}
    for sheet_name, msg_list in messages_dict.items():
        if msg_list:
            combined[sheet_name] = "\n\n".join(msg_list)
    return combined


def preprocess_message(content):
    content = re.sub(r"-\s+-", "-", content)
    content = re.sub(r"\s*[+]+\s*(\d+/)\s*", r"\n+ \1 ", content)

    lines = content.splitlines()
    processed_lines = []

    for line in lines:
        line = line.strip()
        if not line:
            continue

        if re.match(r"^\+\s*(6|7|8|9|10)\s*/", line):
            continue

        if re.match(r"^(\+|\d+\.|=>|-)", line):
            line = "\u200b" + line
        processed_lines.append(line)

    changed = True
    while changed:
        changed = False
        n = len(processed_lines)
        for L in range(n // 2, 0, -1):
            for i in range(n - 2 * L + 1):
                if processed_lines[i : i + L] == processed_lines[i + L : i + 2 * L]:
                    processed_lines = (
                        processed_lines[: i + L] + processed_lines[i + 2 * L :]
                    )
                    changed = True
                    break
            if changed:
                break

    content = "\n".join(processed_lines)
    return content.strip()


def is_valid_message(content):
    return bool(MESSAGE_PATTERN.match(content))


def get_filtered_messages(spreadsheet, sheet_names, current_hour):
    tz = pytz.timezone("Asia/Ho_Chi_Minh")
    now = datetime.datetime.now(tz).replace(tzinfo=None)

    messages = {sheet_name: [] for sheet_name in sheet_names}
    EXCLUDED_SHEETS = [
        "Report",
        "GetReport",
        "iX000s iSSale TTS Base.XoắnNỆN50k*CấuTrúcVolunt",
        "iX000s iSSale gbBOSS AH*AU*cOL*YeuCauTop-iUp*KTra",
    ]

    for sheet_name in sheet_names:
        if sheet_name in EXCLUDED_SHEETS:
            continue
        try:
            sheet = spreadsheet.worksheet(sheet_name)
            data = sheet.get_all_records()

            for row in data:
                try:
                    date_part = parser.parse(str(row["DATE"])).date()
                    time_part = parser.parse(str(row["TIME"])).time()
                    full_datetime = datetime.datetime.combine(date_part, time_part)

                    raw_content = str(row.get("CONTENT", "")).strip()

                    if not is_valid_message(raw_content):
                        continue

                    content = preprocess_message(raw_content)
                    if not content:
                        continue

                    is_in_time = False
                    if current_hour == 8:
                        start = datetime.datetime.combine(
                            now.date() - datetime.timedelta(days=1),
                            datetime.time(13, 0),
                        )
                        end = datetime.datetime.combine(
                            now.date(), datetime.time(1, 30)
                        )
                        is_in_time = start <= full_datetime < end
                    elif current_hour == 14:
                        start = datetime.datetime.combine(
                            now.date(), datetime.time(1, 0)
                        )
                        end = datetime.datetime.combine(
                            now.date(), datetime.time(14, 0)
                        )
                        is_in_time = start <= full_datetime < end
                    else:
                        is_in_time = (
                            (now - datetime.timedelta(hours=24)) <= full_datetime <= now
                        )

                    if is_in_time and content not in messages[sheet_name]:
                        messages[sheet_name].append(content)
                except:
                    continue
        except Exception as e:
            print(f"❌ Lỗi sheet '{sheet_name}': {e}")
    return messages


def write_to_sheet(spreadsheet, sheet_names, sheet_target_name, messages):
    try:
        sheet_names_with_data = [name for name in sheet_names if messages.get(name)]
        if not sheet_names_with_data:
            print(f"--- Không có dữ liệu để ghi vào {sheet_target_name} ---")
            return

        try:
            ws = spreadsheet.worksheet(sheet_target_name)
        except gspread.exceptions.WorksheetNotFound:
            ws = spreadsheet.add_worksheet(
                title=sheet_target_name, rows="1000", cols="20"
            )

        existing_data = ws.get_all_values()
        max_len = max(len(messages[s]) for s in sheet_names_with_data)

        rows_to_append = []
        for i in range(max_len):
            row = []
            for sheet_name in sheet_names_with_data:
                msg = messages[sheet_name][i] if i < len(messages[sheet_name]) else ""
                row.append(msg)

            if row not in existing_data:
                rows_to_append.append(row)

        if rows_to_append:
            ws.append_rows(rows_to_append, value_input_option="USER_ENTERED")
            print(
                f"✅ Đã ghi thêm {len(rows_to_append)} dòng mới vào [{sheet_target_name}]"
            )
        else:
            print(f"ℹ️ Không có dữ liệu mới (trùng lặp) cho [{sheet_target_name}]")

    except Exception as e:
        print(f"❌ Lỗi khi ghi vào sheet {sheet_target_name}: {e}")
# =========================
# Kiểm tra version Chrome
# =========================       
def get_installed_chrome_major_version():
    """Tự động kiểm tra Major Version của Chrome trên máy"""

    system = platform.system()
    try:
        if system == "Windows":
            import winreg
            # Đọc phiên bản Chrome từ Registry Windows
            try:
                key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Google\Chrome\BLBeacon")
            except FileNotFoundError:
                key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Google\Chrome\BLBeacon")
            version, _ = winreg.QueryValueEx(key, "version")
            return int(version.split('.')[0])

        elif system == "Linux":
            output = subprocess.check_output(["google-chrome", "--version"]).decode("utf-8")
            match = re.search(r"Google Chrome (\d+)\.", output)
            if match:
                return int(match.group(1))

        elif system == "Darwin":  # macOS
            cmd = r"/Applications/Google\ Chrome.app/Contents/MacOS/Google\ Chrome --version"
            output = subprocess.check_output(cmd, shell=True).decode("utf-8")
            match = re.search(r"Google Chrome (\d+)\.", output)
            if match:
                return int(match.group(1))
    except Exception as e:
        print(f"⚠️ Không thể tự động phát hiện phiên bản Chrome: {e}")
    
    return None



def get_driver():
    options = uc.ChromeOptions()
    options.add_argument("--headless=new")
    options.add_argument(
        "user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1920,1080")
    options.page_load_strategy = "eager"
    options.add_argument("--lang=en-GB")
    
    prefs = {
        "profile.cookie_controls_mode": 0,
        "credentials_enable_service": False,
        "profile.password_manager_enabled": False
    } 

    options.add_experimental_option("prefs", prefs)

    proxy_url = os.getenv("PROXY_URL")
    if proxy_url:
        options.add_argument(f"--proxy-server={proxy_url}")

    import subprocess
    import re

    chrome_version = None
    try:
        result = subprocess.check_output(["google-chrome", "--version"]).decode("utf-8")
        chrome_version = int(re.search(r"\d+", result).group(0))
        print(
            f"✅ Đã tự động nhận diện Chrome trên máy chủ là version: {chrome_version}"
        )
    except Exception:
        chrome_version = get_installed_chrome_major_version()

    if chrome_version:
        driver = uc.Chrome(options=options, version_main=chrome_version)
    else:
        driver = uc.Chrome(options=options)

    driver.execute_cdp_cmd(
        "Page.addScriptToEvaluateOnNewDocument",
        {"source": """
            Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
            window.navigator.chrome = { runtime: {} };
            Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
            Object.defineProperty(navigator, 'languages', { get: () => ['en-GB', 'en-US', 'en'] });
            Object.defineProperty(navigator, 'credentials', {
                get: () => undefined
            });

            window.PublicKeyCredential = undefined;
        """},
    )

    return driver


def login():
    driver = get_driver()

    # Truy cập link chuẩn cho Work/School
    driver.get("https://teams.microsoft.com/")
    wait = WebDriverWait(driver, 30)

    try:
        print("⏳ Đang đăng nhập...")

        # 1. Xử lý nút Sign in (nếu bị đẩy ra trang chờ)
        try:
            sign_btn = WebDriverWait(driver, 10).until(
                EC.element_to_be_clickable(
                    (
                        By.XPATH,
                        '//button[contains(., "Sign in")] | //a[contains(., "Sign in")] | //button[contains(., "Đăng nhập")]',
                    )
                )
            )
            sign_btn.click()
        except:
            pass  # Bỏ qua nếu form điền email hiện ra trực tiếp

        # 2. Ô nhập Email (Sử dụng Selector linh hoạt cho Microsoft)
        email_box = wait.until(
            EC.presence_of_element_located(
                (By.CSS_SELECTOR, 'input[type="email"], input[name="loginfmt"]')
            )
        )
        email_box.send_keys(email)
        email_box.send_keys(Keys.RETURN)

        time.sleep(3)
        # ====== THÊM ĐOẠN NÀY VÀO ======
        # Xử lý trường hợp Microsoft đòi gửi mã code, ép nó quay về dùng Mật khẩu
        try:
            use_pass_btn = WebDriverWait(driver, 5).until(
                EC.element_to_be_clickable(
                    (
                        By.XPATH,
                        '//*[contains(text(), "Use your password") or contains(text(), "Sử dụng mật khẩu")]',
                    )
                )
            )
            use_pass_btn.click()
            time.sleep(2)
        except:
            pass  # Nếu màn hình đi thẳng tới ô mật khẩu thì cứ bỏ qua bước này
        # ===============================
        # ====== XỬ LÝ MÀN HÌNH "Sign in another way" -> CHỌN USE YOUR PASSWORD ======
        try:
            # 1. Nếu có màn hình "Other ways to sign in" thì bấm trước
            try:
                other_ways_btn = WebDriverWait(driver, 4).until(
                    EC.presence_of_element_located(
                        (
                            By.XPATH,
                            '//*[contains(text(), "Other ways to sign in") or contains(text(), "Cách đăng nhập khác")]',
                        )
                    )
                )
                driver.execute_script("arguments[0].click();", other_ways_btn)
                print("👉 Đã chọn: Other ways to sign in")
                time.sleep(2)
            except:
                pass

            # 2. Chọn dòng "Use your password" (Dùng JS click để không bị trượt)
            use_pass_btn = WebDriverWait(driver, 6).until(
                EC.presence_of_element_located(
                    (
                        By.XPATH,
                        '//*[contains(text(), "Use your password") or contains(text(), "Sử dụng mật khẩu")]/ancestor::div[@role="button"] '
                        '| //*[contains(text(), "Use your password") or contains(text(), "Sử dụng mật khẩu")]',
                    )
                )
            )
            driver.execute_script("arguments[0].click();", use_pass_btn)
            print("👉 Đã kích hoạt: Use your password")
            time.sleep(3)
        except Exception as e:
            print("ℹ️ Bỏ qua chọn phương thức (hoặc đã ở màn hình nhập pass):", e)
        # ===========================================================================
        # 3. Ô nhập Password
        pass_box = wait.until(
            EC.presence_of_element_located(
                (By.CSS_SELECTOR, 'input[type="password"], input[name="passwd"]')
            )
        )
        pass_box.send_keys(password)
        pass_box.send_keys(Keys.RETURN)

        # 4. Xử lý nút "Stay signed in?" (Chọn No để không lưu đăng nhập)
        try:
            print("⏳ Đang xử lý màn hình Stay signed in...")
            no_btn = WebDriverWait(driver, 15).until(
                EC.element_to_be_clickable(
                    (
                        By.XPATH,
                        '//*[@id="declineButton"] | //*[@id="idBtn_Back"] | //*[@value="No"] | //button[contains(., "No")]',
                    )
                )
            )
            no_btn.click()
            time.sleep(3)
        except:
            print("⚠️ Không thấy màn hình Stay signed in, tiếp tục...")
            pass

        print("✅ Đăng nhập thành công")

        # Chờ giao diện Teams load hẳn
        time.sleep(15)

        return driver

    except Exception as e:
        save_screenshot(driver, "login_error.png")
        print("❌ Đăng nhập thất bại:", e)
        try:
            driver.quit()
        except Exception:
            pass
        return None


if __name__ == "__main__":
    # Khởi tạo kết nối Google Sheets
    spreadsheet = None
    sheet_names = []
    try:
        client = get_gspread_client()
        spreadsheet = client.open_by_key(SPREADSHEET_ID)
        sheet_names = [s.title for s in spreadsheet.worksheets()]
        print(f"✅ Kết nối Google Sheets thành công! Spreadsheet ID: {SPREADSHEET_ID}")
    except Exception as e:
        print(f"⚠️ Cảnh báo kết nối Google Sheets: {e}")

    driver = None
    for attempt_login in range(5):
        driver = login()
        if driver:
            print("✅ Đăng nhập Teams thành công!")
            break
        else:
            print(f"⚠️ Thử đăng nhập lại lần {attempt_login + 1}/5...")
            time.sleep(2)

    if not driver:
        print("❌ Đăng nhập Teams không thành công!")
        exit(1)

    try:
        driver.save_screenshot("after_login.png")
        open_chat(driver, chat)

        current_hour = datetime.datetime.now(pytz.timezone("Asia/Ho_Chi_Minh")).hour

        if spreadsheet and sheet_names:
            messages = get_filtered_messages(spreadsheet, sheet_names, current_hour)
            combined_msgs = combine_messages(messages)

            print(f"\n✅ Báo cáo lọc được lúc {current_hour}h:")

            for sheet_name, msg_content in combined_msgs.items():
                print(f"Testing\nSheet: [ {sheet_name} ]\nMessage: [ {msg_content} ]\n")
                message = f"[ {sheet_name} ]\n" + msg_content
                send_message(driver, message)

            write_to_sheet(spreadsheet, sheet_names, "Report", messages)
            write_to_sheet(spreadsheet, sheet_names, "GetReport", messages)
        else:
            print("⚠️ Bỏ qua lọc và ghi báo cáo do không có kết nối Google Sheets.")
    finally:
        if driver:
            try:
                driver.quit()
            except Exception:
                pass
        print("✅ Hoàn tất toàn bộ công việc!")
