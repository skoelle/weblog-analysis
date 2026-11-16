# Copyright (c) 2026 Stefan Koelle (https://stefankoelle.de)
# Licensed under the MIT License. See LICENSE file in project root for details.
from app.classify import (
    CATEGORY_AI_BOT,
    CATEGORY_BROWSER,
    CATEGORY_EMPTY,
    CATEGORY_MONITORING,
    CATEGORY_SCANNER,
    CATEGORY_SCRAPER,
    CATEGORY_SEARCH_BOT,
    classify,
    is_scanner_path,
)

CHROME = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/148.0.0.0 Safari/537.36"
)


def test_browser():
    cls = classify(CHROME, "/shop/")
    assert cls.category == CATEGORY_BROWSER


def test_empty_ua():
    assert classify("-", "/").category == CATEGORY_EMPTY
    assert classify("", "/").category == CATEGORY_EMPTY


def test_search_bots():
    assert (
        classify(
            "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)", "/"
        ).category
        == CATEGORY_SEARCH_BOT
    )
    assert (
        classify("Mozilla/5.0 (compatible; YandexBot/3.0; +http://yandex.com/bots)", "/").category
        == CATEGORY_SEARCH_BOT
    )


def test_ai_bots():
    assert (
        classify(
            "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko; compatible; ClaudeBot/1.0; +claudebot@anthropic.com)",
            "/",
        ).category
        == CATEGORY_AI_BOT
    )
    assert classify("CCBot/2.0 (https://commoncrawl.org/faq/)", "/").category == CATEGORY_AI_BOT
    assert (
        classify(
            "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko); compatible; GPTBot/1.4; +https://openai.com/gptbot",
            "/",
        ).category
        == CATEGORY_AI_BOT
    )
    assert (
        classify(
            "Mozilla/5.0 (compatible; DeepSeekBot/1.0; +https://www.deepseek.com/)", "/"
        ).category
        == CATEGORY_AI_BOT
    )


def test_monitoring():
    ua = "Mozilla/5.0+(compatible; UptimeRobot/2.0; http://www.uptimerobot.com/)"
    assert classify(ua, "/").category == CATEGORY_MONITORING


def test_scraper():
    assert classify("curl/8.7.1", "/").category == CATEGORY_SCRAPER
    assert classify("python-requests/2.31.0", "/").category == CATEGORY_SCRAPER


def test_scanner_path_detection():
    assert is_scanner_path("/.env")
    assert is_scanner_path("/wp-login.php")
    assert is_scanner_path("/.git/config")
    assert not is_scanner_path("/shop/")


def test_browser_ua_on_scanner_path_is_scanner():
    cls = classify(CHROME, "/.env")
    assert cls.category == CATEGORY_SCANNER
    assert cls.is_scanner_path


def test_checkout_paths_are_not_scanner():
    assert classify(CHROME, "/code/").category == CATEGORY_BROWSER
    assert classify(CHROME, "/admin/").category == CATEGORY_BROWSER
    assert classify(CHROME, "/wp-admin/").category == CATEGORY_SCANNER
