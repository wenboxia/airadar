"""README 配图：横幅 + 线上网站截图 + 一张真实的每周审批单截图，存到 docs/images/。

只在开发机用，依赖 playwright（不进 requirements.txt，pipeline 不 import）：
  python3 -m venv /tmp/pw && /tmp/pw/bin/pip install playwright
  /tmp/pw/bin/python tools/readme_shots.py
用系统自带的 Chrome（channel="chrome"），不用另外下载浏览器。

网站的视图是前端状态切换的，没有 URL，所以靠点导航按钮切换——不为截图往网站里加临时代码。
截的是线上站点而不是本地构建：README 要展示的是面试官点进去真正看到的东西。
"""
import io
import os
import sys

from PIL import Image
from playwright.sync_api import sync_playwright

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "docs", "images")
SITE = "https://wenboxia.github.io/airadar/"
ISSUE = "https://github.com/wenboxia/airadar/issues/4"
MAX_W = 1600
# 截图时关掉动画，否则扫描光带、入场渐显会被截到一半
NO_MOTION = "*,*::before,*::after{animation:none!important;transition:none!important}"


def save(png: bytes, name: str, palette=True):
    """palette=True 转成 256 色：截图体积减半、肉眼看不出差别；横幅的小色点会被压灰，所以横幅不转"""
    im = Image.open(io.BytesIO(png)).convert("RGB")
    if im.width > MAX_W:
        im = im.resize((MAX_W, round(im.height * MAX_W / im.width)), Image.LANCZOS)
    if palette:
        im = im.quantize(256, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.FLOYDSTEINBERG)
    path = os.path.join(OUT, name)
    im.save(path, optimize=True)
    print(f"  {name}: {im.width}×{im.height}, {os.path.getsize(path) // 1024} KB")


def shoot_site(browser):
    page = browser.new_page(viewport={"width": 1280, "height": 800}, device_scale_factor=2)
    page.goto(SITE, wait_until="networkidle")
    page.add_style_tag(content=NO_MOTION)
    page.wait_for_selector("text=本周精选")
    page.evaluate("document.fonts.ready")
    page.wait_for_timeout(800)
    save(page.screenshot(), "site-week.png")

    page.locator("nav button", has_text="机制").first.click()
    page.wait_for_timeout(600)
    save(page.screenshot(full_page=False), "site-mechanism.png")
    page.close()


def shoot_issue(browser):
    # 浅色：深色模式下已勾选的禁用复选框只是一个灰方块，看不出"勾了"
    ctx = browser.new_context(viewport={"width": 1100, "height": 900}, device_scale_factor=2,
                              color_scheme="light", locale="zh-CN")
    page = ctx.new_page()
    page.goto(ISSUE, wait_until="networkidle")
    page.add_style_tag(content=NO_MOTION)
    # 只截抽查和捞回两块：整张单子太长；抽查那块"勾 = 撤下"是这套审批最不一样的地方
    top = page.locator("h3", has_text="二 · 自动发布抽查").first
    last = page.locator("h3", has_text="三 · 旧池捞回").first
    top.wait_for()
    box = page.evaluate("""([a, b]) => {
        const h1 = a.getBoundingClientRect();
        let el = b.nextElementSibling, bottom = b.getBoundingClientRect().bottom;
        while (el && el.tagName !== 'HR' && el.tagName !== 'H3') {
            bottom = Math.max(bottom, el.getBoundingClientRect().bottom); el = el.nextElementSibling;
        }
        const body = a.closest('.markdown-body') || a.parentElement;
        const r = body.getBoundingClientRect();
        return {x: r.left - 16, y: h1.top + window.scrollY - 16, w: r.width + 32,
                h: bottom - h1.top + 32};
    }""", [top.element_handle(), last.element_handle()])
    png = page.screenshot(full_page=True, clip={"x": box["x"], "y": box["y"],
                                                "width": box["w"], "height": box["h"]})
    save(png, "issue-approval.png")
    ctx.close()


def shoot_banner(browser):
    page = browser.new_page(viewport={"width": 1440, "height": 400}, device_scale_factor=2)
    page.goto("file://" + os.path.join(ROOT, "tools", "readme_banner.html"), wait_until="networkidle")
    page.evaluate("document.fonts.ready")
    page.wait_for_timeout(500)
    save(page.locator(".card").screenshot(), "banner.png", palette=False)
    page.close()


def main():
    os.makedirs(OUT, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome")
        which = sys.argv[1:] or ["banner", "site", "issue"]
        if "banner" in which:
            shoot_banner(browser)
        if "site" in which:
            shoot_site(browser)
        if "issue" in which:
            shoot_issue(browser)
        browser.close()


if __name__ == "__main__":
    main()
