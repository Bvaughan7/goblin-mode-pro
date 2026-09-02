"""Canonical form for a D-Bus interface, so two implementations can be diffed.

`docs/dbus-interface-v1.xml` is the frozen contract for the privileged helper.
The point of freezing it is that a second implementation in another language
has to serve *exactly* the same interface, and that a change to either one
fails the build instead of being discovered by a user whose GUI hangs for 25 s
on a method the daemon no longer replies to.

Raw introspection XML cannot be compared byte for byte across implementations.
GDBus and zbus both emit valid XML for the same interface and neither is
wrong, but they differ in indentation, attribute order, whether the standard
`org.freedesktop.DBus.*` interfaces are appended, and where they put the
newlines. A byte comparison of raw output would fail on the first Rust commit
for reasons that have nothing to do with the contract - and a freeze check
that fails for a cosmetic reason is one that gets regenerated and stops
protecting anything. That is the failure mode the hybrid plan names as fatal.

So the comparison is byte for byte, but on a *canonical* rendering rather than
on whatever the library happened to print:

- one interface, named explicitly - the standard Peer/Introspectable/
  Properties interfaces a bus adds at runtime are dropped
- methods sorted by name, because declaration order carries no meaning in
  D-Bus and sorting lets an implementation declare them however reads best
- arguments in declaration order, because that order *is* the signature
- every argument rendered as name/type/direction, two-space indent, LF endings

`docs/dbus-interface-v1.xml` is itself stored in canonical form, so the file
on disk is the exact byte target both implementations are compared against.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

#: interfaces every bus-connected object gets for free; never part of a contract
_STANDARD = (
    "org.freedesktop.DBus.Peer",
    "org.freedesktop.DBus.Introspectable",
    "org.freedesktop.DBus.Properties",
    "org.freedesktop.DBus.ObjectManager",
)


class InterfaceNotFound(LookupError):
    """The requested interface is not present in the introspection data."""


def _interface_element(xml_text: str, interface: str) -> ET.Element:
    root = ET.fromstring(xml_text)
    for node in root.iter("interface"):
        if node.get("name") == interface:
            return node
    found = [
        n.get("name")
        for n in root.iter("interface")
        if n.get("name") not in _STANDARD
    ]
    raise InterfaceNotFound(
        f"{interface!r} not in this introspection data; it declares {found or 'nothing'}"
    )


def canonicalize(xml_text: str, interface: str) -> str:
    """Render one interface out of `xml_text` in the frozen canonical form."""
    node = _interface_element(xml_text, interface)
    lines = ["<node>", f'  <interface name="{interface}">']
    methods = sorted(node.findall("method"), key=lambda m: m.get("name") or "")
    for method in methods:
        lines.append(f'    <method name="{method.get("name")}">')
        for arg in method.findall("arg"):
            # direction is optional in the spec and defaults to "in"; make it
            # explicit so an implementation that omits it still matches one
            # that spells it out.
            lines.append(
                f'      <arg name="{arg.get("name")}"'
                f' type="{arg.get("type")}"'
                f' direction="{arg.get("direction", "in")}"/>'
            )
        lines.append("    </method>")
    for signal in sorted(node.findall("signal"), key=lambda s: s.get("name") or ""):
        lines.append(f'    <signal name="{signal.get("name")}">')
        for arg in signal.findall("arg"):
            lines.append(
                f'      <arg name="{arg.get("name")}" type="{arg.get("type")}"/>'
            )
        lines.append("    </signal>")
    for prop in sorted(node.findall("property"), key=lambda p: p.get("name") or ""):
        lines.append(
            f'    <property name="{prop.get("name")}"'
            f' type="{prop.get("type")}"'
            f' access="{prop.get("access")}"/>'
        )
    lines += ["  </interface>", "</node>", ""]
    return "\n".join(lines)


def signatures(xml_text: str, interface: str) -> dict[str, tuple[str, str]]:
    """`{method: (in_signature, out_signature)}` for every method on `interface`.

    The conformance suite uses this to assert that a reply carries the exact
    type the contract promises, rather than merely that a reply arrived.
    """
    node = _interface_element(xml_text, interface)
    out: dict[str, tuple[str, str]] = {}
    for method in node.findall("method"):
        ins = "".join(
            a.get("type", "")
            for a in method.findall("arg")
            if a.get("direction", "in") == "in"
        )
        outs = "".join(
            a.get("type", "")
            for a in method.findall("arg")
            if a.get("direction") == "out"
        )
        out[method.get("name") or ""] = (ins, outs)
    return out
