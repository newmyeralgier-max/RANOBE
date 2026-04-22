---
name: scripter-bot-maker
description: Advanced rules for creating web scrapers, automation bots (e.g., periodic Selenium/Playwright bots), and Telegram utilities.
---

# Bot & Automation Scripter Rules

## When to use this skill
- When working on scripts like `gobec_periodic_bot.py`.
- Whenever interacting with Chromium/Selenium/Playwright or web scraping.
- Building bots that communicate via Telegram APIs or parsing JSON exports.

## Core Directives for Reliability
As per the user's defined architectural directives, any deterministic script or bot must be built to **self-anneal** and survive errors.

1. **Robust Error Handling**: 
   - Never write a web-scraper action (click, wait, parse) without wrapping it in a generous Try-Catch with explicit timeout limits.
   - Example: Instead of naive `driver.find_element()`, use explicit `WebDriverWait(driver, 10).until(EC.presence_of_element_located(...))`
2. **Proxy Management**:
   - Web bots must support proxy rotation. Always include logic to handle proxy connection timeouts gracefully and retry with a new/different proxy or direct fallback if configured.
3. **Headless Toggles**:
   - Provide an environmental variable or constant (e.g., `HEADLESS_MODE`) to quickly toggle between headless (for cron jobs) and headed (for debugging locally).
4. **API Limits**:
   - Implement exponential backoff when hitting Telegram or third-party API rate limits (HTTP 429).
   
## Output & Deliverables
- Log everything critical: timestamps, IDs created, failures, and latency.
- Temporary parsing outputs should go to `.tmp/` or temporary structures that don't pollute the repository. Do not commit `.tmp/` files.
