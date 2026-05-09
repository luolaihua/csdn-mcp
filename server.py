#!/usr/bin/env python3
"""
CSDN MCP Server v5 — Cookie 持久化 + URL 捕获 + 封面 + 专栏

新增：
- Cookie 持久化：重启后自动恢复登录态，免重复扫码
- 发布后自动返回文章 URL
- 封面图上传支持（cover_path / frontmatter cover 字段）
- 分类专栏选择支持（category 参数）
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
_login_page: Page | None = None
_logged_in = False

QR_FILE = Path("/tmp/csdn_qrcode.png")
COOKIE_FILE = Path.home() / ".hermes" / "csdn_cookies.json"

CSDN_HOME = "https://www.csdn.net/"
CSDN_LOGIN = "https://passport.csdn.net/login?code=public"
CSDN_EDITOR = "https://editor.csdn.net/md/"

SELECTORS = {
    "title": '//div[contains(@class,"article-bar")]//input[contains(@placeholder,"请输入文章标题")]',
    "publish_btn": '//button[contains(@class,"btn-publish") and contains(text(),"发布文章")]',
    "tag_add": '//div[@class="modal"]//button[contains(text(),"添加文章标签")]',
    "tag_input": '//div[contains(@class,"mark_selection_box")]//input[contains(@placeholder,"请输入文字搜索")]',
    "tag_close": '//div[contains(@class,"mark_selection_box")]//button[@title="关闭"]',
    "summary": '//div[@class="desc-box"]//textarea[contains(@placeholder,"摘要")]',
    "final_publish": '//div[@role="dialog"]//button[contains(@class,"btn-b-red") and contains(text(),"发布文章")]',
    "cover_btn": '//div[@role="dialog"]//button[contains(text(),"从本地上传")]',
    "category_cb": '//div[@role="dialog"]//input[@type="checkbox" and @value="{cat}"]',
}


# ====== Cookie 持久化 ======

async def _ensure_browser():
    global _playwright, _browser, _context, _logged_in
    if _browser is None:
        _playwright = await async_playwright().start()
        _browser = await _playwright.chromium.launch(headless=True)
        _context = await _browser.new_context(viewport={"width": 1280, "height": 800})

        # 尝试从文件恢复 cookies
        if COOKIE_FILE.exists():
            try:
                saved = json.loads(COOKIE_FILE.read_text())
                if saved:
                    await _context.add_cookies(saved)
                    # 验证 cookies 是否仍然有效
                    test_page = await _context.new_page()
                    await test_page.goto(CSDN_EDITOR, wait_until="domcontentloaded", timeout=15000)
                    await asyncio.sleep(2)
                    if "login" not in test_page.url and "passport" not in test_page.url:
                        _logged_in = True
                    await test_page.close()
            except: pass


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


# ====== MCP Tools ======

@mcp.tool
async def csdn_login() -> str:
    """第一步：获取 CSDN 微信扫码登录二维码。如已登录则直接返回成功。"""
    global _login_page, _logged_in
    await _ensure_browser()

    if _logged_in:
        return "✅ 已登录 CSDN，无需重复扫码。"

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
    """第二步：确认扫码登录是否成功。成功后自动持久化 cookies。"""
    global _logged_in, _context, _login_page

    if _login_page is None:
        return "❌ 请先运行 csdn_login"

    try:
        current_url = _login_page.url
    except:
        _login_page = None
        return "⏳ 登录页已关闭，请重新运行 csdn_login"

    if "passport" in current_url:
        await asyncio.sleep(1)
        try:
            current_url = _login_page.url
        except: pass

    if "passport" in current_url:
        return f"⏳ 尚未检测到登录回调，当前页面: {current_url}"

    _logged_in = True

    # 持久化 cookies 到文件
    try:
        COOKIE_FILE.parent.mkdir(parents=True, exist_ok=True)
        cookies = await _context.cookies()
        COOKIE_FILE.write_text(json.dumps(cookies, indent=2, ensure_ascii=False))
    except: pass

    return "✅ CSDN 登录成功！Cookies 已持久化，重启后无需重复扫码。"


@mcp.tool
def csdn_check_login() -> str:
    """检查当前登录状态"""
    return "✅ 已登录 CSDN，可直接发布。" if _logged_in else "❌ 未登录，请运行 csdn_login 扫码登录。"


@mcp.tool
async def csdn_publish(
    markdown_path: str = "",
    markdown_content: str = "",
    tags: str = "",
    category: str = "",
    cover_path: str = "",
    draft: bool = True,
) -> str:
    """发布 Markdown 到 CSDN（需先登录）。

    参数：
    - draft=true（默认）：只存草稿箱
    - draft=false：直接发布（需用户确认）
    - tags：逗号分隔的标签
    - category：分类专栏名（发布模式生效）
    - cover_path：封面图本地路径（发布模式生效）
    - markdown_path / markdown_content：二选一
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
    cover = cover_path or fm.get("cover", "")
    if cover and not Path(cover).is_absolute():
        cover = ""

    page = await _context.new_page()

    try:
        # 1. 进入编辑器
        await page.goto(CSDN_EDITOR, wait_until="domcontentloaded", timeout=15000)
        await asyncio.sleep(3)

        if "login" in page.url or "passport" in page.url:
            _logged_in = False
            COOKIE_FILE.unlink(missing_ok=True)
            return "❌ 登录已过期（cookies 失效），请重新运行 csdn_login。"

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
                cm.CodeMirror.focus();
                cm.dispatchEvent(new Event('input', {{bubbles:true}}));
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
            await page.click('.editor')
            await asyncio.sleep(0.5)
            await page.keyboard.type(" ")
            await page.keyboard.press("Backspace")

        if draft:
            await asyncio.sleep(3)
            return json.dumps({
                "success": True, "title": title,
                "message": f"✅ 「{title}」内容已注入编辑器！请到 CSDN 后台「内容管理→草稿箱」查看。"
            }, ensure_ascii=False)

        # ====== 发布模式 ======
        await asyncio.sleep(2)

        # 打开发布弹窗
        pb = await page.wait_for_selector(f'xpath={SELECTORS["publish_btn"]}', timeout=10000)
        await pb.click()
        await asyncio.sleep(3)

        # 封面图上传（Element UI upload，需 file_chooser）
        if cover and Path(cover).exists():
            try:
                async with page.expect_file_chooser() as fc_info:
                    cb = await page.wait_for_selector(f'xpath={SELECTORS["cover_btn"]}', timeout=5000)
                    await cb.click()
                file_chooser = await fc_info.value
                await file_chooser.set_files(cover)
                await asyncio.sleep(3)  # 等 CSDN 上传
            except Exception as e:
                pass

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

        # 分类专栏（直接勾选可见checkbox）
        cat = category or fm.get("category", "")
        if cat:
            try:
                cat_xpath = SELECTORS["category_cb"].replace("{cat}", cat)
                cat_cb = await page.wait_for_selector(f"xpath={cat_xpath}", timeout=5000)
                await cat_cb.click()
                await asyncio.sleep(0.5)
            except: pass

        # 最终发布
        final = await page.wait_for_selector(f'xpath={SELECTORS["final_publish"]}', timeout=10000)
        await final.click()

        # 等待发布完成 + 捕获文章 URL（双策略）
        article_url = None
        # 策略1: 检查当前页是否被重定向到文章页
        await asyncio.sleep(4)
        try:
            current = page.url
            if "blog.csdn.net" in current and "/article/details/" in current:
                article_url = current
        except: pass
        # 策略2: 扫描所有页面（新标签页）
        if not article_url:
            for _ in range(10):
                await asyncio.sleep(1)
                for pg in _context.pages:
                    if "blog.csdn.net" in pg.url and "/article/details/" in pg.url:
                        article_url = pg.url; break
                if article_url:
                    break
        # 策略3: 去文章管理页拿最新文章链接
        if not article_url:
            try:
                mgmt = await _context.new_page()
                await mgmt.goto("https://mp.csdn.net/mp_blog/manage/article", wait_until="domcontentloaded", timeout=15000)
                await asyncio.sleep(3)
                article_url = await mgmt.evaluate("""() => {
                    var a = document.querySelector('a[href*=\"article/details\"]');
                    return a ? a.href : null;
                }""")
                await mgmt.close()
            except: pass

        await page.close()

        if article_url:
            return json.dumps({
                "success": True, "title": title, "url": article_url,
                "message": f"✅ 「{title}」已发布！\n🔗 {article_url}"
            }, ensure_ascii=False)
        else:
            return json.dumps({
                "success": True, "title": title,
                "message": f"✅ 「{title}」已发布！请到 CSDN 后台查看（未能自动捕获 URL）。"
            }, ensure_ascii=False)

    except Exception as e:
        return json.dumps({"success": False, "error": str(e)}, ensure_ascii=False)


if __name__ == "__main__":
    mcp.run()
