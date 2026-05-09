"""CSDN 持久化 Cookie 认证模块

两步扫码登录：
  1. Headless 浏览器 → CSDN 登录页 → 抓二维码 → 返回图片
  2. 用户微信扫码后 → 检查登录状态 → 保存 cookies
WSL 无图形界面，通过扫码绕过 X Server。
"""

import json
import time
import base64
import tempfile
from pathlib import Path
from playwright.sync_api import sync_playwright, Browser, BrowserContext, Page

from csdn.config import CSDN_HOME, CSDN_EDITOR_URL, COOKIE_FILE

# 临时 cookie 文件（扫码前的会话 cookie）
TEMP_COOKIE_FILE = Path(tempfile.gettempdir()) / "csdn_temp_cookies.json"


def load_cookies() -> list[dict] | None:
    """加载已保存的最终 cookies"""
    if not COOKIE_FILE.exists():
        return None
    try:
        return json.loads(COOKIE_FILE.read_text())
    except (json.JSONDecodeError, KeyError):
        return None


def save_cookies(context: BrowserContext):
    """保存 cookies 到永久文件"""
    COOKIE_FILE.parent.mkdir(parents=True, exist_ok=True)
    cookies = context.cookies()
    COOKIE_FILE.write_text(json.dumps(cookies, indent=2, ensure_ascii=False))


def _extract_qr_code(page: Page) -> str | None:
    """从 CSDN 微信登录 tab 提取二维码图片，保存为 PNG"""
    try:
        page.wait_for_selector(".public-code:not(.loading) img", timeout=15000)
    except Exception:
        pass
    time.sleep(1)

    # 提取 base64 QR code
    img_src = page.evaluate("""() => {
        for (const sel of ['.public-code:not(.loading) img', '.login-code-wechat img', '.public-code img', 'img[src^="data:image"]']) {
            const img = document.querySelector(sel);
            if (img && img.src && img.src.startsWith('data:image')) return img.src;
        }
        return null;
    }""")

    if not img_src:
        return None

    try:
        header, data = img_src.split(",", 1)
        img_bytes = base64.b64decode(data)
        output = Path(tempfile.gettempdir()) / "csdn_qrcode.png"
        output.write_bytes(img_bytes)
        return str(output)
    except Exception:
        return None


def get_qr_code() -> str:
    """
    第一步：打开 CSDN 登录页，提取微信扫码二维码。
    保存临时 cookies 到 /tmp/csdn_temp_cookies.json。
    返回 JSON: {"status": "waiting", "image": "/tmp/csdn_qrcode.png", "message": "请用微信扫码"}
    """
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            viewport={"width": 1280, "height": 800},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        )
        page = context.new_page()

        try:
            page.goto("https://passport.csdn.net/login?code=public", wait_until="networkidle", timeout=30000)
            time.sleep(3)

            # 确保在微信登录 tab
            try:
                wechat_tab = page.wait_for_selector('span:text("微信登录")', timeout=5000)
                wechat_tab.click()
                time.sleep(2)
            except Exception:
                pass

            qr_path = _extract_qr_code(page)
            if not qr_path:
                browser.close()
                return "❌ 无法获取二维码，请重试。"

            # 保存临时 cookies（扫码前的会话）
            temp_cookies = context.cookies()
            TEMP_COOKIE_FILE.parent.mkdir(parents=True, exist_ok=True)
            TEMP_COOKIE_FILE.write_text(json.dumps(temp_cookies))

            browser.close()
            return json.dumps({
                "status": "waiting",
                "image": qr_path,
                "message": "请用微信扫描上方二维码，完成后告诉我「已扫码」"
            }, ensure_ascii=False)

        except Exception as e:
            browser.close()
            return json.dumps({"status": "error", "message": str(e)}, ensure_ascii=False)


def confirm_login() -> str:
    """
    第二步：加载临时 cookies → 访问 CSDN 编辑器页面验证登录。
    编辑器页面是实际需要登录的目标，验证更可靠。
    成功则保存最终 cookies 到 ~/.hermes/csdn_cookies.json。
    """
    if not TEMP_COOKIE_FILE.exists():
        return "⚠️ 请先运行 csdn_login 获取二维码，扫码后再试。"

    try:
        temp_cookies = json.loads(TEMP_COOKIE_FILE.read_text())
    except Exception:
        return "⚠️ 临时 cookie 文件损坏，请重新运行 csdn_login。"

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1280, "height": 800})
        context.add_cookies(temp_cookies)
        page = context.new_page()

        try:
            # 直接访问编辑器页面（需要登录才能访问）
            page.goto(CSDN_EDITOR_URL, wait_until="domcontentloaded", timeout=20000)
            page.wait_for_timeout(3000)

            current_url = page.url
            logged_in = (
                "login" not in current_url.lower()
                and "passport" not in current_url.lower()
                and "editor.csdn.net" in current_url
            )

            if not logged_in:
                # 尝试先访问首页让 cookie 跨域传播
                page.goto(CSDN_HOME, wait_until="domcontentloaded", timeout=15000)
                page.wait_for_timeout(2000)
                # 再试编辑器
                page.goto(CSDN_EDITOR_URL, wait_until="domcontentloaded", timeout=20000)
                page.wait_for_timeout(3000)
                current_url = page.url
                logged_in = (
                    "login" not in current_url.lower()
                    and "passport" not in current_url.lower()
                    and "editor.csdn.net" in current_url
                )

            if logged_in:
                # 从编辑器页面保存 cookies（确保域正确）
                save_cookies(context)
                TEMP_COOKIE_FILE.unlink(missing_ok=True)
                browser.close()
                return "✅ CSDN 登录成功！编辑器可访问，Cookies 已保存。"

            browser.close()
            return "⏳ 尚未完成扫码，请在微信中确认登录后再试。"

        except Exception as e:
            browser.close()
            return f"⚠️ 检查登录状态失败: {e}"


def create_authenticated_context(playwright) -> tuple[Browser, BrowserContext]:
    """创建带 cookie 认证的浏览器上下文"""
    cookies = load_cookies()
    browser = playwright.chromium.launch(headless=True)
    context = browser.new_context()
    if cookies:
        context.add_cookies(cookies)
    return browser, context


def check_login_status() -> str:
    """验证 CSDN cookies 是否有效"""
    cookies = load_cookies()
    if not cookies:
        return "❌ 未找到已保存的 cookies，请先运行 csdn_login 登录。"

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context()
        context.add_cookies(cookies)
        page = context.new_page()

        try:
            page.goto(CSDN_HOME, wait_until="domcontentloaded", timeout=15000)
            page.wait_for_selector('a[href*="blog.csdn.net"]', timeout=8000)
            browser.close()
            return "✅ CSDN cookies 有效，已处于登录状态。"
        except Exception:
            browser.close()
            return "⚠️ CSDN cookies 已过期，请重新运行 csdn_login 登录。"
