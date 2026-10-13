"""Browser QA for the local evidence gallery; does not exercise the product backend."""
from __future__ import annotations

import json
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "design-previews/2026-10-03-agent-polish"


def main(out_dir=None, minimum_frames=20, minimum_images=12):
    global OUT
    if out_dir is not None:
        OUT = Path(out_dir)
    errors = []
    with sync_playwright() as runtime:
        browser = runtime.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 1050})
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto((OUT / "index.html").as_uri())
        page.wait_for_load_state("networkidle")
        assert any(label in page.locator("#terminalFrame").inner_text() for label in ("格致", "工作台", "科研模型", "命令选择"))
        count = page.locator("#framePicker option").count()
        assert count >= minimum_frames
        page.locator("#framePicker").select_option("1")
        assert page.locator("#timeline").input_value() == "1"
        page.locator("#play").click()
        page.wait_for_function("Number(document.querySelector('#timeline').value) > 1")
        page.locator("#play").click()
        page.locator("#framePicker").select_option("0")
        page.screenshot(path=str(OUT / "preview-terminal.png"), full_page=True)
        page.locator("#desktopTab").click()
        page.wait_for_function("document.querySelector('#nativeImage').complete && document.querySelector('#nativeImage').naturalWidth > 0")
        native_images = page.locator("#imagePicker option").count()
        for index in range(native_images):
            page.locator("#imagePicker").select_option(str(index))
            page.locator("#nativeImage").evaluate("image => image.decode()")
        page.locator("#imagePicker").select_option("0")
        page.locator("#nativeImage").evaluate("image => image.decode()")
        page.screenshot(path=str(OUT / "preview-desktop.png"), full_page=True)
        assert native_images >= minimum_images
        page.locator("#terminalTab").click()
        page.set_viewport_size({"width": 390, "height": 844})
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        page.screenshot(path=str(OUT / "preview-mobile.png"), full_page=True)
        assert not errors, errors
        browser.close()
    report = {"gallery_playwright": "passed", "frames": count,
              "native_images": native_images, "widths": [1440, 390],
              "page_errors": errors, "native_pty": False}
    (OUT / "gallery-verification.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report))


if __name__ == "__main__":
    main()
