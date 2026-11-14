from __future__ import annotations

from typing import Any

from ..constants import API_VERSION
from ..extension.identity import expand_identifier, parse_identifier
from ..extension import ExtensionVocabulary
from ..models import Diagnostic, DiscoveryResult
from ..util import add_extension_closure, as_map


class ProfileValidator:
    def validate(self, profile: dict[str, Any], discovery: DiscoveryResult) -> list[Diagnostic]:
        diagnostics: list[Diagnostic] = []
        add = lambda msg: diagnostics.append(Diagnostic("error", "profile-validation", "profile", msg))
        if profile.get("apiVersion") != API_VERSION:
            add(f"apiVersion must be {API_VERSION}")
        if profile.get("kind") != "RuntimeConditionsProfile":
            add("kind must be RuntimeConditionsProfile")
        try:
            extensions = [parse_identifier(ref) for ref in profile.get("extensions", [])]
        except Exception as exc:
            add(str(exc))
            extensions = []
        conditions = profile.get("conditions") if isinstance(profile.get("conditions"), list) else []
        if not extensions and conditions:
            add("extensions must declare the extension dependency closure used by conditions")

        definitions = {
            artifact.reference: artifact.extension_definition
            for artifact in discovery.validated_artifacts
            if artifact.extension_id and artifact.extension_definition
        }
        definitions.update({(artifact.id, artifact.version): artifact.definition for artifact in discovery.sdk_extensions})
        # A profile may name an extension by its short <url>:<version> reference.
        extensions = [
            (expand_identifier(*ref), ref[1]) if ref not in definitions and (expand_identifier(*ref), ref[1]) in definitions else ref
            for ref in extensions
        ]
        declared: set[tuple[str, str]] = set()
        for extension in extensions:
            if extension in declared:
                add(f"duplicate extension id {extension}")
            declared.add(extension)
            if extension not in definitions:
                add(f"missing extension definition for {extension}")
        closure: set[tuple[str, str]] = set()
        for extension in extensions:
            add_extension_closure(extension, {key: value.dependencies for key, value in definitions.items()}, closure)
        for extension in closure:
            if extension not in declared:
                add(f"extensions missing dependency {extension}")
        vocabulary = ExtensionVocabulary([definitions[item] for item in closure if item in definitions])
        for key, count in vocabulary.counts().items():
            if count > 1:
                add(f"resolved extension set contains vocabulary conflict for {key}")
        for conflict in vocabulary.condition_field_conflicts():
            add(f"resolved extension set contains vocabulary conflict for {conflict}")
        for index, condition in enumerate(conditions):
            if isinstance(condition, dict):
                validate_condition(index, vocabulary, condition, add)
            else:
                add(f"conditions[{index}] must be a mapping")
        return diagnostics


# The fields the core profile schema defines. Every other field of a condition is
# defined by an extension, whose definition says what it may hold.
CORE_CONDITION_FIELDS = {"name", "optional", "kind", "extension", "interface"}
CORE_INTERFACE_FIELDS = {"type"}


def values_at(root: Any, path: str) -> list[tuple[str, Any]]:
    """Return every value a condition holds at a field path such as
    ``interface.operations[].method``, each with the path that reaches it. A segment
    ending in ``[]`` walks the elements of a list."""
    found: list[tuple[str, Any]] = [("", root)]
    for segment in path.split("."):
        name, is_list = (segment[:-2], True) if segment.endswith("[]") else (segment, False)
        following: list[tuple[str, Any]] = []
        for current_path, current in found:
            if not isinstance(current, dict) or name not in current:
                continue
            here = f"{current_path}.{name}" if current_path else name
            value = current[name]
            if not is_list:
                following.append((here, value))
            elif isinstance(value, list):
                following.extend((f"{here}[{index}]", item) for index, item in enumerate(value))
        found = following
    return found


def validate_condition(
    index: int,
    vocabulary: ExtensionVocabulary,
    condition: dict[str, Any],
    add: Any,
) -> None:
    """Check one condition as plain data against its extensions' definitions. It knows only
    what the core schema defines; the shape of everything else comes from the extensions."""
    prefix = f"conditions[{index}]"
    kind = str(condition.get("kind", ""))
    interface = as_map(condition.get("interface"))
    interface_type = str(interface.get("type", ""))
    expect_profile_count(vocabulary.kind_count(kind), f"{prefix}.kind {kind}", add)
    expect_profile_count(
        vocabulary.interface_type_count(kind, interface_type),
        f"{prefix}.interface.type {kind}/{interface_type}",
        add,
    )
    # A field some extension defines has to be defined for this kind and interface type.
    for name in sorted(interface):
        if name not in CORE_INTERFACE_FIELDS and vocabulary.interface_field_declared(name):
            expect_profile_count(
                vocabulary.interface_field_count(kind, interface_type, name),
                f"{prefix}.interface.{name} for {kind}/{interface_type}",
                add,
            )
    for name in sorted(condition):
        if name not in CORE_CONDITION_FIELDS and vocabulary.condition_field_declared(name):
            expect_profile_count(
                vocabulary.condition_field_count(kind, interface_type, name),
                f"{prefix}.{name} for {kind}/{interface_type}",
                add,
            )
    # Where extensions enumerate the values of a field, every value found there must be one.
    for field in vocabulary.field_value_fields(kind, interface_type):
        for path, value in values_at(condition, field):
            if isinstance(value, str):
                expect_profile_count(
                    vocabulary.field_value_count(field, kind, interface_type, value),
                    f"{prefix}.{path} {value} for {kind}/{interface_type}",
                    add,
                )


def expect_profile_count(count: int, message: str, add: Any) -> None:
    if count != 1:
        add(f"{message}: expected exactly one definition, got {count}")
