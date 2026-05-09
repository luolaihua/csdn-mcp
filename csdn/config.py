"""CSDN MCP Server — 常量配置"""
from pathlib import Path

# URLs
CSDN_EDITOR_URL = "https://editor.csdn.net/md/"
CSDN_HOME = "https://www.csdn.net/"

# Cookie 持久化路径
COOKIE_FILE = Path.home() / ".hermes" / "csdn_cookies.json"

# CSDN 编辑器的 DOM selectors（基于 editor.csdn.net/md/ 实际结构）
SELECTORS = {
    # 标题输入框
    "title_input": '//div[contains(@class,"article-bar")]//input[contains(@placeholder,"请输入文章标题")]',
    # 编辑器内容区域
    "editor_area": '//div[@class="editor"]//div[@class="cledit-section"]',
    # 发布文章主按钮
    "publish_btn": '//button[contains(@class,"btn-publish") and contains(text(),"发布文章")]',
    # === 弹窗内元素 ===
    # 添加标签按钮
    "tag_add_btn": '//div[@class="mark_selection"]//button[@class="tag__btn-tag" and contains(text(),"添加文章标签")]',
    # 标签搜索输入框
    "tag_search_input": '//div[contains(@class,"mark_selection_box")]//input[contains(@placeholder,"请输入文字搜索")]',
    # 标签弹窗关闭
    "tag_close_btn": '//div[contains(@class,"mark_selection_box")]//button[@title="关闭"]',
    # 封面上传 input[file]
    "cover_input": "//input[@class='el-upload__input' and @type='file']",
    # 摘要 textarea
    "summary_input": '//div[@class="desc-box"]//textarea[contains(@placeholder,"摘要")]',
    # 新建分类专栏按钮
    "category_new_btn": '//div[@id="tagList"]//button[@class="tag__btn-tag" and contains(text(),"新建分类专栏")]',
    # 分类专栏弹窗关闭
    "category_close_btn": '//div[@class="tag__options-content"]//button[@class="modal__close-button button" and @title="关闭"]',
    # 最终发布按钮（弹窗中）
    "final_publish_btn": '//div[contains(@class,"modal__button-bar")]//button[contains(text(),"发布文章")]',
}
