# Copyright (c) 2026 Stefan Koelle (https://stefankoelle.de)
# Licensed under the MIT License. See LICENSE file in project root for details.
"""Classify a request as human, crawler, AI bot, monitoring, scraper or scanner.

The provider masks client IPs to a /24 (IPv4) or /48 (IPv6), so a "visitor" can
only ever mean "a network", never a real person.  This module focuses on
telling automated traffic apart from requests that look like a real browser.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Categories (ordered roughly from "human" to "hostile").
CATEGORY_BROWSER = "browser"
CATEGORY_SEARCH_BOT = "search_bot"
CATEGORY_AI_BOT = "ai_bot"
CATEGORY_MONITORING = "monitoring"
CATEGORY_SCRAPER = "scraper"
CATEGORY_SCANNER = "scanner"
CATEGORY_EMPTY = "empty"
CATEGORY_OTHER = "other"

CATEGORIES = [
    CATEGORY_BROWSER,
    CATEGORY_SEARCH_BOT,
    CATEGORY_AI_BOT,
    CATEGORY_MONITORING,
    CATEGORY_SCRAPER,
    CATEGORY_SCANNER,
    CATEGORY_EMPTY,
    CATEGORY_OTHER,
]

# Named bots: (needle in lowercase UA) -> (category, display name).  First match
# wins, so put more specific needles before generic ones.
BOT_RULES: list[tuple[str, str, str]] = [
    # AI crawlers / assistants
    ("claudebot", CATEGORY_AI_BOT, "ClaudeBot"),
    ("claude-user", CATEGORY_AI_BOT, "Claude-User"),
    ("claude-searchbot", CATEGORY_AI_BOT, "Claude-SearchBot"),
    ("anthropic", CATEGORY_AI_BOT, "Anthropic"),
    ("gptbot", CATEGORY_AI_BOT, "GPTBot"),
    ("chatgpt-user", CATEGORY_AI_BOT, "ChatGPT-User"),
    ("oai-searchbot", CATEGORY_AI_BOT, "OAI-SearchBot"),
    ("oai-adsbot", CATEGORY_AI_BOT, "OAI-AdsBot"),
    ("openai", CATEGORY_AI_BOT, "OpenAI"),
    ("perplexity", CATEGORY_AI_BOT, "PerplexityBot"),
    ("deepseek", CATEGORY_AI_BOT, "DeepSeekBot"),
    ("qwen", CATEGORY_AI_BOT, "QwenBot"),
    ("kimi", CATEGORY_AI_BOT, "KimiBot"),
    ("moonshot", CATEGORY_AI_BOT, "MoonshotBot"),
    ("grok", CATEGORY_AI_BOT, "GrokBot"),
    ("hunyuan", CATEGORY_AI_BOT, "Hunyuan"),
    ("chatglm", CATEGORY_AI_BOT, "ChatGLM"),
    ("cohere-ai", CATEGORY_AI_BOT, "cohere-ai"),
    ("ccbot", CATEGORY_AI_BOT, "CCBot"),
    ("bytespider", CATEGORY_AI_BOT, "Bytespider"),
    ("youbot", CATEGORY_AI_BOT, "YouBot"),
    ("duckassistbot", CATEGORY_AI_BOT, "DuckAssistBot"),
    ("google-extended", CATEGORY_AI_BOT, "Google-Extended"),
    ("meta-externalagent", CATEGORY_AI_BOT, "Meta-ExternalAgent"),
    ("meta-externalfetcher", CATEGORY_AI_BOT, "Meta-ExternalFetcher"),
    ("meta-webindexer", CATEGORY_AI_BOT, "Meta-WebIndexer"),
    ("amazonbot", CATEGORY_AI_BOT, "Amazonbot"),
    ("copyleaks", CATEGORY_AI_BOT, "CopyleaksBot"),
    ("keenable", CATEGORY_AI_BOT, "Keenable"),
    # Search engine crawlers
    ("googlebot", CATEGORY_SEARCH_BOT, "Googlebot"),
    ("adsbot-google", CATEGORY_SEARCH_BOT, "AdsBot-Google"),
    ("feedfetcher-google", CATEGORY_SEARCH_BOT, "FeedFetcher-Google"),
    ("bingbot", CATEGORY_SEARCH_BOT, "Bingbot"),
    ("yandex", CATEGORY_SEARCH_BOT, "YandexBot"),
    ("baiduspider", CATEGORY_SEARCH_BOT, "Baiduspider"),
    ("petalbot", CATEGORY_SEARCH_BOT, "PetalBot"),
    ("applebot", CATEGORY_SEARCH_BOT, "Applebot"),
    ("duckduckbot", CATEGORY_SEARCH_BOT, "DuckDuckBot"),
    ("bravebot", CATEGORY_SEARCH_BOT, "Bravebot"),
    ("qwant", CATEGORY_SEARCH_BOT, "Qwant"),
    ("seznam", CATEGORY_SEARCH_BOT, "SeznamBot"),
    ("sogou", CATEGORY_SEARCH_BOT, "Sogou"),
    ("exabot", CATEGORY_SEARCH_BOT, "Exabot"),
    ("ia_archiver", CATEGORY_SEARCH_BOT, "IA Archiver"),
    ("linkedinbot", CATEGORY_SEARCH_BOT, "LinkedInBot"),
    ("isbsearchbot", CATEGORY_SEARCH_BOT, "IsonSearchBot"),
    # SEO / audit / monitoring
    ("uptimerobot", CATEGORY_MONITORING, "UptimeRobot"),
    ("uptime", CATEGORY_MONITORING, "Uptime"),
    ("pingdom", CATEGORY_MONITORING, "Pingdom"),
    ("gtmetrix", CATEGORY_MONITORING, "GTmetrix"),
    ("datadog", CATEGORY_MONITORING, "Datadog"),
    ("tlm-audit", CATEGORY_MONITORING, "TLM-Audit"),
    ("sitelock", CATEGORY_MONITORING, "SiteLock"),
    ("censys", CATEGORY_MONITORING, "Censys"),
    ("ahrefs", CATEGORY_SCRAPER, "AhrefsBot"),
    ("semrush", CATEGORY_SCRAPER, "SemrushBot"),
    ("mj12", CATEGORY_SCRAPER, "MJ12Bot"),
    ("dotbot", CATEGORY_SCRAPER, "DotBot"),
    ("serpstat", CATEGORY_SCRAPER, "SerpstatBot"),
    ("screaming", CATEGORY_SCRAPER, "Screaming Frog"),
    # Generic crawlers / scrapers
    ("spider", CATEGORY_SCRAPER, "Crawler"),
    ("crawl", CATEGORY_SCRAPER, "Crawler"),
    ("scrap", CATEGORY_SCRAPER, "Scraper"),
    ("curl", CATEGORY_SCRAPER, "curl"),
    ("wget", CATEGORY_SCRAPER, "wget"),
    ("python-requests", CATEGORY_SCRAPER, "python-requests"),
    ("python-urllib", CATEGORY_SCRAPER, "python-urllib"),
    ("aiohttp", CATEGORY_SCRAPER, "aiohttp"),
    ("httpx", CATEGORY_SCRAPER, "httpx"),
    ("go-http-client", CATEGORY_SCRAPER, "Go-http-client"),
    ("got (", CATEGORY_SCRAPER, "got"),
    ("okhttp", CATEGORY_SCRAPER, "OkHttp"),
    ("apache-httpclient", CATEGORY_SCRAPER, "Apache-HttpClient"),
    ("java/", CATEGORY_SCRAPER, "Java"),
    ("libwww", CATEGORY_SCRAPER, "libwww"),
    ("lwp-", CATEGORY_SCRAPER, "libwww-perl"),
    ("headless", CATEGORY_SCRAPER, "Headless"),
    ("phantomjs", CATEGORY_SCRAPER, "PhantomJS"),
    ("faraday", CATEGORY_SCRAPER, "Faraday"),
    ("feedfetcher", CATEGORY_SCRAPER, "FeedFetcher"),
]

BROWSER_HINTS = (
    "mozilla/5.0",
    "mozilla/4.0",
    "opera",
    "applewebkit",
    "gecko",
    "firefox",
    "chrome",
    "safari",
    "edg/",
    "trident",
    "msie",
)

# Paths typical for vulnerability scanners / bot probes.  Mozilla-looking UAs
# that hit these are reclassified as scanners.
SCANNER_PATTERNS = re.compile(
    r"(?:"
    r"/\.env|/\.git|/\.svn|/\.hg|/\.aws|/\.ssh|/\.DS_Store|/\.idea|/\.vscode|"
    r"/wp-login|/wp-admin|/xmlrpc\.php|/phpmyadmin|/pma|/myadmin|/adminfuns|"
    r"/admin\.php|/administrator|/credentials|/docker-compose|/config\.json|"
    r"/config\.php|/info\.php|/autoload_classmap|/vendor/|/\.well-known/|"
    r"/backup|/\.bak|/\.sql|/db\.php|/chosen\.php|/alfa\.php|/goods\.php|"
    r"/ioxi-o\.php|/this_is_a_new_hello_world|/1\.php|/222\.php|/aa\.php|"
    r"/as\.php|/file\.php|/simple\.php|/about\.php|/wp-content/.*\.php|"
    r"/\.github|/\.terraform|/\.docker|/\.kube|/\.npmrc|/\.ftpconfig|"
    r"/server-status|/actuator|/cgi-bin/|/shell\.php|/eval-stdin|"
    r"/owa/|/ecp/|/autodiscover|/remote/login|/cgi-sys|/boaform"
    r")",
    re.IGNORECASE,
)

WINDOWS_SCANNER_UA = re.compile(r"mozilla/5\.0 \(windows nt", re.IGNORECASE)


@dataclass(frozen=True)
class Classification:
    category: str
    bot_name: str | None
    is_scanner_path: bool


def is_scanner_path(path: str) -> bool:
    return bool(SCANNER_PATTERNS.search(path))


def classify(ua: str, path: str) -> Classification:
    raw = (ua or "").strip()
    lower = raw.lower()
    scanner_path = is_scanner_path(path)

    if not raw or raw == "-":
        return Classification(CATEGORY_EMPTY, None, scanner_path)

    for needle, category, name in BOT_RULES:
        if needle in lower:
            return Classification(category, name, scanner_path)

    looks_like_browser = any(hint in lower for hint in BROWSER_HINTS)
    if looks_like_browser:
        if scanner_path:
            return Classification(CATEGORY_SCANNER, None, True)
        return Classification(CATEGORY_BROWSER, None, False)

    # Unknown UA: treat as scanner if it only probes known-bad paths, otherwise
    # leave it as "other" so it stays visible instead of being hidden.
    if scanner_path:
        return Classification(CATEGORY_SCANNER, None, True)
    return Classification(CATEGORY_OTHER, None, False)


HUMAN_CATEGORIES = frozenset({CATEGORY_BROWSER})
