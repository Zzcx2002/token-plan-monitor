"""Regenerate the application and documentation icons from the shared drawing."""

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
    png_destination = destination.with_suffix(".png")
    make_brand_icon(512).save(png_destination, format="PNG")
    print(destination)
    print(png_destination)


if __name__ == "__main__":
    main()
