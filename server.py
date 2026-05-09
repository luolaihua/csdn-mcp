#!/usr/bin/env python3
"""
CSDN MCP Server v4 — QR 页面持久化 + async Playwright

核心修复：csdn_login 开的 QR 页面保持存活，扫码回调在该页面上完成，
csdn_confirm 检查该页面的跳转状态而非重新打开页面。
"""
import json
import asyncio
from pathlib import Path
from fastmcp import FastMCP
from playwright.async_api import async_playwright, Browser, BrowserContext, Page

mcp = FastMCP("CSDN Publisher")

_playwright = None
_browser: Browser | None = None
_context: BrowserContext | None = None
_login_page: Page | None = None  # QR 登录页持久引用
_logged_in = False

QR_FILE = Path("/tmp/csdn_qrcode.png")
COOKIE_FILE = Path.home() / ".hermes" / "csdn_cookies.json"

CSDN_HOME = "https://www.csdn.net/"
CSDN_LOGIN = "https://passport.csdn.net/login?code=public"
CSDN_EDITOR = "https://editor.csdn.net/md/"

SELECTORS = {
    "title": '//div[contains(@class,"article-bar")]//input[contains(@placeholder,"请输入文章标题")]',
    "publish_btn": '//button[contains(@class,"btn-publish") and contains(text(),"发布文章")]',
    "tag_add": '//div[@class="mark_selection"]//button[contains(text(),"添加文章标签")]',
    "tag_input": '//div[contains(@class,"mark_selection_box")]//input[contains(@placeholder,"请输入文字搜索")]',
    "tag_close": '//div[contains(@class,"mark_selection_box")]//button[@title="关闭"]',
    "summary": '//div[@class="desc-box"]//textarea[contains(@placeholder,"摘要")]',
    "final_publish": '//div[contains(@class,"modal__button-bar")]//button[contains(text(),"发布文章")]',
}


async def _ensure_browser():
    global _playwright, _browser, _context
    if _browser is None:
        _playwright = await async_playwright().start()
        _browser = await _playwright.chromium.launch(headless=True)
        _context = await _browser.new_context(viewport={"width": 1280, "height": 800})


def _parse_front_matter(text: str) -> dict:
    lines = text.strip().split("\n")
    if not lines or lines[0].strip() != "---":
        return {}
    end = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            end = i; break
    if end is None:
        return {}
    result = {}
    for line in lines[1:end]:
        if ":" in line:
            k, _, v = line.partition(":")
            result[k.strip()] = v.strip().strip('"').strip("'")
    return result


# ========== MCP Tools ==========

@mcp.tool
async def csdn_login() -> str:
    """第一步：获取 CSDN 微信扫码登录二维码。"""
    global _login_page
    await _ensure_browser()
    
    # 关闭旧登录页
    if _login_page:
        try: await _login_page.close()
        except: pass
    
    _login_page = await _context.new_page()
    await _login_page.goto(CSDN_LOGIN, wait_until="domcontentloaded", timeout=15000)
    await asyncio.sleep(3)
    
    try:
        qr_el = await _login_page.wait_for_selector(".login-code-wechat", timeout=10000)
        await qr_el.screenshot(path=str(QR_FILE))
    except Exception as e:
        return f"❌ 获取二维码失败: {e}"
    
    return json.dumps({
        "status": "waiting",
        "image": str(QR_FILE),
        "message": "请用微信扫码并在手机上确认登录，完成后调用 csdn_confirm"
    }, ensure_ascii=False)


@mcp.tool
async def csdn_confirm() -> str:
    """第二步：确认扫码登录是否成功。检查 QR 页是否已跳转离开登录页。"""
    global _logged_in, _context, _login_page
    
    if _login_page is None:
        return "❌ 请先运行 csdn_login"
    
    try:
        current_url = _login_page.url
    except:
        _login_page = None
        return "⏳ 登录页已关闭，请重新运行 csdn_login"
    
    # QR 扫码成功后，页面通常会从 passport 跳转到 CSDN 首页或回调页
    if "passport" in current_url:
        # 可能页面刷新到了 applets 页 — 等待自动跳转
        await asyncio.sleep(1)
        try:
            current_url = _login_page.url
        except:
            pass
    
    if "passport" in current_url:
        return f"⏳ 尚未检测到登录回调，当前页面: {current_url}"
    
    _logged_in = True
    
    # 保存 cookies
    try:
        COOKIE_FILE.parent.mkdir(parents=True, exist_ok=True)
        cookies = await _context.cookies()
        COOKIE_FILE.write_text(json.dumps(cookies, indent=2, ensure_ascii=False))
    except: pass
    
    return "✅ CSDN 登录成功！浏览器会话已就绪，可直接发布。"


@mcp.tool
def csdn_check_login() -> str:
    """检查当前登录状态"""
    return "✅ 已登录 CSDN，可直接发布。" if _logged_in else "❌ 未登录，请运行 csdn_login 扫码登录。"


@mcp.tool
async def csdn_publish(
    markdown_path: str = "",
    markdown_content: str = "",
    tags: str = "",
    draft: bool = True,
) -> str:
    """发布 Markdown 到 CSDN（需先登录）。
    
    - draft=true（默认）：只存草稿箱，不发布
    - draft=false：直接发布文章（需用户确认后调用）
    """
    global _context, _logged_in

    if not _logged_in:
        return "❌ 未登录 CSDN，请先运行 csdn_login + csdn_confirm。"

    if markdown_path:
        content = Path(markdown_path).read_text(encoding="utf-8")
    elif markdown_content:
        content = markdown_content
    else:
        return "❌ 必须提供 markdown_path 或 markdown_content"

    fm = _parse_front_matter(content)
    title = fm.get("title", "未命名")
    body = content.split("---", 2)[-1].strip() if fm else content.strip()

    page = await _context.new_page()

    try:
        # 1. 进入编辑器
        await page.goto(CSDN_EDITOR, wait_until="domcontentloaded", timeout=15000)
        await asyncio.sleep(3)

        if "login" in page.url or "passport" in page.url:
            _logged_in = False
            return "❌ 登录已过期，请重新运行 csdn_login。"

        # 2. 填标题
        title_el = await page.wait_for_selector(f'xpath={SELECTORS["title"]}', timeout=10000)
        await title_el.click()
        await title_el.fill(title)
        await asyncio.sleep(1)

        # 3. CodeMirror 注入正文 + 触发自动保存
        escaped = json.dumps(body)
        ok = await page.evaluate(f"""
        (function(){{
            var cm = document.querySelector('.CodeMirror');
            if(cm && cm.CodeMirror){{
                cm.CodeMirror.setValue({escaped});
                // 触发 change 事件让 CSDN 编辑器检测到修改
                cm.CodeMirror.focus();
                cm.dispatchEvent(new Event('input', {{bubbles:true}}));
                // 模拟一次性输入触发保存
                var ta = cm.querySelector('textarea');
                if(ta){{ ta.dispatchEvent(new Event('input', {{bubbles:true}})); }}
                return true;
            }}
            return false;
        }})()
        """)
        if not ok:
            await page.click('.editor')
            await asyncio.sleep(1)
            await page.keyboard.insert_text(body[:5000])
        else:
            # CodeMirror 注入成功，但需触发 CSDN 的自动保存：
            # 在编辑器里模拟一次真实键入唤醒保存机制
            await page.click('.editor')
            await asyncio.sleep(0.5)
            await page.keyboard.type(" ")  # 触发输入事件
            await page.keyboard.press("Backspace")

        if draft:
            # 草稿模式：不关页面，让自动保存持续生效
            await asyncio.sleep(3)
            page_url = page.url
            # 不 close，保持页面存活
            return json.dumps({
                "success": True, "title": title,
                "message": f"✅ 「{title}」内容已注入编辑器！请到 CSDN 后台「内容管理→草稿箱」查看。"
            }, ensure_ascii=False)

        # === 发布模式 ===
        await asyncio.sleep(2)

        # 打开发布弹窗
        pb = await page.wait_for_selector(f'xpath={SELECTORS["publish_btn"]}', timeout=10000)
        await pb.click()
        await asyncio.sleep(3)

        # 标签
        tag_list = [t.strip() for t in (tags or fm.get("tags","")).replace("，",",").split(",") if t.strip()]
        if tag_list:
            try:
                add_btn = await page.wait_for_selector(f'xpath={SELECTORS["tag_add"]}', timeout=5000)
                await add_btn.click()
                await asyncio.sleep(1)
                ti = await page.wait_for_selector(f'xpath={SELECTORS["tag_input"]}', timeout=5000)
                for t in tag_list[:5]:
                    await ti.fill(t)
                    await asyncio.sleep(1)
                    await ti.press("Enter")
                    await asyncio.sleep(0.5)
                close_btn = await page.wait_for_selector(f'xpath={SELECTORS["tag_close"]}', timeout=5000)
                await close_btn.click()
            except: pass

        # 最终发布
        final = await page.wait_for_selector(f'xpath={SELECTORS["final_publish"]}', timeout=10000)
        await final.click()
        await asyncio.sleep(3)

        await page.close()
        return json.dumps({
            "success": True, "title": title,
            "message": f"✅ 「{title}」已发布！请到 CSDN 后台查看。"
        }, ensure_ascii=False)

    except Exception as e:
        return json.dumps({"success": False, "error": str(e)}, ensure_ascii=False)


if __name__ == "__main__":
    mcp.run()
