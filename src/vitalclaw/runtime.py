"""Project-local runtime paths and config handling."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import os
from pathlib import Path
import tomllib
from typing import Literal

DEFAULT_DAILY_CHECK_TIME = "08:00"
DEFAULT_DATA_SOURCE = "health_export"
DEFAULT_OPEN_WEARABLES_API_URL = "https://api.openwearables.io"
DataSource = Literal["health_export", "open_wearables"]


@dataclass(slots=True)
class RuntimePaths:
    """Filesystem layout for the local VitalClaw runtime."""

    project_root: Path
    runtime_dir: Path
    raw_dir: Path
    bin_dir: Path
    xdg_config_home: Path
    config_path: Path
    db_path: Path


@dataclass(slots=True)
class AppConfig:
    """Project-local VitalClaw configuration."""

    source: DataSource = DEFAULT_DATA_SOURCE
    he_path: str | None = None
    timezone: str = "UTC"
    daily_check_time: str = DEFAULT_DAILY_CHECK_TIME
    initialized_at: str | None = None
    api_url: str | None = None
    ow_api_url: str | None = None
    ow_api_key: str | None = None
    ow_user_id: str | None = None
    ow_last_invitation_code: str | None = None
    ow_developer_email: str | None = None
    ow_developer_password: str | None = None
    required_types: dict[str, int] = field(default_factory=dict)


def find_project_root(start: Path | None = None) -> Path:
    """Return the nearest project root that contains pyproject.toml."""
    current = (start or Path.cwd()).resolve()
    for candidate in (current, *current.parents):
        if (candidate / "pyproject.toml").exists():
            return candidate
    return current


def get_runtime_paths(project_root: Path | None = None) -> RuntimePaths:
    """Construct project-local runtime paths."""
    root = find_project_root(project_root)
    runtime_dir = root / ".vitalclaw"
    return RuntimePaths(
        project_root=root,
        runtime_dir=runtime_dir,
        raw_dir=runtime_dir / "raw",
        bin_dir=runtime_dir / "bin",
        xdg_config_home=runtime_dir / "xdg",
        config_path=runtime_dir / "config.toml",
        db_path=runtime_dir / "vitalclaw.sqlite3",
    )


def ensure_runtime_dirs(paths: RuntimePaths) -> None:
    """Create the runtime directory structure."""
    for directory in (
        paths.runtime_dir,
        paths.raw_dir,
        paths.bin_dir,
        paths.xdg_config_home / "healthexport",
    ):
        directory.mkdir(parents=True, exist_ok=True)


def load_config(paths: RuntimePaths) -> AppConfig | None:
    """Load project-local config from TOML if present."""
    if not paths.config_path.exists():
        return None
    data = tomllib.loads(paths.config_path.read_text(encoding="utf-8"))
    app = data.get("app", {})
    remote = data.get("health_export", {})
    ow = data.get("open_wearables", {})
    required_types = {
        str(key): int(value)
        for key, value in data.get("required_types", {}).items()
    }
    source = _normalize_optional_str(app.get("source")) or DEFAULT_DATA_SOURCE
    if source not in {"health_export", "open_wearables"}:
        source = DEFAULT_DATA_SOURCE
    return AppConfig(
        source=source,
        he_path=_normalize_optional_str(remote.get("he_path")),
        timezone=_normalize_optional_str(app.get("timezone")) or "UTC",
        daily_check_time=_normalize_optional_str(app.get("daily_check_time")) or DEFAULT_DAILY_CHECK_TIME,
        initialized_at=_normalize_optional_str(app.get("initialized_at")),
        api_url=_normalize_optional_str(remote.get("api_url")),
        ow_api_url=_normalize_optional_str(ow.get("api_url")) or None,
        ow_api_key=_normalize_optional_str(ow.get("api_key")) or None,
        ow_user_id=_normalize_optional_str(ow.get("user_id")) or None,
        ow_last_invitation_code=_normalize_optional_str(ow.get("last_invitation_code")) or None,
        ow_developer_email=_normalize_optional_str(ow.get("developer_email")) or None,
        ow_developer_password=_normalize_optional_str(ow.get("developer_password")) or None,
        required_types=required_types,
    )


def save_config(paths: RuntimePaths, config: AppConfig) -> None:
    """Persist project-local config as TOML."""
    ensure_runtime_dirs(paths)
    lines = [
        "[app]",
        f'source = "{_escape_toml(config.source)}"',
        f'timezone = "{_escape_toml(config.timezone)}"',
        f'daily_check_time = "{_escape_toml(config.daily_check_time)}"',
    ]
    if config.initialized_at:
        lines.append(f'initialized_at = "{_escape_toml(config.initialized_at)}"')

    lines.extend(
        [
            "",
            "[health_export]",
            f'he_path = "{_escape_toml(config.he_path or "")}"',
        ]
    )
    if config.api_url:
        lines.append(f'api_url = "{_escape_toml(config.api_url)}"')

    lines.extend(["", "[open_wearables]"])
    lines.append(f'api_url = "{_escape_toml(config.ow_api_url or DEFAULT_OPEN_WEARABLES_API_URL)}"')
    lines.append(f'api_key = "{_escape_toml(config.ow_api_key or "")}"')
    lines.append(f'user_id = "{_escape_toml(config.ow_user_id or "")}"')
    lines.append(f'last_invitation_code = "{_escape_toml(config.ow_last_invitation_code or "")}"')
    lines.append(f'developer_email = "{_escape_toml(config.ow_developer_email or "")}"')
    lines.append(f'developer_password = "{_escape_toml(config.ow_developer_password or "")}"')

    lines.extend(["", "[required_types]"])
    for key, value in sorted(config.required_types.items()):
        lines.append(f"{key} = {int(value)}")

    paths.config_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def local_timezone_name() -> str:
    """Return the best-effort local timezone name."""
    env_tz = os.environ.get("TZ")
    if env_tz:
        return env_tz
    tzinfo = datetime.now().astimezone().tzinfo
    key = getattr(tzinfo, "key", None)
    if isinstance(key, str) and key:
        return key
    return "UTC"


def _escape_toml(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _normalize_optional_str(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
