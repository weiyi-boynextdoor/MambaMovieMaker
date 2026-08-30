#!/usr/bin/env python3
"""
Extract HLS playback links from a GDC Vault presentation page.
Download with this command:
ffmpeg -i "xxx.m3u8" -c copy -movflags +faststart "output.mp4"
"""

from __future__ import annotations

import argparse
import http.cookiejar
import json
import re
import ssl
import sys
import time
from dataclasses import asdict, dataclass
from html.parser import HTMLParser
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urljoin, urlparse
from urllib.request import HTTPSHandler, HTTPCookieProcessor, Request, build_opener


USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0 Safari/537.36"
)
VIDEO_ID_PATTERN = re.compile(r"^[0-9a-f]{32}$")
PLAYBACK_TAIL_PATTERN = re.compile(
    r"GDC_PLAYBACK_TAIL\s*=\s*['\"](?P<tail>/[^'\"]+/index\.m3u8)['\"]"
)
PLAYBACK_ORIGIN_PATTERN = re.compile(
    r"['\"](?P<origin>https://[^/'\"]+)/out/v1/['\"]"
)


class ExtractionError(RuntimeError):
    """Raised when a playback URL cannot be extracted."""


class ResourceParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.iframes: list[str] = []
        self.scripts: list[str] = []

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        values = dict(attrs)
        if tag == "iframe" and values.get("src"):
            self.iframes.append(values["src"] or "")
        elif tag == "script" and values.get("src"):
            self.scripts.append(values["src"] or "")


@dataclass(frozen=True)
class Variant:
    url: str
    resolution: str | None = None
    bandwidth: int | None = None
    average_bandwidth: int | None = None
    frame_rate: float | None = None
    codecs: str | None = None


@dataclass(frozen=True)
class ExtractionResult:
    page_url: str
    player_url: str
    video_id: str
    master_url: str
    source: str
    variants: list[Variant]


class GDCVaultExtractor:
    def __init__(self, timeout: float = 20.0, cookies_file: str | None = None):
        self.timeout = timeout
        ssl_context = ssl.create_default_context()
        ignore_unexpected_eof = getattr(ssl, "OP_IGNORE_UNEXPECTED_EOF", 0)
        ssl_context.options |= ignore_unexpected_eof
        handlers: list[Any] = [HTTPSHandler(context=ssl_context)]
        if cookies_file:
            cookie_jar = http.cookiejar.MozillaCookieJar(cookies_file)
            try:
                cookie_jar.load(ignore_discard=True, ignore_expires=True)
            except (FileNotFoundError, http.cookiejar.LoadError) as error:
                raise ExtractionError(f"Cannot load cookies file: {error}") from error
            handlers.append(HTTPCookieProcessor(cookie_jar))
        self.opener = build_opener(*handlers)

    def fetch_text(self, url: str, referer: str | None = None) -> str:
        headers = {"User-Agent": USER_AGENT, "Accept-Encoding": "identity"}
        if referer:
            headers["Referer"] = referer
        request = Request(url, headers=headers)
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                with self.opener.open(request, timeout=self.timeout) as response:
                    payload = response.read()
                    charset = response.headers.get_content_charset() or "utf-8"
                    return payload.decode(charset, errors="replace")
            except (HTTPError, URLError, TimeoutError) as error:
                last_error = error
                if attempt < 2:
                    time.sleep(0.5 * (attempt + 1))
        raise ExtractionError(f"Failed to fetch {url}: {last_error}") from last_error

    def extract(self, page_url: str) -> ExtractionResult:
        player_url = self._find_player_url(page_url)
        video_id = self._video_id(player_url)
        player_html = self.fetch_text(player_url, referer=page_url)
        master_url, source = self._resolve_master_url(
            player_url, player_html, video_id
        )
        master_playlist = self.fetch_text(master_url, referer=player_url)
        variants = parse_master_playlist(master_url, master_playlist)
        return ExtractionResult(
            page_url=page_url,
            player_url=player_url,
            video_id=video_id,
            master_url=master_url,
            source=source,
            variants=variants,
        )

    def _find_player_url(self, page_url: str) -> str:
        parsed = urlparse(page_url)
        if parsed.hostname == "gdcvault.blazestreaming.com":
            return page_url

        page_html = self.fetch_text(page_url)
        resources = parse_resources(page_html)
        for iframe in resources.iframes:
            candidate = urljoin(page_url, iframe)
            if urlparse(candidate).hostname == "gdcvault.blazestreaming.com":
                return candidate
        raise ExtractionError("No GDC Vault Blaze Streaming iframe was found")

    @staticmethod
    def _video_id(player_url: str) -> str:
        video_id = parse_qs(urlparse(player_url).query).get("id", [""])[0]
        if not VIDEO_ID_PATTERN.fullmatch(video_id):
            raise ExtractionError(f"Invalid or missing player video id: {video_id!r}")
        return video_id

    def _resolve_master_url(
        self, player_url: str, player_html: str, video_id: str
    ) -> tuple[str, str]:
        resources = parse_resources(player_html)
        script_urls = [
            urljoin(player_url, script)
            for script in resources.scripts
            if "script_VOD" in script
        ]
        if not script_urls:
            raise ExtractionError("The player VOD script was not found")

        player_script = self.fetch_text(script_urls[0], referer=player_url)
        tail_match = PLAYBACK_TAIL_PATTERN.search(player_script)
        origin_match = PLAYBACK_ORIGIN_PATTERN.search(player_script)
        if not tail_match or not origin_match:
            raise ExtractionError("The playback URL rule was not found in the player script")

        legacy_url = (
            f"{origin_match.group('origin')}/out/v1/{video_id}"
            f"{tail_match.group('tail')}"
        )
        aliases_url = urljoin(player_url, "aliases.json")
        try:
            aliases = json.loads(self.fetch_text(aliases_url, referer=player_url))
            entry = aliases.get("aliases", {}).get(video_id)
            mapped_url = entry.get("playbackUrl") if isinstance(entry, dict) else None
            if isinstance(mapped_url, str) and mapped_url.startswith("https://"):
                return mapped_url, "alias"
        except (ExtractionError, json.JSONDecodeError, AttributeError):
            pass
        return legacy_url, "legacy"


def parse_resources(html: str) -> ResourceParser:
    parser = ResourceParser()
    parser.feed(html)
    return parser


def parse_attribute_list(value: str) -> dict[str, str]:
    attributes: dict[str, str] = {}
    for match in re.finditer(r'(\w+(?:-\w+)*)=("[^"]*"|[^,]*)', value):
        raw_value = match.group(2)
        attributes[match.group(1)] = raw_value.strip('"')
    return attributes


def parse_master_playlist(master_url: str, playlist: str) -> list[Variant]:
    lines = [line.strip() for line in playlist.splitlines() if line.strip()]
    variants: list[Variant] = []
    for index, line in enumerate(lines):
        if not line.startswith("#EXT-X-STREAM-INF:"):
            continue
        if index + 1 >= len(lines) or lines[index + 1].startswith("#"):
            continue
        attributes = parse_attribute_list(line.partition(":")[2])
        variants.append(
            Variant(
                url=urljoin(master_url, lines[index + 1]),
                resolution=attributes.get("RESOLUTION"),
                bandwidth=to_int(attributes.get("BANDWIDTH")),
                average_bandwidth=to_int(attributes.get("AVERAGE-BANDWIDTH")),
                frame_rate=to_float(attributes.get("FRAME-RATE")),
                codecs=attributes.get("CODECS"),
            )
        )
    return variants


def to_int(value: str | None) -> int | None:
    try:
        return int(value) if value is not None else None
    except ValueError:
        return None


def to_float(value: str | None) -> float | None:
    try:
        return float(value) if value is not None else None
    except ValueError:
        return None


def print_human(result: ExtractionResult) -> None:
    print(f"Player: {result.player_url}")
    print(f"Video ID: {result.video_id}")
    print(f"Master ({result.source}): {result.master_url}")
    if not result.variants:
        print("Variants: none found (the master URL may itself be a media playlist)")
        return
    print("Variants:")
    for variant in result.variants:
        details = [item for item in (variant.resolution, format_fps(variant.frame_rate)) if item]
        label = ", ".join(details) or "unknown quality"
        print(f"  {label}: {variant.url}")


def format_fps(value: float | None) -> str | None:
    return f"{value:g} fps" if value is not None else None


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Extract HLS links from a GDC Vault presentation page."
    )
    parser.add_argument("url", help="GDC Vault presentation or player URL")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON")
    parser.add_argument(
        "--cookies",
        metavar="FILE",
        help="Load a Netscape-format cookies.txt file for authenticated pages",
    )
    parser.add_argument("--timeout", type=float, default=20.0, help="HTTP timeout in seconds")
    args = parser.parse_args()

    try:
        result = GDCVaultExtractor(args.timeout, args.cookies).extract(args.url)
    except ExtractionError as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(asdict(result), ensure_ascii=False, indent=2))
    else:
        print_human(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
