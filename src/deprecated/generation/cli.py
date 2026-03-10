"""CLI entry point for the Tellimations generation pipeline.

Usage:
    python -m src.generation.cli \\
        --api-key YOUR_KEY \\
        --character-name "Luna" \\
        --character-type "rabbit" \\
        --setting-place "enchanted forest" \\
        --setting-mood "mysterious and magical" \\
        --output output/

    Resume from a specific step:
    python -m src.generation.cli \\
        --api-key YOUR_KEY \\
        --character-name "Luna" \\
        --character-type "rabbit" \\
        --setting-place "enchanted forest" \\
        --setting-mood "mysterious and magical" \\
        --output output/ \\
        --session session_001 \\
        --resume-from features

Environment variable GEMINI_API_KEY can be used instead of --api-key.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from pathlib import Path

from src.generation.pipeline import PipelineError, run_pipeline
from src.models.pipeline import PipelineConfig, PipelineStep
from src.models.plot import PlotCharacter, PlotSetting


def _parse_traits(traits_str: str) -> dict[str, str]:
    """Parse comma-separated key=value traits.

    Example: "color=brown,personality=brave,size=small"
    """
    if not traits_str:
        return {}
    traits = {}
    for pair in traits_str.split(","):
        pair = pair.strip()
        if "=" in pair:
            k, v = pair.split("=", 1)
            traits[k.strip()] = v.strip()
    return traits


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Tellimations generation pipeline CLI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )

    # API key
    parser.add_argument(
        "--api-key",
        default=os.environ.get("GEMINI_API_KEY", ""),
        help="Gemini API key (or set GEMINI_API_KEY env var)",
    )

    # Character
    parser.add_argument(
        "--character-name",
        required=True,
        help="Main character name (e.g., 'Luna')",
    )
    parser.add_argument(
        "--character-type",
        required=True,
        help="Character type (e.g., 'rabbit', 'cat', 'dragon')",
    )
    parser.add_argument(
        "--character-traits",
        default="",
        help="Comma-separated key=value traits "
             "(e.g., 'color=brown,personality=brave')",
    )

    # Setting
    parser.add_argument(
        "--setting-place",
        required=True,
        help="Setting location (e.g., 'enchanted forest')",
    )
    parser.add_argument(
        "--setting-mood",
        required=True,
        help="Setting ambiance (e.g., 'mysterious and magical')",
    )
    parser.add_argument(
        "--setting-epoch",
        default="present",
        help="Setting time period (default: 'present')",
    )

    # Output
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("output"),
        help="Output root directory (default: output/)",
    )
    parser.add_argument(
        "--session",
        default="session_001",
        help="Session identifier (default: session_001)",
    )

    # Resume
    parser.add_argument(
        "--resume-from",
        choices=[s.value for s in PipelineStep],
        default=None,
        help="Resume pipeline from this step (rerun it and all subsequent)",
    )

    # Retry settings
    parser.add_argument(
        "--max-retries",
        type=int,
        default=3,
        help="Max retries per LLM call (default: 3)",
    )

    # Verbosity
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Enable debug logging",
    )

    return parser


async def async_main(args: argparse.Namespace) -> None:
    """Run the pipeline with parsed arguments."""
    if not args.api_key:
        print(
            "Error: Gemini API key required. "
            "Use --api-key or set GEMINI_API_KEY env var.",
            file=sys.stderr,
        )
        sys.exit(1)

    config = PipelineConfig(
        api_key=args.api_key,
        character=PlotCharacter(
            name=args.character_name,
            type=args.character_type,
            traits=_parse_traits(args.character_traits),
        ),
        setting=PlotSetting(
            lieu=args.setting_place,
            ambiance=args.setting_mood,
            epoch=args.setting_epoch,
        ),
        output_dir=args.output,
        session_id=args.session,
        max_retries=args.max_retries,
        resume_from=(
            PipelineStep(args.resume_from) if args.resume_from else None
        ),
    )

    result = await run_pipeline(config)

    # Print summary
    print(f"\nPipeline completed for session '{result.session_id}'")
    print(f"Output directory: {args.output / args.session}")
    print(f"Completed steps: {[s.value for s in result.state.completed_steps]}")
    if result.plot:
        print(f"Scenes: {len(result.plot.plot)}")
    if result.neg:
        total_wp = sum(len(s.waypoints) for s in result.neg.scenes)
        print(f"NEG waypoints: {total_wp}")
    if result.features:
        total_elem = sum(len(s.elements) for s in result.features.scenes)
        print(f"Elements scanned: {total_elem}")
    if result.masks:
        total_masks = sum(
            len(p.parts) for sm in result.masks.scenes for p in sm.elements
        )
        print(f"Masks generated: {total_masks}")


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    try:
        asyncio.run(async_main(args))
    except PipelineError as exc:
        print(f"\nPipeline failed: {exc}", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        print("\nInterrupted by user.", file=sys.stderr)
        sys.exit(130)


if __name__ == "__main__":
    main()
