from __future__ import annotations

import json
import re
from dataclasses import dataclass
from html import unescape
from urllib.parse import urlparse, urlunparse

import aiohttp

STORE_HOSTS = {
    "Best Buy": {"bestbuy.com", "www.bestbuy.com"},
    "Walmart": {"walmart.com", "www.walmart.com"},
    "Target": {"target.com", "www.target.com"},
    "Barnes & Noble": {"barnesandnoble.com", "www.barnesandnoble.com"},
}


@dataclass(slots=True)
class ProductStatus:
    url: str
    store: str
    sku: str | None
    name: str
    price: str | None
    image_url: str | None
    available: bool | None
    detail: str


def detect_store(value: str) -> str:
    parsed = urlparse(value.strip())
    hostname = (parsed.hostname or "").lower()
    for store, hosts in STORE_HOSTS.items():
        if hostname in hosts:
            return store
    raise ValueError("Supported stores are Best Buy, Walmart, Target, and Barnes & Noble.")


def normalize_product_url(value: str) -> tuple[str, str]:
    value = value.strip()
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("Please paste the full product link beginning with https://")
    store = detect_store(value)
    # Remove fragments while preserving query strings that may identify products.
    clean = urlunparse((parsed.scheme, parsed.netloc, parsed.path, parsed.params, parsed.query, ""))
    return clean, store


def extract_sku(url: str, store: str) -> str | None:
    path = urlparse(url).path
    patterns = {
        "Best Buy": [r"/(\d{7,9})\.p(?:/|$)"],
        "Walmart": [r"/ip/(?:[^/]+/)?(\d+)(?:/|$)"],
        "Target": [r"/-/A-(\d+)(?:/|$)", r"/A-(\d+)(?:/|$)"],
        "Barnes & Noble": [r"/w/[^/]+/(\d+)(?:/|$)"],
    }
    for pattern in patterns.get(store, []):
        match = re.search(pattern, path, re.I)
        if match:
            return match.group(1)
    return None


def _json_ld_objects(html: str) -> list[dict]:
    found: list[dict] = []
    pattern = re.compile(
        r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        re.I | re.S,
    )
    for raw in pattern.findall(html):
        try:
            root = json.loads(unescape(raw).strip())
        except (json.JSONDecodeError, TypeError):
            continue
        queue = root if isinstance(root, list) else [root]
        while queue:
            value = queue.pop(0)
            if isinstance(value, list):
                queue.extend(value)
            elif isinstance(value, dict):
                found.append(value)
                graph = value.get("@graph")
                if isinstance(graph, list):
                    queue.extend(graph)
    return found


def _clean_text(value: object) -> str | None:
    if value is None:
        return None
    text = re.sub(r"\s+", " ", unescape(str(value))).strip()
    return text or None


def _format_price(raw: object, currency: object = "USD") -> str | None:
    if raw in (None, ""):
        return None
    text = str(raw).strip()
    if text.startswith("$"):
        return text
    if str(currency).upper() == "USD":
        return f"${text}"
    return f"{currency} {text}"


def _availability_from_value(raw: object) -> bool | None:
    value = str(raw or "").lower().replace("_", "").replace("-", "")
    if any(token in value for token in ("instock", "limitedavailability", "availablefororder")):
        return True
    if any(token in value for token in ("outofstock", "soldout", "discontinued", "unavailable")):
        return False
    return None


def parse_product_page(url: str, store: str, html: str) -> ProductStatus:
    lowered = html.lower()
    sku = extract_sku(url, store)
    name = f"{store} product"
    price: str | None = None
    image_url: str | None = None
    available: bool | None = None
    detail = "Availability could not be confirmed."

    for obj in _json_ld_objects(html):
        obj_type = obj.get("@type")
        if obj_type == "Product" or (isinstance(obj_type, list) and "Product" in obj_type):
            name = _clean_text(obj.get("name")) or name
            image = obj.get("image")
            if isinstance(image, list) and image:
                first = image[0]
                image_url = str(first.get("url")) if isinstance(first, dict) else str(first)
            elif isinstance(image, dict):
                image_url = str(image.get("url") or "") or None
            elif isinstance(image, str):
                image_url = image

            offers = obj.get("offers")
            offer_list = offers if isinstance(offers, list) else [offers]
            for offer in offer_list:
                if not isinstance(offer, dict):
                    continue
                price = price or _format_price(offer.get("price") or offer.get("lowPrice"), offer.get("priceCurrency", "USD"))
                state = _availability_from_value(offer.get("availability"))
                if state is not None:
                    available = state
                    detail = "Available online" if state else "Out of stock"
                    break
        if available is not None:
            break

    # Store-specific signals. Positive matches are deliberately strict to avoid
    # telling a Discord server that an item restocked when it did not.
    positive_tokens = {
        "Best Buy": ('"buttonstate":"add_to_cart"', '>add to cart<', 'add to cart</button>'),
        "Walmart": ('"availabilitystatus":"in_stock"', '"availabilitystatus":"instock"', '"isavailable":true', '>add to cart<'),
        "Target": ('"availability_status":"in_stock"', '"availabilitystatus":"instock"', '"purchasable":true', '>add to cart<'),
        "Barnes & Noble": ('"availability":"instock"', '>add to cart<', 'add to bag'),
    }
    negative_tokens = {
        "Best Buy": ('"buttonstate":"sold_out"', '>sold out<', '>coming soon<'),
        "Walmart": ('"availabilitystatus":"out_of_stock"', '"availabilitystatus":"outofstock"', '>out of stock<'),
        "Target": ('"availability_status":"out_of_stock"', '"availabilitystatus":"outofstock"', '>out of stock<', 'sold out'),
        "Barnes & Noble": ('"availability":"outofstock"', '>out of stock<', 'temporarily out of stock'),
    }
    if available is None:
        if any(token in lowered for token in positive_tokens[store]):
            available = True
            detail = "Add to Cart detected"
        elif any(token in lowered for token in negative_tokens[store]):
            available = False
            detail = "Unavailable"

    title_match = re.search(r"<title[^>]*>(.*?)</title>", html, re.I | re.S)
    if name == f"{store} product" and title_match:
        name = _clean_text(re.sub(r"<[^>]+>", "", title_match.group(1))) or name
        for suffix in (" - Best Buy", " | Walmart.com", " : Target", " | Barnes & Noble®"):
            if name.endswith(suffix):
                name = name[: -len(suffix)].strip()

    return ProductStatus(
        url=url,
        store=store,
        sku=sku,
        name=name[:250],
        price=price,
        image_url=image_url,
        available=available,
        detail=detail,
    )


async def fetch_product_status(url: str) -> ProductStatus:
    normalized, store = normalize_product_url(url)
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0 Safari/537.36"
        ),
        "Accept-Language": "en-US,en;q=0.9",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Cache-Control": "no-cache",
    }
    timeout = aiohttp.ClientTimeout(total=25)
    async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
        async with session.get(normalized, allow_redirects=True) as response:
            if response.status in {403, 429}:
                raise RuntimeError(f"{store} blocked or rate-limited this check (HTTP {response.status}).")
            if response.status >= 400:
                raise RuntimeError(f"{store} returned HTTP {response.status}.")
            html = await response.text(errors="replace")
            final_url, final_store = normalize_product_url(str(response.url))
            return parse_product_page(final_url, final_store, html)
