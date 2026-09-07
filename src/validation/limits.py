"""Platform/format assertion helpers (kept tiny for import safety)."""


def assert_platform_format(platform: str, format: str) -> None:
    if platform not in ("x", "threads"):
        raise ValueError(f"unsupported platform '{platform}'")
    if format not in ("single", "thread"):
        raise ValueError(f"unsupported format '{format}'")
