"""The contract between the profile validator and the extensions.

A profile that uses a published extension has to validate, whatever shape that extension gives its
fields, and a value the extension does not allow has to be rejected. The validator may know only
what the core profile schema defines; the rest comes from each extension's definition. When an
extension is published, or its shape changes, add or update its case here.

tests/testdata/published holds the definitions as published from the extensions repository at 3640fd7.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import yaml

from runtimeconditions_profiler.extension.definition import parse_extension_definition
from runtimeconditions_profiler.profile.validator import ProfileValidator

ROOT = "https://runtimeconditions.io/extensions"
COMMON = f"{ROOT}/rc/common-integrations:v1alpha2"
ENV = f"{ROOT}/rc/env-configuration:v1alpha2"
NATS = f"{ROOT}/nats/service:0.2.0"
S3 = f"{ROOT}/aws/s3:0.2.0"
K8S = f"{ROOT}/kubernetes/api:0.1.0"
GA = f"{ROOT}/google/analytics:0.1.0"
GIT = f"{ROOT}/rc/source-control:0.1.0"

# name, extensions, condition, the text to break and what to break it into
CASES = [
    ("common http operations are objects", [COMMON],
     {"kind": "api", "interface": {"type": "http", "operations": [{"method": "GET", "path": "/orders"}]}}, "GET", "FETCH"),
    ("common smtp operations are strings", [COMMON],
     {"kind": "api", "interface": {"type": "smtp", "operations": ["send"]}}, "send", "receive"),
    ("common relational engine is a string", [COMMON],
     {"kind": "datastore", "interface": {"type": "relational", "engine": "postgres"}}, "postgres", "oracle2"),
    ("env properties depend on the interface", [COMMON, ENV],
     {"kind": "api", "interface": {"type": "smtp", "operations": ["send"]}, "configuration": {"env": [{"property": "tlsMode", "name": "SMTP_TLS"}]}},
     "tlsMode", "database"),
    ("env alternatives", [COMMON, ENV],
     {"kind": "cache", "interface": {"type": "key_value", "engine": "redis"},
      "configuration": {"alternatives": [{"env": [{"property": "url", "name": "A"}]}, {"env": [{"property": "hostname", "name": "B"}]}]}},
     "hostname", "tlsMode"),
    ("nats operations are objects with their own keys", [NATS],
     {"kind": "nats.service", "interface": {"type": "service", "operations": [{"resource": "connection", "action": "connect"}]},
      "configuration": {"env": [{"property": "url", "name": "NATS_URL"}]}}, "url", "bogus"),
    ("aws s3 operations have a name and a role", [S3],
     {"kind": "aws.s3", "interface": {"type": "bucket", "bucketName": "orders",
                                      "operations": [{"name": "AbortMultipartUpload"}, {"name": "CopyObject", "role": "source"}]}},
     "source", "elsewhere"),
    ("kubernetes operations carry a verb and a scope", [K8S],
     {"kind": "kubernetes.api", "interface": {"type": "api", "operations": [
         {"verb": "get", "apiGroup": "", "apiVersion": "v1", "resource": "pods", "scope": "namespaced"}]}}, "get", "grab"),
    ("google analytics events are strings", [GA],
     {"kind": "google.analytics", "interface": {"type": "web", "events": ["page_view"]},
      "configuration": {"env": [{"property": "measurementId", "name": "GA_ID"}]}}, "page_view", "scroll"),
    ("source control access is a list of strings", [GIT],
     {"kind": "rc.source_control", "interface": {"type": "git", "provider": "github", "access": ["clone", "fetch"]}}, "clone", "rewrite"),
]


def _discovery() -> Any:
    artifacts = []
    for path in sorted((Path(__file__).parent / "testdata" / "published").glob("*.yaml")):
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
        identifier, version = document["metadata"]["id"], str(document["metadata"]["version"])
        definition = parse_extension_definition(document, identifier, identifier)
        artifacts.append(SimpleNamespace(id=identifier, version=version, definition=definition))
    return SimpleNamespace(validated_artifacts=[], sdk_extensions=artifacts)


def _messages(extensions: list[str], conditions: list[dict[str, Any]]) -> list[str]:
    profile = {
        "apiVersion": "runtimeconditions.io/v1alpha1",
        "kind": "RuntimeConditionsProfile",
        "extensions": extensions,
        "conditions": conditions,
    }
    return [item.message for item in ProfileValidator().validate(profile, _discovery())]


def _replace(value: Any, old: str, new: str) -> Any:
    if isinstance(value, dict):
        return {key: _replace(item, old, new) for key, item in value.items()}
    if isinstance(value, list):
        return [_replace(item, old, new) for item in value]
    return new if value == old else value


@pytest.mark.parametrize("name, extensions, condition, old, new", CASES, ids=[case[0] for case in CASES])
def test_a_published_extension_validates_its_own_shape(name, extensions, condition, old, new) -> None:
    assert _messages(extensions, [condition]) == []
    broken = _replace(condition, old, new)
    assert broken != condition
    assert _messages(extensions, [broken]) != []


def test_one_profile_may_use_several_extensions_with_different_shapes() -> None:
    conditions = [case[2] for case in CASES if case[1] in ([COMMON], [NATS], [S3], [GIT])]
    assert _messages([COMMON, NATS, S3, GIT], conditions) == []
