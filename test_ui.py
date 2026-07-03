import asyncio
from playwright.async_api import async_playwright

async def run():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.goto("http://localhost:8501")

        # Focus element to check outline
        await page.locator("#railSemantic").focus()
        await page.screenshot(path="focus_railSemantic.png")

        await browser.close()

asyncio.run(run())
