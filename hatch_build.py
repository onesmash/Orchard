from __future__ import annotations

import platform
import subprocess
import sys
from pathlib import Path

from hatchling.builders.hooks.plugin.interface import BuildHookInterface


class CustomBuildHook(BuildHookInterface):
    PLUGIN_NAME = "custom"

    def initialize(self, version: str, build_data: dict) -> None:
        if self.target_name != "wheel":
            return

        root = Path(self.root)

        # Always include the bundled skills in the wheel.
        skills_src = root / "skills"
        if skills_src.is_dir():
            build_data.setdefault("force_include", {})[str(skills_src)] = (
                "orchard/skills"
            )

        system = platform.system().lower()
        machine = platform.machine().lower()
        if system != "darwin" or machine not in {"arm64", "aarch64"}:
            print(
                f"Skipping packaged orchard-indexstore-reader for unsupported build platform: "
                f"{platform.system()} {platform.machine()}",
                file=sys.stderr,
            )
            return

        pkg = root / "swift" / "orchard-indexstore-reader"
        print("Building packaged orchard-indexstore-reader (release)...", file=sys.stderr)
        subprocess.run(
            ["swift", "build", "-c", "release", "--package-path", str(pkg)],
            check=True,
        )

        binary = pkg / ".build" / "release" / "orchard-indexstore-reader"
        if not binary.exists():
            raise FileNotFoundError(f"Expected build output not found: {binary}")

        build_data["pure_python"] = False
        build_data["tag"] = "py3-none-macosx_11_0_arm64"
        build_data.setdefault("force_include", {})[str(binary)] = (
            "orchard/_bin/darwin-arm64/orchard-indexstore-reader"
        )
