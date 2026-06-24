from orchard.build.context import BuildContext, make_build_id


def test_build_context_fields():
    ctx = BuildContext(
        build_id="",
        build_system="xcodebuild",
        workspace_root="/tmp/MyApp",
        scheme="MyApp",
        target="MyApp",
        configuration="Debug",
        sdk="iphonesimulator17.5",
        triple="arm64-apple-ios17.5-simulator",
        toolchain_id="com.apple.dt.toolchain.XcodeDefault",
        derived_data_path="/tmp/DerivedData",
        index_store_path=None,
        symbolgraph_output_path=None,
        commit_sha=None,
        build_config_hash="",
    )
    assert ctx.build_system == "xcodebuild"
    assert ctx.target == "MyApp"


def test_make_build_id_stable():
    ctx = BuildContext(
        build_id="",
        build_system="swift_build",
        workspace_root="/tmp/pkg",
        scheme=None,
        target="MyLib",
        configuration="release",
        sdk="macosx14.5",
        triple="arm64-apple-macosx14.5",
        toolchain_id="swift-5.10",
        derived_data_path=None,
        index_store_path=None,
        symbolgraph_output_path=None,
        commit_sha="abc123",
        build_config_hash="",
    )
    bid = make_build_id(ctx)
    assert bid.startswith("build-")
    assert make_build_id(ctx) == bid  # stable
