import argparse
from yt_dlp import YoutubeDL


def download(
    url: str,
    output_template: str | None = None,
    cookies_from_browser: str | None = None,
    cookies_file: str | None = None,
) -> None:
    """Download a video from the specified URL.

    Parameters

    url: str
        The video URL.
    output_template: Optional[str]
        A yt-dlp template for naming the output file. If omitted the default
        template from yt-dlp will be used (e.g. "%(title)s.%(ext)s").
    """
    ydl_opts: dict = {
        "http_headers": {
            "Origin": "https://www.bilibili.com",
            "Referer": "https://www.bilibili.com/",
        },
    }
    if output_template:
        ydl_opts["outtmpl"] = output_template
    if cookies_from_browser:
        ydl_opts["cookiesfrombrowser"] = (cookies_from_browser,)
    if cookies_file:
        ydl_opts["cookiefile"] = cookies_file

    with YoutubeDL(ydl_opts) as ydl:
        ydl.download([url])


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Download a video with yt-dlp.",
    )
    parser.add_argument("url")
    parser.add_argument("output_template", nargs="?")
    parser.add_argument(
        "--cookies-from-browser",
        help='Read cookies from a browser, for example "chrome" or "edge".',
    )
    parser.add_argument(
        "--cookies",
        dest="cookies_file",
        help="Read cookies from a Netscape-format cookies.txt file.",
    )
    args = parser.parse_args()

    download(
        args.url,
        args.output_template,
        cookies_from_browser=args.cookies_from_browser,
        cookies_file=args.cookies_file,
    )


if __name__ == "__main__":
    main()
