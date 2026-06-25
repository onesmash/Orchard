"""Tests for the ImpactTraversalPolicy dataclass."""

from orchard.handlers.impact_policy import ImpactTraversalPolicy


class TestImpactTraversalPolicy:
    """Test suite for ImpactTraversalPolicy."""

    def test_default_policy_excludes_low_confidence(self):
        """Default policy has include_low_confidence=False and expected relation_types."""
        policy = ImpactTraversalPolicy()
        assert policy.include_low_confidence is False
        assert policy.relation_types == ["Calls", "References", "Implements"]

    def test_policy_default_max_depth(self):
        """Default max_depth is 5."""
        policy = ImpactTraversalPolicy()
        assert policy.max_depth == 5

    def test_policy_custom_max_depth(self):
        """max_depth can be overridden via constructor."""
        policy = ImpactTraversalPolicy(max_depth=3)
        assert policy.max_depth == 3

    def test_policy_with_bridges(self):
        """effective_relation_types includes BridgesTo when include_bridge_edges=True."""
        policy = ImpactTraversalPolicy()
        result = policy.effective_relation_types()
        assert "BridgesTo" in result

        # BridgesTo should appear once — no duplicate when explicitly listed
        policy2 = ImpactTraversalPolicy(
            relation_types=["Calls", "BridgesTo", "References"],
        )
        result2 = policy2.effective_relation_types()
        assert result2.count("BridgesTo") == 1

        # When include_bridge_edges=False, BridgesTo is not appended
        policy3 = ImpactTraversalPolicy(include_bridge_edges=False)
        result3 = policy3.effective_relation_types()
        assert "BridgesTo" not in result3
