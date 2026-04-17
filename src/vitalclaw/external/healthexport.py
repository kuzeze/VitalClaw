"""Adapter for the official HealthExport Remote CLI."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import json
import os
import platform
import shutil
import subprocess
import tarfile
import tempfile
from typing import Any
from urllib.request import urlopen

from vitalclaw.runtime import RuntimePaths, ensure_runtime_dirs

LATEST_RELEASE_URL = "https://api.github.com/repos/TParizek/healthexport_cli/releases/latest"
BREW_TAP = "TParizek/healthexport_tap"
BREW_FORMULA = "TParizek/healthexport_tap/he"


@dataclass(slots=True)
class HEAuthStatus:
    """Parsed HealthExport auth status."""

    authenticated: bool
    uid: str | None = None
    source: str | None = None
    masked_key: str | None = None


@dataclass(slots=True)
class HealthTypeRef:
    """One HealthExport type descriptor."""

    id: int
    name: str
    slug: str
    category: str
    subcategory: str


class HealthExportCLI:
    """Wrapper around the official `he` command."""

    def __init__(
        self,
        *,
        paths: RuntimePaths,
        he_path: str | None = None,
        api_url: str | None = None,
    ) -> None:
        self.paths = paths
        self.he_path = Path(he_path).expanduser().resolve() if he_path else None
        self.api_url = api_url

    def ensure_available(self) -> Path:
        """Verify or install the official `he` CLI."""
        ensure_runtime_dirs(self.paths)

        if self.he_path and self.he_path.exists():
            self._execute(["version"])
            return self.he_path

        on_path = shutil.which("he")
        if on_path:
            self.he_path = Path(on_path).resolve()
            self._execute(["version"])
            return self.he_path

        if self._try_homebrew_install():
            return self.he_path  # type: ignore[return-value]

        self.he_path = self._download_latest_release()
        self._execute(["version"])
        return self.he_path

    def configure_account_key(self, account_key: str) -> HEAuthStatus:
        """Store the account key in project-local HealthExport config."""
        self.ensure_available()
        self._run(["config", "set", "format", "json"])
        self._run(["config", "set", "account_key", account_key], expect_json=False)
        if self.api_url:
            self._run(["config", "set", "api_url", self.api_url], expect_json=False)
        status = self.auth_status()
        if not status.authenticated:
            raise RuntimeError("HealthExport CLI did not report an authenticated state after saving the account key.")
        return status

    def auth_status(self) -> HEAuthStatus:
        """Return the current authentication status."""
        result = self._run(["auth", "status"], expect_json=False, allow_exit_codes={0, 2})
        stderr = (result.stderr or "").strip().splitlines()
        if result.returncode == 2 or any("Not authenticated" in line for line in stderr):
            return HEAuthStatus(authenticated=False)

        masked_key = None
        uid = None
        source = None
        for line in stderr:
            stripped = line.strip()
            if stripped.startswith("Account key:"):
                masked_key = stripped.split(":", 1)[1].strip()
            elif stripped.startswith("UID:"):
                uid = stripped.split(":", 1)[1].strip()
            elif stripped.startswith("Source:"):
                source = stripped.split(":", 1)[1].strip()
        return HEAuthStatus(authenticated=True, uid=uid, source=source, masked_key=masked_key)

    def list_types(self) -> list[HealthTypeRef]:
        """List available HealthExport types."""
        raw = self._run_json(["types", "--format", "json"])
        if not isinstance(raw, list):
            raise RuntimeError("Unexpected output from `he types --format json`.")
        refs: list[HealthTypeRef] = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            refs.append(
                HealthTypeRef(
                    id=int(item["id"]),
                    name=str(item["name"]),
                    slug=_slugify(str(item["name"])),
                    category=str(item["category"]).lower(),
                    subcategory=str(item.get("subcategory", "")),
                )
            )
        return refs

    def fetch_data(self, *, type_ids: list[int], from_date: str, to_date: str) -> list[dict[str, Any]]:
        """Fetch decrypted JSON health data for the requested types and date range."""
        args = ["data", "--format", "json", "--from", from_date, "--to", to_date]
        for type_id in type_ids:
            args.extend(["--type", str(type_id)])
        raw = self._run_json(args)
        if not isinstance(raw, list):
            raise RuntimeError("Unexpected output from `he data --format json`.")
        return [item for item in raw if isinstance(item, dict)]

    def mcp_status(self) -> dict[str, Any]:
        """Return MCP diagnostics from the official CLI."""
        raw = self._run_json(["mcp", "status", "--format", "json"])
        if not isinstance(raw, dict):
            raise RuntimeError("Unexpected output from `he mcp status --format json`.")
        return raw

    def _run_json(self, args: list[str]) -> Any:
        result = self._run(args)
        stdout = (result.stdout or "").strip()
        if not stdout:
            return None
        return json.loads(stdout)

    def _run(
        self,
        args: list[str],
        *,
        expect_json: bool = True,
        allow_exit_codes: set[int] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        if self.he_path is None:
            self.ensure_available()
        return self._execute(
            args,
            expect_json=expect_json,
            allow_exit_codes=allow_exit_codes,
        )

    def _execute(
        self,
        args: list[str],
        *,
        expect_json: bool = False,
        allow_exit_codes: set[int] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        if self.he_path is None:
            raise RuntimeError("HealthExport CLI path is not configured.")
        command = [str(self.he_path), *args]
        env = os.environ.copy()
        env["XDG_CONFIG_HOME"] = str(self.paths.xdg_config_home)
        result = subprocess.run(
            command,
            cwd=self.paths.project_root,
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )
        allowed = allow_exit_codes or {0}
        if result.returncode not in allowed:
            raise RuntimeError(
                f"HealthExport CLI failed ({result.returncode}): {' '.join(command)}\n"
                f"stdout:\n{result.stdout}\n"
                f"stderr:\n{result.stderr}"
            )
        if expect_json and result.returncode == 0:
            stdout = (result.stdout or "").strip()
            if stdout:
                json.loads(stdout)
        return result

    def _try_homebrew_install(self) -> bool:
        brew = shutil.which("brew")
        if not brew:
            return False
        subprocess.run([brew, "tap", BREW_TAP, f"https://github.com/{BREW_TAP}"], check=False, capture_output=True, text=True)
        result = subprocess.run([brew, "install", BREW_FORMULA], check=False, capture_output=True, text=True)
        if result.returncode != 0 and "already installed" not in (result.stderr or "").lower():
            return False
        on_path = shutil.which("he")
        if not on_path:
            return False
        self.he_path = Path(on_path).resolve()
        return True

    def _download_latest_release(self) -> Path:
        ensure_runtime_dirs(self.paths)
        with urlopen(LATEST_RELEASE_URL) as response:  # noqa: S310
            release = json.load(response)
        asset_name, asset_url = _select_release_asset(release)
        if not asset_url:
            raise RuntimeError(f"Could not find release asset {asset_name!r} in the latest HealthExport CLI release.")

        with tempfile.TemporaryDirectory() as tmpdir:
            archive_path = Path(tmpdir) / asset_name
            with urlopen(asset_url) as response, archive_path.open("wb") as handle:  # noqa: S310
                handle.write(response.read())
            with tarfile.open(archive_path, "r:gz") as tar:
                members = [member for member in tar.getmembers() if member.name == "he"]
                if not members:
                    raise RuntimeError("Downloaded HealthExport archive does not contain the `he` binary.")
                tar.extractall(path=tmpdir, members=members)  # noqa: S202
            binary = Path(tmpdir) / "he"
            target = self.paths.bin_dir / "he"
            shutil.copy2(binary, target)
            target.chmod(0o755)
        return target.resolve()


def _expected_asset_name() -> str:
    system = platform.system().lower()
    machine = platform.machine().lower()
    if system != "darwin":
        raise RuntimeError("VitalClaw V1 currently supports macOS-only HealthExport CLI installation.")
    arch = "arm64" if machine in {"arm64", "aarch64"} else "amd64"
    return f"_darwin_{arch}.tar.gz"


def _select_release_asset(release: dict[str, Any]) -> tuple[str, str | None]:
    suffix = _expected_asset_name()
    assets = release.get("assets", [])
    for asset in assets:
        name = asset.get("name")
        if isinstance(name, str) and name.startswith("he_") and name.endswith(suffix):
            return name, asset.get("browser_download_url")
    return suffix, None


def _slugify(value: str) -> str:
    slug = "".join(character if character.isalnum() else "_" for character in value.lower())
    while "__" in slug:
        slug = slug.replace("__", "_")
    return slug.strip("_")
