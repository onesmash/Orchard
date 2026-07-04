"""Tests for .swiftinterface parser."""
from orchard.ingest.swiftinterface import parse_interface_text, InterfaceConformance


SAMPLE = """
// swift-interface-format-version: 1.0
// swift-compiler-version: Apple Swift version 6.3
import Swift
import SwiftUI

@objc(ZMBizEntranceTag) public enum BusinessEntranceTag : Swift.UInt, Swift.Sendable {
  case none
  case meetings
}

public struct LoginViewModel : ObservableObject, Identifiable {
  public let id: String
  public func login() {}
}

public class BaseController : UIViewController, LoginServiceProtocol {
  public func handleLogin() {}
}

public protocol LoginServiceProtocol {
  func login()
}

public extension String : MyCustomProtocol {
  func extra() {}
}
"""


def test_parse_enum_conformances():
    results = parse_interface_text(SAMPLE, "MyLogin")
    # enum BusinessEntranceTag : UInt, Sendable
    # UInt is not a protocol → filtered out
    # Sendable is a protocol
    conforms = [r for r in results if r.type_name == "BusinessEntranceTag"]
    assert len(conforms) == 1
    assert conforms[0].protocol_name == "Sendable"
    assert conforms[0].type_kind == "enum"
    assert conforms[0].module == "MyLogin"


def test_parse_struct_multiple_conformances():
    results = parse_interface_text(SAMPLE, "MyLogin")
    conforms = [r for r in results if r.type_name == "LoginViewModel"]
    protos = {r.protocol_name for r in conforms}
    assert protos == {"ObservableObject", "Identifiable"}


def test_parse_class_with_superclass_and_protocol():
    results = parse_interface_text(SAMPLE, "MyLogin")
    conforms = [r for r in results if r.type_name == "BaseController"]
    protos = {r.protocol_name for r in conforms}
    # UIViewController is superclass → filtered out
    # LoginServiceProtocol is protocol → kept
    assert protos == {"LoginServiceProtocol"}


def test_parse_protocol_no_conformances():
    results = parse_interface_text(SAMPLE, "MyLogin")
    conforms = [r for r in results if r.type_name == "LoginServiceProtocol"]
    assert len(conforms) == 0  # protocol doesn't inherit from anything here


def test_parse_extension_conformance():
    results = parse_interface_text(SAMPLE, "MyLogin")
    conforms = [r for r in results if r.type_name == "String"]
    assert len(conforms) == 1
    assert conforms[0].protocol_name == "MyCustomProtocol"


def test_empty_text():
    assert parse_interface_text("", "M") == []


def test_no_conformances():
    assert parse_interface_text("public struct Foo { }", "M") == []
