import orchard.setup as setup_mod


def test_setup_skill_installs_all_bundled_skills(tmp_path, monkeypatch):
    skills_src = tmp_path / "skills-src"
    expected = {}
    for skill_name in [
        "orchard",
        "orchard-cli",
        "orchard-debugging",
        "orchard-exploring",
        "orchard-impact-analysis",
    ]:
        skill_dir = skills_src / skill_name
        skill_dir.mkdir(parents=True)
        content = f"name: {skill_name}\n"
        (skill_dir / "SKILL.md").write_text(content, encoding="utf-8")
        expected[skill_name] = content

    claude_root = tmp_path / "claude" / "skills"
    agents_root = tmp_path / "agents" / "skills"

    monkeypatch.setattr(setup_mod, "_BUNDLED_SKILL_NAMES", list(expected))
    monkeypatch.setattr(setup_mod, "_SKILL_TARGET_ROOTS", [claude_root, agents_root])
    monkeypatch.setattr(
        setup_mod,
        "_skill_source_dir",
        lambda skill_name: skills_src / skill_name,
    )

    ok, msg = setup_mod._setup_skill()

    assert ok is True
    assert "orchard-impact-analysis" in msg
    for root in [claude_root, agents_root]:
        for skill_name, content in expected.items():
            installed = root / skill_name / "SKILL.md"
            assert installed.exists()
            assert installed.read_text(encoding="utf-8") == content
