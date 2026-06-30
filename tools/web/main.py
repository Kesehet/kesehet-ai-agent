from typing import Any

import requests
import trafilatura
from ddgs import DDGS
from ddgs.http_client import HttpClient


HttpClient._impersonates = ("chrome_146",)
HttpClient._impersonates_os = ("windows",)


def search_web(query: str, max_results: int = 5) -> list[dict[str, str]]:
    """
    Search the web and return relevant pages.
    """
    results: list[dict[str, str]] = []
    safe_limit = max(1, min(max_results, 10))

    with DDGS() as ddgs:
        for item in ddgs.text(query, max_results=safe_limit):
            results.append({
                "title": item.get("title", ""),
                "url": item.get("href", ""),
                "snippet": item.get("body", ""),
            })

    return results


def ai_scrape(url: str) -> dict[str, str]:
    """
    Scrape a single webpage and return clean AI-usable content.
    """
    headers = {
        "User-Agent": "Mozilla/5.0 AIContentBot/1.0",
    }

    response = requests.get(url, headers=headers, timeout=15)
    response.raise_for_status()

    text = trafilatura.extract(
        response.text,
        include_comments=False,
        include_tables=True,
        include_links=False,
    )

    return {
        "url": url,
        "content": text or "",
    }


def web_search(query: str, max_results: int = 5) -> list[dict[str, Any]]:
    """
    Search the web, visit each result, and extract clean content.
    """
    search_results = search_web(query, max_results=max_results)
    scraped_pages: list[dict[str, Any]] = []

    for result in search_results:
        url = result["url"]

        try:
            page = ai_scrape(url)
            scraped_pages.append({
                "title": result["title"],
                "url": url,
                "snippet": result["snippet"],
                "content": page["content"],
            })
        except Exception as error:
            scraped_pages.append({
                "title": result["title"],
                "url": url,
                "snippet": result["snippet"],
                "content": "",
                "error": str(error),
            })

    return scraped_pages
