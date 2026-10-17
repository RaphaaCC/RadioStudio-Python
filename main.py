"""Compatibility entry point; the application now lives in the radiostudio package."""

from radiostudio.app import main


if __name__ == "__main__":
    raise SystemExit(main())
