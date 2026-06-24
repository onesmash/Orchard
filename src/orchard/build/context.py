import hashlib
from dataclasses import dataclass
from typing import Literal


@dataclass
class BuildContext:
    build_id: str
    build_system: Literal["xcodebuild", "swift_build", "other"]
    workspace_root: str
    scheme: str | None
    target: str
    configuration: str
    sdk: str
    triple: str
    toolchain_id: str
    derived_data_path: str | None
    index_store_path: str | None
    symbolgraph_output_path: str | None
    commit_sha: str | None
    build_config_hash: str


def make_build_id(ctx: BuildContext) -> str:
    key = f"{ctx.workspace_root}|{ctx.target}|{ctx.configuration}|{ctx.sdk}|{ctx.toolchain_id}|{ctx.commit_sha or ''}"
    digest = hashlib.sha256(key.encode()).hexdigest()[:12]
    return f"build-{digest}"
