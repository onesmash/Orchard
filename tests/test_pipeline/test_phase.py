"""Tests for pipeline phase protocol and registry."""
from orchard.pipeline.phase import PhaseConfig
from orchard.pipeline.registry import register, get_enabled_phases, clear


class TestPhaseConfig:
    def test_default_deps_empty(self):
        pc = PhaseConfig(name="test")
        assert pc.deps == []

    def test_enabled_when_absent_means_enabled(self):
        clear()
        register(PhaseConfig(name="always_on"))
        phases = get_enabled_phases()
        assert len(phases) == 1

    def test_enabled_when_false_excludes(self):
        clear()
        register(PhaseConfig(name="off", enabled_when=lambda: False))
        assert len(get_enabled_phases()) == 0

    def test_enabled_when_true_includes(self):
        clear()
        register(PhaseConfig(name="on", enabled_when=lambda: True))
        assert len(get_enabled_phases()) == 1
