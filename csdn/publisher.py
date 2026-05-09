"""CSDN 文章发布核心引擎 — 基于 Playwright

核心思路：
1. Cookie 认证（auth.py）
2. 导航到 editor.csdn.net/md/
3. 填写标题
4. 通过 page.evaluate() JS 注入 CodeMirror 内容（避免 WSL headless 剪贴板问题）
5. 点击"发布文章"→ 弹窗填元数据 → 发布
"""

import json
import time
import sys
from pathlib import Path
from playwright.sync_api import Page, Browser, BrowserContext, sync_playwright

from csdn.auth import create_authenticated_context, save_cookies
from csdn.config import CSDN_HOME, CSDN_EDITOR_URL, SELECTORS


def parse_front_matter(markdown_text: str) -> dict:
    """解析 Markdown frontmatter（YAML --- 块），返回 dict"""
    lines = markdown_text.strip().split("\n")
    if not lines or lines[0].strip() != "---":
        return {}

    end_idx = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            end_idx = i
            break
    if end_idx is None:
        return {}

    result = {}
    for line in lines[1:end_idx]:
        if ":" in line:
            key, _, value = line.partition(":")
            result[key.strip()] = value.strip().strip('"').strip("'")
    return result


def _inject_editor_content(page: Page, content: str) -> bool:
    """
    将 Markdown 内容注入 CSDN CodeMirror 编辑器。
    策略：尝试多种 JS 注入方式，找到可用的 CodeMirror 实例。
    Returns True on success, False on failure.
    """
    # 转义 content 中的特殊字符以便嵌入 JS 字符串
    escaped = json.dumps(content)  # 自动处理 \n, \t, ", ' 等

    strategies = [
        # 策略 1: 标准 CodeMirror selector
        f"""
        (function() {{
            var cm = document.querySelector('.CodeMirror');
            if (cm && cm.CodeMirror) {{
                cm.CodeMirror.setValue({escaped});
                cm.CodeMirror.refresh();
                return 'ok-cm';
            }}
            return 'no-cm';
        }})();
        """,
        # 策略 2: 查找所有 CodeMirror 实例
        f"""
        (function() {{
            var editors = document.querySelectorAll('.CodeMirror');
            for (var i = 0; i < editors.length; i++) {{
                if (editors[i].CodeMirror) {{
                    editors[i].CodeMirror.setValue({escaped});
                    editors[i].CodeMirror.refresh();
                    return 'ok-multi-' + i;
                }}
            }}
            return 'no-instance';
        }})();
        """,
        # 策略 3: 通过 cledit-section 找到父级 CodeMirror
        f"""
        (function() {{
            var section = document.querySelector('.cledit-section');
            if (section) {{
                var cm = section.closest('.editor').querySelector('.CodeMirror');
                if (cm && cm.CodeMirror) {{
                    cm.CodeMirror.setValue({escaped});
                    cm.CodeMirror.refresh();
                    return 'ok-cledit';
                }}
            }}
            return 'no-cledit';
        }})();
        """,
    ]

    for i, script in enumerate(strategies):
        try:
            result = page.evaluate(script)
            if result and str(result).startswith("ok"):
                print(f"[Publisher] Content injected via strategy {i+1}: {result}")
                return True
        except Exception as e:
            print(f"[Publisher] Strategy {i+1} failed: {e}")
            continue

    return False


def _fill_content_fallback(page: Page, content: str):
    """
    当 JS 注入失败时，尝试 fallback 方案：
    1. 点击编辑器区域
    2. 使用 Playwright 的 keyboard.insert_text()（逐字输入，仅适合短内容）
    3. 如果内容 > 5000 字符，放弃 insert_text 改用 clipboard
    """
    editor_el = page.wait_for_selector(
        f'xpath={SELECTORS["editor_area"]}', timeout=10000
    )
    editor_el.click()
    time.sleep(1)

    if len(content) < 5000:
        # 短内容：逐字输入
        page.keyboard.insert_text(content)
    else:
        # 长内容：尝试 Ctrl+V（需要系统剪贴板已设置）
        try:
            import pyperclip
            pyperclip.copy(content)
            cmd_ctrl = "Meta" if sys.platform == "darwin" else "Control"
            page.keyboard.press(f"{cmd_ctrl}+v")
        except Exception:
            # 最终 fallback：分批 insert_text
            chunk_size = 2000
            for i in range(0, len(content), chunk_size):
                page.keyboard.insert_text(content[i:i + chunk_size])
                time.sleep(0.5)
    time.sleep(2)


def publish_article(
    markdown_path: str | None = None,
    markdown_content: str | None = None,
    tags: list[str] | None = None,
    category: str | None = None,
    auto_publish: bool = True,
) -> str:
    """
    发布文章到 CSDN。

    Args:
        markdown_path: Markdown 文件绝对路径（与 content 二选一）
        markdown_content: Markdown 文本（与 path 二选一）
        tags: 文章标签列表；为 None 则从 frontmatter 解析
        category: 分类专栏名（需已在 CSDN 创建）
        auto_publish: True=直接发布, False=仅保存草稿

    Returns:
        {"success": true/false, "title": ..., "url": ..., "message": ...} JSON
    """
    # 读取内容
    if markdown_path:
        content = Path(markdown_path).read_text(encoding="utf-8")
    elif markdown_content:
        content = markdown_content
    else:
        return json.dumps(
            {"success": False, "error": "必须提供 markdown_path 或 markdown_content"},
            ensure_ascii=False
        )

    front_matter = parse_front_matter(content)
    title = front_matter.get("title", "未命名文章")

    # 去掉 frontmatter 得到纯正文
    body = content.split("---", 2)[-1].strip() if front_matter else content.strip()

    with sync_playwright() as p:
        browser, context = create_authenticated_context(p)
        page = context.new_page()

        try:
            # 1. 先访问首页建立会话，再进入编辑器
            page.goto(CSDN_HOME, wait_until="domcontentloaded", timeout=20000)
            time.sleep(2)
            
            # 尝试通过首页「写文章」链接跳转
            reached = False
            for sel in ['a[href*="editor.csdn.net"]', 'a[href*="mp.csdn.net"]',
                        'a:has-text("写文章")', 'a:has-text("创作")']:
                try:
                    btn = page.query_selector(sel)
                    if btn:
                        btn.click()
                        time.sleep(3)
                        if 'editor.csdn.net' in page.url or 'mp.csdn.net' in page.url:
                            reached = True
                            break
                except Exception:
                    pass
            
            if not reached:
                page.goto(CSDN_EDITOR_URL, wait_until="domcontentloaded", timeout=20000)
            
            time.sleep(2)

            # 2. 检查登录状态
            if "login" in page.url.lower() or "passport" in page.url.lower():
                browser.close()
                return json.dumps(
                    {"success": False, "error": "CSDN cookies 已过期，请运行 csdn_login 重新登录"},
                    ensure_ascii=False
                )

            # 3. 填写标题
            try:
                title_el = page.wait_for_selector(
                    f'xpath={SELECTORS["title_input"]}', timeout=10000
                )
                title_el.click()
                title_el.fill(title)
            except Exception as e:
                browser.close()
                return json.dumps(
                    {"success": False, "error": f"无法定位标题输入框: {e}"},
                    ensure_ascii=False
                )
            time.sleep(1)

            # 4. 注入正文内容
            injected = _inject_editor_content(page, body)
            if not injected:
                print("[Publisher] JS injection failed, trying fallback...")
                _fill_content_fallback(page, body)
            time.sleep(2)

            # 5. 点击「发布文章」
            publish_btn = page.wait_for_selector(
                f'xpath={SELECTORS["publish_btn"]}', timeout=10000
            )
            publish_btn.click()
            time.sleep(3)

            # 6. 填写元数据弹窗
            # 6a. 标签
            tag_list = tags or _parse_tags(front_matter)
            if tag_list:
                try:
                    _add_tags(page, tag_list)
                except Exception as e:
                    print(f"[Publisher] 标签添加失败（继续）: {e}")

            # 6b. 封面
            cover = front_matter.get("cover") or front_matter.get("cover_image")
            if cover and Path(cover).is_file():
                try:
                    _set_cover(page, cover)
                except Exception as e:
                    print(f"[Publisher] 封面设置失败（继续）: {e}")

            # 6c. 摘要
            summary = front_matter.get("description") or front_matter.get("summary")
            if summary:
                try:
                    _set_summary(page, summary)
                except Exception as e:
                    print(f"[Publisher] 摘要设置失败（继续）: {e}")

            # 6d. 分类专栏
            if category:
                try:
                    _set_category(page, category)
                except Exception as e:
                    print(f"[Publisher] 分类专栏设置失败（继续）: {e}")

            # 7. 发布
            article_url = None
            if auto_publish:
                final_btn = page.wait_for_selector(
                    f'xpath={SELECTORS["final_publish_btn"]}', timeout=10000
                )
                final_btn.click()
                time.sleep(3)
                article_url = _try_get_article_url(page, context)

            # 8. 保存更新后的 cookies
            save_cookies(context)
            browser.close()

            return json.dumps({
                "success": True,
                "action": "published" if auto_publish else "draft",
                "title": title,
                "url": article_url,
                "message": (
                    f"✅ 文章「{title}」已{'发布' if auto_publish else '保存草稿'}！"
                    + (f"\n🔗 {article_url}" if article_url else "\n请登录 CSDN 后台查看文章。")
                ),
            }, ensure_ascii=False)

        except Exception as e:
            browser.close()
            return json.dumps(
                {"success": False, "error": f"发布失败: {str(e)}"},
                ensure_ascii=False
            )


def _parse_tags(front_matter: dict) -> list[str]:
    """从 frontmatter 解析标签列表"""
    raw = front_matter.get("tags", "")
    if isinstance(raw, list):
        return [str(t).strip() for t in raw if str(t).strip()]
    return [t.strip() for t in raw.replace("，", ",").split(",") if t.strip()]


def _add_tags(page: Page, tags: list[str]):
    """在弹窗中添加文章标签"""
    add_btn = page.wait_for_selector(
        f'xpath={SELECTORS["tag_add_btn"]}', timeout=5000
    )
    add_btn.click()
    time.sleep(1)

    tag_input = page.wait_for_selector(
        f'xpath={SELECTORS["tag_search_input"]}', timeout=5000
    )
    for tag in tags[:5]:  # CSDN 最多 5 个标签
        tag_input.fill(tag)
        time.sleep(1)
        tag_input.press("Enter")
        time.sleep(0.5)

    close_btn = page.wait_for_selector(
        f'xpath={SELECTORS["tag_close_btn"]}', timeout=5000
    )
    close_btn.click()
    time.sleep(1)


def _set_cover(page: Page, cover_path: str):
    """设置文章封面（本地文件上传）"""
    file_input = page.wait_for_selector(
        f'xpath={SELECTORS["cover_input"]}', timeout=5000
    )
    file_input.set_input_files(cover_path)
    time.sleep(2)


def _set_summary(page: Page, summary: str):
    """设置文章摘要"""
    summary_input = page.wait_for_selector(
        f'xpath={SELECTORS["summary_input"]}', timeout=5000
    )
    summary_input.fill(summary[:256])  # CSDN 摘要长度限制
    time.sleep(1)


def _set_category(page: Page, category: str):
    """选择分类专栏"""
    new_btn = page.wait_for_selector(
        f'xpath={SELECTORS["category_new_btn"]}', timeout=5000
    )
    new_btn.click()
    time.sleep(1)

    # 勾选对应专栏 checkbox
    checkbox = page.wait_for_selector(
        f'xpath=//input[@type="checkbox" and @value="{category}"]/..', timeout=5000
    )
    checkbox.click()
    time.sleep(1)

    close_btn = page.wait_for_selector(
        f'xpath={SELECTORS["category_close_btn"]}', timeout=5000
    )
    close_btn.click()
    time.sleep(1)


def _try_get_article_url(page: Page, context: BrowserContext) -> str | None:
    """尝试从新标签页或当前页面获取已发布文章的 URL"""
    try:
        time.sleep(2)
        # 检查所有打开的页面
        for pg in context.pages:
            if "blog.csdn.net" in pg.url and "/article/details/" in pg.url:
                return pg.url
        # 检查当前页
        if "blog.csdn.net" in page.url:
            return page.url
    except Exception:
        pass
    return None
