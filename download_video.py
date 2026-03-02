# pip install yt-dlp
import sys
from yt_dlp import YoutubeDL


def download(url: str, output_template: str | None = None) -> None:
    """Download a video from the specified URL.

    Parameters
    
    url: str
        The video URL.
    output_template: Optional[str]
        A yt-dlp template for naming the output file. If omitted the default
        template from yt-dlp will be used (e.g. "%(title)s.%(ext)s").
    """
    ydl_opts: dict = {}
    if output_template:
        ydl_opts["outtmpl"] = output_template

    with YoutubeDL(ydl_opts) as ydl:
        ydl.download([url])



def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python download_video.py <url> [output_template]")
        sys.exit(1)

    url = sys.argv[1]
    output = sys.argv[2] if len(sys.argv) > 2 else None
    download(url, output)


if __name__ == "__main__":
    main()
