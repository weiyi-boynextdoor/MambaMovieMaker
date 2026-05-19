import argparse
import shutil
from pathlib import Path

import torch


DEFAULT_MODEL = "htdemucs"


def ensure_demucs_available() -> None:
    try:
        import demucs  # noqa: F401
    except ImportError as exc:
        raise SystemExit(
            "Demucs is not installed.\n"
            "Install it in your conda base environment first:\n"
            "  conda activate base\n"
            "  python -m pip install -U demucs --no-deps\n"
            "\n"
            "If PyTorch cannot use your RTX 5080, install a CUDA-enabled "
            "PyTorch build that matches your driver."
        ) from exc


def ensure_soundfile_available() -> None:
    try:
        import soundfile  # noqa: F401
    except ImportError as exc:
        raise SystemExit(
            "soundfile is not installed.\n"
            "Install it in your conda base environment first:\n"
            "  conda activate base\n"
            "  python -m pip install soundfile --no-deps"
        ) from exc


def ensure_ffmpeg_available() -> None:
    if shutil.which("ffmpeg"):
        return

    raise SystemExit(
        "ffmpeg was not found in PATH.\n"
        "Install it before running this script, for example:\n"
        "  conda activate base\n"
        "  conda install -c conda-forge ffmpeg"
    )


def detect_device(preferred_device: str) -> str:
    if preferred_device != "auto":
        return preferred_device

    try:
        import torch

        if torch.cuda.is_available():
            device_name = torch.cuda.get_device_name(0)
            print(f"Using CUDA device: {device_name}")
            return "cuda"
    except ImportError:
        pass

    print("CUDA is not available to PyTorch. Falling back to CPU.")
    return "cpu"


def find_demucs_outputs(work_dir: Path, model: str, input_path: Path) -> tuple[Path, Path]:
    stem = input_path.stem
    model_dir = work_dir / model / stem
    vocals_path = model_dir / "vocals.wav"
    no_vocals_path = model_dir / "no_vocals.wav"

    if not vocals_path.exists() or not no_vocals_path.exists():
        raise FileNotFoundError(
            "Demucs finished, but the expected output files were not found: "
            f"{vocals_path} and {no_vocals_path}"
        )

    return vocals_path, no_vocals_path


def prevent_clip(wav: torch.Tensor, mode: str) -> torch.Tensor:
    if mode in (None, "none"):
        return wav
    if mode == "rescale":
        return wav / max(1.01 * wav.abs().max().item(), 1)
    if mode == "clamp":
        return wav.clamp(-0.99, 0.99)
    if mode == "tanh":
        return torch.tanh(wav)
    raise ValueError(f"Invalid clip mode: {mode}")


def save_wav_without_torchcodec(
    wav: torch.Tensor,
    path: str | Path,
    samplerate: int,
    bitrate: int = 320,
    clip: str = "rescale",
    bits_per_sample: int = 16,
    as_float: bool = False,
    preset: int = 2,
) -> None:
    """Save Demucs output without torchaudio's TorchCodec writer."""
    import soundfile as sf

    output_path = Path(path)
    suffix = output_path.suffix.lower()
    if suffix != ".wav":
        raise ValueError("This script saves WAV output only.")

    wav = prevent_clip(wav.detach().cpu(), clip)
    data = wav.transpose(0, 1).numpy()

    if as_float:
        subtype = "FLOAT"
    elif bits_per_sample == 24:
        subtype = "PCM_24"
    elif bits_per_sample == 32:
        subtype = "PCM_32"
    else:
        subtype = "PCM_16"

    sf.write(output_path, data, samplerate, subtype=subtype)


def run_demucs(
    input_path: Path,
    work_dir: Path,
    model: str,
    device: str,
) -> None:
    from demucs import separate as demucs_separate

    demucs_separate.save_audio = save_wav_without_torchcodec
    demucs_separate.main(
        [
            "--two-stems",
            "vocals",
            "--name",
            model,
            "--device",
            device,
            "--out",
            str(work_dir),
            str(input_path),
        ]
    )


def separate_audio(
    input_path: Path,
    output_dir: Path,
    model: str,
    device: str,
    overwrite: bool,
) -> tuple[Path, Path]:
    ensure_demucs_available()
    ensure_soundfile_available()
    ensure_ffmpeg_available()

    if not input_path.exists():
        raise FileNotFoundError(f"Input file does not exist: {input_path}")

    output_dir.mkdir(parents=True, exist_ok=True)
    work_dir = output_dir / "_demucs_work"
    work_dir.mkdir(parents=True, exist_ok=True)

    resolved_device = detect_device(device)

    print(f"Running Demucs model {model} on {resolved_device}")
    run_demucs(input_path, work_dir, model, resolved_device)

    vocals_path, background_path = find_demucs_outputs(work_dir, model, input_path)
    final_vocals = output_dir / f"{input_path.stem}_vocals.wav"
    final_background = output_dir / f"{input_path.stem}_background.wav"

    if not overwrite:
        for final_path in (final_vocals, final_background):
            if final_path.exists():
                raise FileExistsError(
                    f"Output already exists: {final_path}. "
                    "Use --overwrite to replace it."
                )

    shutil.copy2(vocals_path, final_vocals)
    shutil.copy2(background_path, final_background)

    return final_vocals, final_background


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Separate vocals and background audio with Demucs.",
    )
    parser.add_argument("input", help="Input audio or video file, such as .mp3, .wav, or .mp4.")
    parser.add_argument(
        "-o",
        "--output-dir",
        default="separated_audio",
        help="Directory for separated files. Default: separated_audio",
    )
    parser.add_argument(
        "--model",
        default=DEFAULT_MODEL,
        help=f"Demucs model name. Default: {DEFAULT_MODEL}",
    )
    parser.add_argument(
        "--device",
        default="auto",
        choices=("auto", "cuda", "cpu"),
        help="Compute device. Default: auto",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Replace existing output files.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    vocals_path, background_path = separate_audio(
        input_path=Path(args.input),
        output_dir=Path(args.output_dir),
        model=args.model,
        device=args.device,
        overwrite=args.overwrite,
    )

    print("Done.")
    print(f"Vocals: {vocals_path}")
    print(f"Background: {background_path}")


if __name__ == "__main__":
    main()
