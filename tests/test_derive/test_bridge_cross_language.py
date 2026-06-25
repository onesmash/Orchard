"""Tests for CrossLanguageName dataclass."""
from orchard.derive.bridge import CrossLanguageName


class TestCrossLanguageName:
    def test_objc_instance_method(self):
        cn = CrossLanguageName(
            clang_name="-[ZMHomeViewController viewDidLoad]",
            swift_name="ZMHomeViewController.viewDidLoad()",
            definition_language="objc",
        )
        assert cn.definition_name == "-[ZMHomeViewController viewDidLoad]"

    def test_objc_class_method(self):
        cn = CrossLanguageName(
            clang_name="+[ZMNDevice shareInstance]",
            swift_name=None,
            definition_language="objc",
        )
        assert cn.definition_name == "+[ZMNDevice shareInstance]"

    def test_swift_definition(self):
        cn = CrossLanguageName(
            clang_name="-[Zoom.PTEntranceViewController handleMoreSelectedWithTag:withParams:]",
            swift_name="PTEntranceViewController.handleMoreSelected(_:_:)",
            definition_language="swift",
        )
        assert cn.definition_name == "PTEntranceViewController.handleMoreSelected(_:_:)"

    def test_optional_names(self):
        cn = CrossLanguageName(clang_name="-[MyClass method:]", definition_language="objc")
        assert cn.swift_name is None

    def test_repr(self):
        cn = CrossLanguageName(
            clang_name="-[A foo:]", swift_name="A.foo(_:)", definition_language="swift"
        )
        assert "CrossLanguageName" in repr(cn)
