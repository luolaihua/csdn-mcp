#!/usr/bin/env python3
"""
CSDN MCP Server v18 — fix: innerHTML img search for upload extraction

"""
import json
import re
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
COOKIE_FILE = Path("/home/laihluo/.hermes") / "csdn_cookies.json"

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
    "img_btn": 'button.navigation-bar__button:has-text("图片")',
    "img_modal": '.modal__inner-1[aria-label="Insert image"]',
    "img_input": '.modal__inner-1[aria-label="Insert image"] input[type="file"]',
    "editor_body": 'pre.editor__inner',
    "save_btn_bottom": 'button.btn-save',
    "save_btn_toolbar": 'button.button-save',
}


# ====== 辅助函数 ======

def _js_escape(text: str) -> str:
    """转义为 JS 模板字符串（保留换行/中文，不转 \\u）"""
    return text.replace('\\', '\\\\').replace('`', '\\`').replace('$', '\\$')

def _resolve_image_path(img_path: str, base_dir: Path) -> Path | None:
    """解析图片路径，返回绝对路径或 None"""
    p = Path(img_path)
    if p.is_absolute() and p.exists():
        return p
    resolved = base_dir / p
    if resolved.exists():
        return resolved
    return None


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
                    try:
                        await test_page.wait_for_selector('input[placeholder*="请输入文章标题"]', timeout=10000)
                        _logged_in = True
                    except:
                        _logged_in = False
                    await test_page.close()
            except Exception as e:
                print(f"[WARN] cookie 加载验证失败: {e}")


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


async def _upload_inline_images(page: Page, body: str, base_dir: Path) -> str:
    """扫描 markdown 中的本地图片，上传到 CSDN 图床，返回替换后的正文
    
    每个图片使用独立的 browser context 上传，避免 CSDN 的 localStorage 状态锁。
    """
    global _browser
    from playwright.async_api import async_playwright
    import json as _json
    
    img_pattern = re.compile(r'!\[([^\]]*)\]\(([^)]+)\)')
    uploads = []
    for m in img_pattern.finditer(body):
        path = m.group(2)
        if path.startswith(('http://', 'https://', 'data:')):
            continue
        abs_path = _resolve_image_path(path, base_dir)
        if abs_path:
            uploads.append((m.group(0), m.group(1), str(abs_path), path))
    
    if not uploads:
        return body
    
    print(f"  📤 准备上传 {len(uploads)} 张图片（每张独立 context）")
    
    # 加载 cookies
    cookies = []
    if COOKIE_FILE.exists():
        cookies = _json.loads(COOKIE_FILE.read_text())
    
    # 每个图片使用独立的 browser context 上传
    urls_in_order = []
    for idx, (full, alt, abs_path, orig_path) in enumerate(uploads):
        print(f"    [{idx+1}/{len(uploads)}] {orig_path}...", end=" ", flush=True)
        
        ctx = await _browser.new_context(viewport={"width": 1280, "height": 800})
        if cookies:
            await ctx.add_cookies(cookies)
        
        upage = await ctx.new_page()
        try:
            await upage.goto(CSDN_EDITOR, wait_until="domcontentloaded", timeout=15000)

            btn = await upage.wait_for_selector('button.navigation-bar__button:has-text("图片")', timeout=10000)
            await btn.click()

            await upage.wait_for_selector('.modal__inner-1[aria-label="Insert image"]', timeout=5000)

            fi = await upage.wait_for_selector('.modal__inner-1[aria-label="Insert image"] input[type="file"]', timeout=5000)
            await fi.set_input_files(abs_path)

            # 等待图片上传完成（CDN URL 出现在编辑器中）
            try:
                await upage.wait_for_function("""() => {
                    var pre = document.querySelector('pre.editor__inner');
                    if (!pre) return false;
                    var imgs = pre.querySelectorAll('img');
                    for (var i = imgs.length - 1; i >= 0; i--) {
                        if (/(?:img-blog|i-blog)\\.csdnimg\\.cn/.test(imgs[i].src))
                            return true;
                    }
                    return false;
                }""", timeout=15000)
            except Exception:
                await asyncio.sleep(5)
            
            url = await upage.evaluate("""() => {
                var pre = document.querySelector('pre.editor__inner');
                if (!pre) return null;
                var imgs = pre.querySelectorAll('img');
                for (var i = imgs.length - 1; i >= 0; i--) {
                    if (/(?:img-blog|i-blog)\\.csdnimg\\.cn/.test(imgs[i].src))
                        return imgs[i].src;
                }
                return null;
            }""")
            
            if url:
                urls_in_order.append((orig_path, url))
                print(f"✅")
            else:
                print(f"⚠ No CDN URL")
                urls_in_order.append((orig_path, None))
        except Exception as e:
            print(f"❌ {e}")
            urls_in_order.append((orig_path, None))
        finally:
            await upage.close()
            await ctx.close()
        await asyncio.sleep(0.5)
    
    # 替换 body 中的图片路径
    success_count = 0
    for orig_path, url in urls_in_order:
        if url:
            body = body.replace(f'({orig_path})', f'({url})')
            success_count += 1
    
    print(f"    ✅ {success_count}/{len(uploads)} 张图片已替换为 CDN URL")
    
    return body


# ====== MCP Tools ======

@mcp.tool
async def csdn_login() -> str:
    """第一步：获取 CSDN 微信扫码登录二维码。如已登录则直接返回成功。"""
    global _login_page, _logged_in
    await _ensure_browser()

    if _logged_in:
        return "✅ 已登录 CSDN，无需重复扫码。"

    if _login_page:
        try:
            await _login_page.close()
        except Exception as e:
            print(f"[WARN] 关闭登录页: {e}")

    _login_page = await _context.new_page()
    await _login_page.goto(CSDN_LOGIN, wait_until="domcontentloaded", timeout=15000)

    try:
        qr_el = await _login_page.wait_for_selector(".login-code-wechat", timeout=15000)
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
    except Exception as e:
        print(f"[WARN] 读取登录页URL: {e}")
        _login_page = None
        return "⏳ 登录页已关闭，请重新运行 csdn_login"

    if "passport" in current_url:
        await asyncio.sleep(1)
        try:
            current_url = _login_page.url
        except Exception as e:
            print(f"[WARN] 二次读取登录页URL: {e}")

    if "passport" in current_url:
        return f"⏳ 尚未检测到登录回调，当前页面: {current_url}"

    _logged_in = True

    # 持久化 cookies 到文件
    try:
        COOKIE_FILE.parent.mkdir(parents=True, exist_ok=True)
        cookies = await _context.cookies()
        COOKIE_FILE.write_text(json.dumps(cookies, indent=2, ensure_ascii=False))
    except Exception as e:
        print(f"[WARN] 保存cookies到文件: {e}")

    return "✅ CSDN 登录成功！Cookies 已持久化，重启后无需重复扫码。"


@mcp.tool
async def csdn_check_login() -> str:
    """检查当前登录状态"""
    global _logged_in
    await _ensure_browser()

    try:
        check_page = await _context.new_page()
        await check_page.goto(CSDN_EDITOR, wait_until="domcontentloaded", timeout=15000)
        try:
            await check_page.wait_for_selector('input[placeholder*="请输入文章标题"]', timeout=10000)
            _logged_in = True
            await check_page.close()
            return "✅ 已登录 CSDN，可直接发布。"
        except Exception:
            _logged_in = False
            await check_page.close()
            return "❌ 未登录（cookies 已过期），请运行 csdn_login 扫码登录。"
    except Exception as e:
        return f"❌ 检查登录状态失败: {e}"


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
        await page.wait_for_selector('input[placeholder*="请输入文章标题"]', timeout=10000)

        if "login" in page.url or "passport" in page.url:
            _logged_in = False
            COOKIE_FILE.unlink(missing_ok=True)
            return "❌ 登录已过期（cookies 失效），请重新运行 csdn_login。"

        # 2. 填标题（用 JS 赋值，避免 fill() 在中文长标题下失效）
        await page.evaluate("""(t) => {
            var inp = document.querySelector('input[placeholder*="请输入文章标题"]');
            if (inp) {
                inp.value = '';
                inp.value = t;
                inp.dispatchEvent(new Event('input', {bubbles: true}));
                inp.dispatchEvent(new Event('change', {bubbles: true}));
            }
        }""", title)

        # 3. 上传内联图片 + 注入正文
        base_dir = Path(markdown_path).parent if markdown_path else Path.cwd()
        body = await _upload_inline_images(page, body, base_dir)
        
        ok = await page.evaluate("""
        (escaped) => {
            // 优先 CodeMirror（旧版编辑器）
            var cm = document.querySelector('.CodeMirror');
            if(cm && cm.CodeMirror){
                cm.CodeMirror.setValue(escaped);
                cm.CodeMirror.focus();
                cm.dispatchEvent(new Event('input', {bubbles:true}));
                return 'codemirror';
            }
            // 新版 contenteditable 编辑器（insertHTML 保留换行）
            var pre = document.querySelector('pre.editor__inner');
            if(pre && pre.isContentEditable){
                var html = escaped.replace(/\\n/g, '<br>');
                pre.focus();
                document.execCommand('selectAll', false, null);
                document.execCommand('insertHTML', false, html);
                pre.dispatchEvent(new Event('input', {bubbles:true}));
                return 'contenteditable';
            }
            return false;
        }
        """, body)
        
        if not ok:
            # 终极 fallback：键盘输入
            await page.click('.editor')
            await asyncio.sleep(0.5)
            await page.keyboard.insert_text(body)
        else:
            await page.click('.editor')
            await asyncio.sleep(0.5)
            await page.keyboard.type(" ")
            await page.keyboard.press("Backspace")

        if draft:
            try:
                # 保存草稿：优先用工具栏保存按钮，fallback 到底部弹窗按钮
                save_btn = None
                for sel in ['button.button-save', 'button.btn-save']:
                    try:
                        sb = await page.wait_for_selector(sel, timeout=3000)
                        if sb and await sb.is_visible():
                            save_btn = sb
                            break
                    except Exception:
                        continue

                if save_btn:
                    await save_btn.click()
                    # 如果弹出 popover，点「保存草稿」
                    try:
                        draft_btn = page.locator('button:has-text("保存草稿")')
                        if await draft_btn.is_visible(timeout=2000):
                            await draft_btn.click()
                    except Exception:
                        pass
                else:
                    # 终极 fallback：eval 找文本含"保存"的可见按钮
                    save_btn = await page.evaluate_handle('''() => {
                        const btns = document.querySelectorAll('button');
                        for (const b of btns) {
                            if (b.offsetWidth > 0 && b.offsetHeight > 0 &&
                                (b.textContent.includes('保存') || b.textContent.includes('草稿'))) {
                                return b;
                            }
                        }
                        return null;
                    }''')
                    if not save_btn:
                        raise Exception("找不到保存按钮")
                    await save_btn.click()

                # 等待保存完成（URL 出现 articleId 或等待固定时间）
                try:
                    await page.wait_for_function(
                        'window.location.href.includes("articleId")',
                        timeout=10000
                    )
                except Exception:
                    await asyncio.sleep(5)

                m = re.search(r'articleId=(\d+)', page.url)
                aid = m.group(1) if m else None
                msg = f"✅ 「{title}」已保存到草稿箱"
                if aid: msg += f"\n🔗 https://editor.csdn.net/md/?articleId={aid}"
            except Exception as e:
                await asyncio.sleep(3)
                msg = f"✅ 「{title}」内容已注入编辑器！请手动点击保存或到 CSDN 后台查看。\n(自动保存失败: {e})"
            return json.dumps({"success": True, "title": title, "message": msg}, ensure_ascii=False)

        # ====== 发布模式 ======

        # 打开发布弹窗
        pb = await page.wait_for_selector(f'xpath={SELECTORS["publish_btn"]}', timeout=10000)
        await pb.click()
        # 等待发布弹窗打开
        try:
            await page.wait_for_selector(f'xpath={SELECTORS["tag_add"]}', timeout=10000)
        except Exception:
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
                print(f"[WARN] 封面上传: {e}")

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
            except Exception as e:
                print(f"[WARN] 添加标签失败: {e}")

        # 分类专栏（直接勾选可见checkbox）
        cat = category or fm.get("category", "")
        if cat:
            try:
                cat_xpath = SELECTORS["category_cb"].replace("{cat}", cat)
                cat_cb = await page.wait_for_selector(f"xpath={cat_xpath}", timeout=5000)
                await cat_cb.click()
                await asyncio.sleep(0.5)
            except Exception as e:
                print(f"[WARN] 选择分类专栏失败: {e}")

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
        except Exception as e:
            print(f"[WARN] 捕获策略1 URL失败: {e}")
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
            except Exception as e:
                print(f"[WARN] 捕获策略3 URL失败: {e}")

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
