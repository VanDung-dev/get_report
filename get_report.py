import datetime
from dotenv import load_dotenv
import pytz
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from dateutil import parser
import re
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
import undetected_chromedriver as uc
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from webdriver_manager.chrome import ChromeDriverManager
import time
import json
import os

load_dotenv()

email = os.environ.get("TEAMS_EMAIL")
password = os.environ.get("TEAMS_PASSWORD")
gcp_credentials_json = os.environ.get("GCP_SA_KEY")
chat = "GetReport"
local_tz = pytz.timezone("Asia/Ho_Chi_Minh")
SPREADSHEET_ID = "1_m7s-1-I-SOFfzlWe7CBf5fstFir7qXYAKW4j-8hKYM"
SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

# Khởi tạo kết nối
creds_dict = json.loads(gcp_credentials_json)
creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, SCOPES)
client = gspread.authorize(creds)
print(f"Spreadsheet ID: {SPREADSHEET_ID}")
spreadsheet = client.open_by_key(SPREADSHEET_ID)

# Lấy danh sách TẤT CẢ sheet (Đã bỏ [:-1] để không bị sót nhân viên)
sheet_names = [s.title for s in spreadsheet.worksheets()]

MESSAGE_PATTERN = re.compile(r".*\+\s*(\d+)/.*", re.IGNORECASE | re.DOTALL)


def display_screenshot(driver: webdriver.Chrome, file_name: str = "screenshot.png"):
    """Chụp màn hình và hiển thị"""
    driver.save_screenshot(file_name)
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


def get_filtered_messages(current_hour):
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


def write_to_sheet(sheet_target_name, messages):
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


def get_driver():
    options = uc.ChromeOptions()
    options.add_argument("--headless=new")
    options.add_argument(
        "user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ) # Cập nhật User-Agent mới nhất của Chrome 120 để tránh bị nhận diện là bot
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1920,1080")
    options.page_load_strategy = "eager"
    options.add_argument("--lang=en-GB")
    
    prefs = {"profile.cookie_controls_mode": 0,
        "credentials_enable_service": False,      # Tắt popup hỏi lưu pass
        "profile.password_manager_enabled": False # Tắt trình quản lý mật khẩu
    } 

    options.add_experimental_option("prefs", prefs)

    proxy_url = os.getenv("PROXY_URL")
    if proxy_url:
        options.add_argument(f"--proxy-server={proxy_url}")

    import subprocess
    import re

    chrome_version = None
    try:
        # Lệnh này sẽ chạy thành công trên máy chủ Ubuntu của GitHub Actions
        # Lấy output (ví dụ: "Google Chrome 147.0.7727.55")
        result = subprocess.check_output(["google-chrome", "--version"]).decode("utf-8")
        # Dùng Regex để tách lấy con số đầu tiên (147)
        chrome_version = int(re.search(r"\d+", result).group(0))
        print(
            f"✅ Đã tự động nhận diện Chrome trên máy chủ là version: {chrome_version}"
        )
    except Exception:
        # Nếu chạy thủ công trên Windows ở máy tính cá nhân nó sẽ nhảy vào đây
        pass

    # Khởi tạo Driver với đúng phiên bản máy chủ đang có
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
    driver.get("https://teams.live.com/v2/")
    wait = WebDriverWait(driver, 30)

    try:
        print("⏳ Đang tiến hành đăng nhập...")
        sign_in_btn = wait.until(
            EC.element_to_be_clickable((By.XPATH, '//button[contains(., "Sign in")]'))
        )
        sign_in_btn.click()

        email_input = wait.until(
            EC.presence_of_element_located((By.ID, "usernameEntry"))
        )
        email_input.send_keys(email)
        email_input.send_keys(Keys.RETURN)
        time.sleep(3)

        try:
            use_pass_btn = WebDriverWait(driver, 10).until(
                EC.element_to_be_clickable(
                    (By.XPATH, '//span[contains(text(), "Use your password")]')
                )
            )
            use_pass_btn.click()
        except:
            pass

        pass_input = wait.until(
            EC.presence_of_element_located((By.ID, "passwordEntry"))
        )
        pass_input.send_keys(password)
        pass_input.send_keys(Keys.RETURN)

        try:
            no_btn = WebDriverWait(driver, 15).until(
                EC.element_to_be_clickable(
                    (By.CSS_SELECTOR, 'button[data-testid="secondaryButton"]')
                )
            )
            no_btn.click()
        except:
            pass

        print("✅ Đăng nhập thành công!")
        time.sleep(15)

        try:
            extra_signin = driver.find_elements(
                By.XPATH,
                '//button[contains(., "Sign in") or contains(@aria-describedby, "signIn-title singIn-subtitle")]',
            )
            if len(extra_signin) > 0:
                print("Phát hiện trang bắt Sign in tiếp theo...")
                extra_signin[0].click()
                print("Đã ấn nút Sign in")
                time.sleep(10)

                print("Bắt đầu ấn nút Retry")
                actions = webdriver.ActionChains(driver)
                actions.move_by_offset(500, 500).click().perform()
                actions.send_keys(Keys.TAB).perform()
                time.sleep(1)
                actions.send_keys(Keys.ENTER).perform()
                print("Đã ấn nút Retry")
                time.sleep(20)
            else:
                print(
                    "👉 Giao diện Teams đã load thẳng, không có popup chặn, tiếp tục công việc!"
                )

        except Exception as e:
            print(f"⚠️ Bỏ qua lỗi check màn hình phụ: {e}")

        driver.save_screenshot("after_login_success.png")
        return driver

    except Exception as e:
        driver.save_screenshot("error_login.png")
        print(f"❌ Lỗi đăng nhập chính: {e}")
        driver.quit()
        return None


if __name__ == "__main__":
    for attempt_login in range(5):
        driver = login()
        if driver:
            print("login thành công")
            break
        else:
            print(f"⚠️ Thử đăng nhập lại lần {attempt_login + 1}/5...")
            time.sleep(2)

    time.sleep(5)
    if not driver:
        print("❌ Đăng nhập không thành công!")
        exit()  # Đảm bảo dừng chương trình nếu không đăng nhập được

    driver.save_screenshot("after_login.png")
    open_chat(driver, chat)

    current_hour = datetime.datetime.now(pytz.timezone("Asia/Ho_Chi_Minh")).hour

    messages = get_filtered_messages(current_hour)
    combined_msgs = combine_messages(messages)

    print(f"\n✅ Báo cáo lọc được lúc {current_hour}h:")

    # Chỉ duyệt qua những sheet có dữ liệu, tối ưu logic gửi Teams
    for sheet_name, msg_content in combined_msgs.items():
        print(f"Testing\nSheet: [ {sheet_name} ]\nMessage: [ {msg_content} ]\n")
        message = f"[ {sheet_name} ]\n" + msg_content
        send_message(driver, message)

    write_to_sheet("Report", messages)
    write_to_sheet("GetReport", messages)

    driver.quit()
    print("✅ Hoàn tất toàn bộ công việc!")
