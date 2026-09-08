"""Platform/format assertion helpers (kept tiny for import safety)."""


def assert_platform_format(platform: str, format: str) -> None:
    if platform not in ("x", "threads"):
        raise ValueError(f"unsupported platform '{platform}'")
    if format not in ("single", "thread"):
        raise ValueError(f"unsupported format '{format}'")


def platform_budget(platform: str, format: str) -> str:
    """Human-readable length budget for LLM prompts, loaded from platforms.yml.

    The numbers are NEVER hardcoded here - config/platforms.yml is the source
    of truth. A 10-character safety margin is applied to the raw limit so the
    deterministic validator (weighted length) has headroom.
    """
    from src.config import get_config

    cfg = get_config().platforms.get(platform, {})
    single_max = int(cfg.get("single", {}).get("max_length", 280))
    url_len = int(cfg.get("single", {}).get("url_length", 23))
    budget = single_max - 10
    if format == "thread":
        thread = cfg.get("thread", {})
        return (
            f"THREAD on {platform}: each post hard budget {budget} characters "
            f"(weighted; URLs count {url_len}), up to "
            f"{int(thread.get('max_posts', 18))} posts, thread total under "
            f"{int(thread.get('max_total_length', 5600))} characters."
        )
    return (
        f"SINGLE on {platform}: the ENTIRE post must be {budget} characters or "
        f"less (weighted; URLs count {url_len}). This is a hard maximum."
    )
