"""Regenerate the multi-size application icon from the shared Pillow drawing."""

from pathlib import Path

from token_plan_monitor.assets import make_brand_icon


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    destination = PROJECT_ROOT / "assets" / "app.ico"
    destination.parent.mkdir(parents=True, exist_ok=True)
    make_brand_icon(256).save(
        destination,
        format="ICO",
        sizes=[
            (16, 16),
            (20, 20),
            (24, 24),
            (32, 32),
            (40, 40),
            (48, 48),
            (64, 64),
            (128, 128),
            (256, 256),
        ],
    )
    print(destination)


if __name__ == "__main__":
    main()
