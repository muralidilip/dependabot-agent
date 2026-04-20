"""Unit tests for the Maven Central version lookup tool."""

from __future__ import annotations

import json
from urllib.error import HTTPError, URLError

import pytest

import dependabot_agent.tools.maven_central as maven_central
from dependabot_agent.tools.maven_central import (
    _filter_candidates,
    _is_pre_release,
    _parse_version,
    _query_metadata_xml,
    _query_search_api,
    lookup_absolute_latest_version,
    lookup_latest_version,
)


# ── Helpers ──────────────────────────────────────────────────────────────

class _FakeResponse:
    """Minimal context-manager that mimics urlopen's response."""

    def __init__(self, payload: bytes) -> None:
        self._payload = payload

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        return False

    def read(self) -> bytes:
        return self._payload


def _maven_search_response(*versions: str) -> bytes:
    """Build a fake Maven Central solrsearch JSON payload."""
    docs = [{"v": v, "g": "org.example", "a": "example"} for v in versions]
    return json.dumps({"response": {"numFound": len(docs), "docs": docs}}).encode()


def _maven_metadata_xml(*versions: str) -> bytes:
    """Build a fake maven-metadata.xml payload."""
    version_elements = "\n".join(f"      <version>{v}</version>" for v in versions)
    latest = versions[-1] if versions else ""
    return f"""\
<?xml version="1.0" encoding="UTF-8"?>
<metadata>
  <groupId>org.example</groupId>
  <artifactId>example</artifactId>
  <versioning>
    <latest>{latest}</latest>
    <release>{latest}</release>
    <versions>
{version_elements}
    </versions>
  </versioning>
</metadata>""".encode()


# ── _parse_version ───────────────────────────────────────────────────────

class TestParseVersion:
    def test_simple_semver(self) -> None:
        assert _parse_version("3.4.1") == (3, 4, 1)

    def test_two_segment(self) -> None:
        assert _parse_version("2.25") == (2, 25)

    def test_four_segment_with_qualifier(self) -> None:
        assert _parse_version("4.1.115.Final") == (4, 1, 115)

    def test_long_version(self) -> None:
        assert _parse_version("2.25.10") == (2, 25, 10)

    def test_single_segment_returns_none(self) -> None:
        assert _parse_version("3") is None

    def test_non_numeric_returns_none(self) -> None:
        assert _parse_version("abc") is None

    def test_empty_returns_none(self) -> None:
        assert _parse_version("") is None


# ── _is_pre_release ─────────────────────────────────────────────────────

class TestIsPreRelease:
    @pytest.mark.parametrize("version", [
        "3.0.0-alpha", "3.0.0-beta1", "3.0.0-RC1", "3.0.0-rc2",
        "3.0.0.M1", "3.0.0-SNAPSHOT", "3.0.0-dev", "3.0.0-preview",
        "3.0.0-CR1", "3.0.0-ea",
    ])
    def test_detects_pre_release(self, version: str) -> None:
        assert _is_pre_release(version) is True

    @pytest.mark.parametrize("version", [
        "3.4.1", "3.5.0", "4.1.115.Final", "2.25.10", "1.0.0.RELEASE",
    ])
    def test_stable_versions(self, version: str) -> None:
        assert _is_pre_release(version) is False


# ── _filter_candidates ──────────────────────────────────────────────────

class TestFilterCandidates:
    def test_keeps_same_major(self) -> None:
        versions = ["3.4.0", "3.5.1", "4.0.0", "2.9.0"]
        result = _filter_candidates(versions, "3.2.0", (3, 2, 0))
        version_strs = [v for _, v in result]
        assert "3.4.0" in version_strs
        assert "3.5.1" in version_strs
        assert "4.0.0" not in version_strs
        assert "2.9.0" not in version_strs

    def test_excludes_pre_release_for_stable_current(self) -> None:
        versions = ["3.5.0", "3.6.0-RC1", "3.6.0-SNAPSHOT"]
        result = _filter_candidates(versions, "3.4.0", (3, 4, 0))
        version_strs = [v for _, v in result]
        assert "3.5.0" in version_strs
        assert "3.6.0-RC1" not in version_strs
        assert "3.6.0-SNAPSHOT" not in version_strs

    def test_includes_pre_release_when_current_is_pre(self) -> None:
        versions = ["3.5.0-RC1", "3.5.0-RC2", "3.4.0"]
        result = _filter_candidates(versions, "3.5.0-RC1", (3, 5, 0))
        version_strs = [v for _, v in result]
        assert "3.5.0-RC1" in version_strs
        assert "3.5.0-RC2" in version_strs
        assert "3.4.0" in version_strs

    def test_empty_list(self) -> None:
        assert _filter_candidates([], "3.2.0", (3, 2, 0)) == []

    def test_no_matching_major(self) -> None:
        versions = ["4.0.0", "4.1.0"]
        result = _filter_candidates(versions, "3.2.0", (3, 2, 0))
        assert result == []


# ── lookup_latest_version (Spring Boot via plugin ID) ────────────────────

def _mock_query(version_map: dict[str, list[str]]):
    """Return a fake _query_maven_central that returns versions based on artifact_id."""
    def fake(group_id: str, artifact_id: str) -> list[str] | None:
        return version_map.get(artifact_id)
    return fake


class TestLookupSpringBoot:
    """Tests that the Spring Boot Gradle plugin ID resolves correctly
    by trying candidate artifacts in order."""

    def test_finds_upgrade_via_starter_parent(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """First candidate (spring-boot-starter-parent) has matching versions."""
        call_log: list[str] = []
        original_mock = _mock_query({
            "spring-boot-starter-parent": ["4.0.3", "4.0.2", "4.0.1", "4.0.0", "3.5.3"],
        })
        def tracking_mock(g: str, a: str) -> list[str] | None:
            call_log.append(a)
            return original_mock(g, a)

        monkeypatch.setattr(maven_central, "_query_maven_central", tracking_mock)

        result = lookup_latest_version("org.springframework.boot", "", "4.0.0")

        assert result["upgrade_available"] is True
        assert result["latest_version"] == "4.0.3"
        assert result["current_version"] == "4.0.0"
        assert result["artifact_id"] == "spring-boot-starter-parent"
        assert result["group_id"] == "org.springframework.boot"
        # Should NOT have tried other candidates
        assert len(call_log) == 1
        assert call_log[0] == "spring-boot-starter-parent"

    def test_falls_back_to_spring_boot_core(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """starter-parent returns no 4.x → falls back to spring-boot artifact."""
        call_log: list[str] = []
        original_mock = _mock_query({
            "spring-boot-starter-parent": ["3.5.3", "3.5.2"],
            "spring-boot": ["4.0.2", "4.0.1", "4.0.0"],
        })
        def tracking_mock(g: str, a: str) -> list[str] | None:
            call_log.append(a)
            return original_mock(g, a)

        monkeypatch.setattr(maven_central, "_query_maven_central", tracking_mock)

        result = lookup_latest_version("org.springframework.boot", "", "4.0.0")

        assert result["upgrade_available"] is True
        assert result["latest_version"] == "4.0.2"
        assert result["artifact_id"] == "spring-boot"
        assert len(call_log) == 2  # tried starter-parent, then spring-boot

    def test_falls_back_to_gradle_plugin(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Both starter-parent and spring-boot return no 4.x → falls back
        to spring-boot-gradle-plugin."""
        monkeypatch.setattr(maven_central, "_query_maven_central", _mock_query({
            "spring-boot-starter-parent": ["3.5.3", "3.5.2"],
            "spring-boot": ["3.5.3", "3.5.2"],
            "spring-boot-gradle-plugin": ["4.0.1", "4.0.0"],
        }))

        result = lookup_latest_version("org.springframework.boot", "", "4.0.0")

        assert result["upgrade_available"] is True
        assert result["latest_version"] == "4.0.1"
        assert result["artifact_id"] == "spring-boot-gradle-plugin"

    def test_3x_resolved_from_starter_parent(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Spring Boot 3.x should also resolve via starter-parent as first candidate."""
        monkeypatch.setattr(maven_central, "_query_maven_central", _mock_query({
            "spring-boot-starter-parent": ["3.5.3", "3.5.2", "3.5.1", "3.4.7", "3.2.0"],
        }))

        result = lookup_latest_version("org.springframework.boot", "", "3.2.0")

        assert result["upgrade_available"] is True
        assert result["latest_version"] == "3.5.3"
        assert result["artifact_id"] == "spring-boot-starter-parent"
        assert "3.5.3" in result["same_major_versions"]
        assert "3.5.2" in result["same_major_versions"]

    def test_no_upgrade_available(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Already on the latest version → upgrade_available is False."""
        monkeypatch.setattr(maven_central, "_query_maven_central", _mock_query({
            "spring-boot-starter-parent": ["4.0.0"],
        }))

        result = lookup_latest_version("org.springframework.boot", "", "4.0.0")

        assert result["upgrade_available"] is False
        assert result["latest_version"] == "4.0.0"

    def test_skips_pre_release_versions(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Pre-release versions should NOT be offered as upgrades for a stable current version."""
        monkeypatch.setattr(maven_central, "_query_maven_central", _mock_query({
            "spring-boot-starter-parent": ["4.1.0-RC1", "4.1.0-M2", "4.1.0-SNAPSHOT", "4.0.0"],
            "spring-boot": ["4.1.0-RC1", "4.1.0-M2", "4.1.0-SNAPSHOT", "4.0.0"],
            "spring-boot-gradle-plugin": ["4.1.0-RC1", "4.0.0"],
        }))

        result = lookup_latest_version("org.springframework.boot", "", "4.0.0")

        assert result["upgrade_available"] is False
        assert result["latest_version"] == "4.0.0"
        assert "4.1.0-RC1" not in result.get("same_major_versions", [])

    def test_no_major_version_upgrade(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Versions from a different major should NEVER be returned."""
        monkeypatch.setattr(maven_central, "_query_maven_central", _mock_query({
            "spring-boot-starter-parent": ["4.0.0", "3.5.3", "3.4.7"],
        }))

        result = lookup_latest_version("org.springframework.boot", "", "3.4.0")

        assert result["upgrade_available"] is True
        assert result["latest_version"] == "3.5.3"
        # Must NOT jump to 4.x
        assert not result["latest_version"].startswith("4")


# ── lookup_latest_version (direct Maven coordinates) ─────────────────────

class TestLookupDirectCoordinates:
    def test_aws_sdk_bom(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(maven_central, "_query_maven_central", _mock_query({
            "bom": ["2.34.0", "2.33.13", "2.25.10"],
        }))

        result = lookup_latest_version("software.amazon.awssdk", "bom", "2.25.10")

        assert result["upgrade_available"] is True
        assert result["latest_version"] == "2.34.0"
        assert result["group_id"] == "software.amazon.awssdk"
        assert result["artifact_id"] == "bom"

    def test_openhtmltopdf(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(maven_central, "_query_maven_central", _mock_query({
            "openhtmltopdf-pdfbox": ["1.1.24", "1.1.22", "1.0.10"],
        }))

        result = lookup_latest_version(
            "com.openhtmltopdf", "openhtmltopdf-pdfbox", "1.0.10"
        )

        assert result["upgrade_available"] is True
        assert result["latest_version"] == "1.1.24"


# ── Error handling ──────────────────────────────────────────────────────

class TestErrorHandling:
    def test_all_queries_fail_returns_current_version(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When all Maven Central queries fail, return current version."""
        def always_none(g: str, a: str) -> list[str] | None:
            return None

        monkeypatch.setattr(maven_central, "_query_maven_central", always_none)

        result = lookup_latest_version("org.springframework.boot", "", "3.2.0")

        assert result["upgrade_available"] is False
        assert result["latest_version"] == "3.2.0"
        assert "error" in result

    def test_network_timeout_in_metadata_xml(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """_query_metadata_xml handles timeout gracefully."""
        def fake_urlopen(request, timeout: int) -> _FakeResponse:
            raise TimeoutError("The read operation timed out")

        monkeypatch.setattr(maven_central, "urlopen", fake_urlopen)

        result = _query_metadata_xml("org.example", "lib")
        assert result is None

    def test_http_error_in_search_api(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """_query_search_api handles HTTP errors gracefully."""
        import io

        def fake_urlopen(request, timeout: int) -> _FakeResponse:
            raise HTTPError(
                request.full_url, 503, "Service Unavailable",
                hdrs=None, fp=io.BytesIO(b""),
            )

        monkeypatch.setattr(maven_central, "urlopen", fake_urlopen)

        result = _query_search_api("org.example", "lib")
        assert result is None

    def test_unparseable_current_version(self) -> None:
        result = lookup_latest_version("org.example", "foo", "not-a-version")

        assert result["upgrade_available"] is False
        assert result["latest_version"] == "not-a-version"
        assert "error" in result

    def test_empty_api_response(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(maven_central, "_query_maven_central", _mock_query({}))

        result = lookup_latest_version("org.example", "nonexistent", "1.0.0")

        assert result["upgrade_available"] is False
        assert result["latest_version"] == "1.0.0"
        assert result["same_major_versions"] == []


# ── _query_maven_central (metadata XML → search API fallback) ───────────

class TestQueryMavenCentral:
    def test_prefers_metadata_xml(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Should use metadata XML results when available."""
        monkeypatch.setattr(maven_central, "_query_metadata_xml",
                            lambda g, a: ["4.0.5", "4.0.4", "4.0.0"])
        monkeypatch.setattr(maven_central, "_query_search_api",
                            lambda g, a: ["3.5.3"])  # should NOT be used

        from dependabot_agent.tools.maven_central import _query_maven_central
        result = _query_maven_central("org.springframework.boot", "spring-boot-starter-parent")

        assert "4.0.5" in result
        assert "3.5.3" not in result

    def test_falls_back_to_search_api(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Should fall back to search API when metadata XML returns None."""
        monkeypatch.setattr(maven_central, "_query_metadata_xml",
                            lambda g, a: None)
        monkeypatch.setattr(maven_central, "_query_search_api",
                            lambda g, a: ["3.5.3", "3.5.2"])

        from dependabot_agent.tools.maven_central import _query_maven_central
        result = _query_maven_central("org.example", "lib")

        assert result == ["3.5.3", "3.5.2"]

    def test_falls_back_on_empty_metadata(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Should fall back when metadata XML returns empty list."""
        monkeypatch.setattr(maven_central, "_query_metadata_xml",
                            lambda g, a: [])
        monkeypatch.setattr(maven_central, "_query_search_api",
                            lambda g, a: ["2.0.0", "1.5.0"])

        from dependabot_agent.tools.maven_central import _query_maven_central
        result = _query_maven_central("org.example", "lib")

        assert result == ["2.0.0", "1.5.0"]

    def test_metadata_xml_parses_correctly(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Verify _query_metadata_xml correctly parses XML."""
        xml_payload = _maven_metadata_xml("4.0.0", "4.0.1", "4.0.5")

        def fake_urlopen(request, timeout: int) -> _FakeResponse:
            assert "maven-metadata.xml" in request.full_url
            return _FakeResponse(xml_payload)

        monkeypatch.setattr(maven_central, "urlopen", fake_urlopen)

        result = _query_metadata_xml("org.example", "example")
        assert result == ["4.0.0", "4.0.1", "4.0.5"]


# ── same_major_versions list ─────────────────────────────────────────────

class TestSameMajorVersionsList:
    def test_returns_top_10_sorted_descending(self, monkeypatch: pytest.MonkeyPatch) -> None:
        all_versions = [f"3.{minor}.0" for minor in range(15)]  # 3.0.0 .. 3.14.0

        monkeypatch.setattr(maven_central, "_query_maven_central", _mock_query({
            "lib": all_versions,
        }))

        result = lookup_latest_version("org.example", "lib", "3.0.0")

        assert len(result["same_major_versions"]) == 10
        assert result["same_major_versions"][0] == "3.14.0"
        assert result["same_major_versions"][-1] == "3.5.0"


# ── lookup_absolute_latest_version ────────────────────────────────────────

class TestLookupAbsoluteLatestVersion:
    """Tests for lookup_absolute_latest_version which gets latest version regardless of major."""

    def test_returns_absolute_latest(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Should return the highest version across all major versions."""
        monkeypatch.setattr(maven_central, "_query_maven_central", _mock_query({
            "thymeleaf": ["2.1.0", "3.0.0", "3.1.0", "3.1.4.RELEASE"],
        }))

        result = lookup_absolute_latest_version("org.thymeleaf", "thymeleaf")

        assert result["found"] is True
        assert result["latest_version"] == "3.1.4.RELEASE"
        assert result["group_id"] == "org.thymeleaf"
        assert result["artifact_id"] == "thymeleaf"

    def test_skips_pre_release_versions(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Should exclude pre-release versions even when they're newer."""
        monkeypatch.setattr(maven_central, "_query_maven_central", _mock_query({
            "lib": ["3.0.0", "3.1.0", "4.0.0-beta1", "4.0.0-RC1"],
        }))

        result = lookup_absolute_latest_version("org.example", "lib")

        assert result["found"] is True
        assert result["latest_version"] == "3.1.0"  # Highest stable version

    def test_returns_not_found_on_query_failure(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Should return found=False when artifact doesn't exist."""
        monkeypatch.setattr(maven_central, "_query_maven_central", lambda g, a: None)

        result = lookup_absolute_latest_version("org.nonexistent", "unknown")

        assert result["found"] is False
        assert result["latest_version"] == ""
        assert "error" in result

    def test_returns_not_found_when_only_pre_releases(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Should return found=False when only pre-release versions exist."""
        monkeypatch.setattr(maven_central, "_query_maven_central", _mock_query({
            "lib": ["1.0.0-alpha", "1.0.0-beta", "1.0.0-RC1"],
        }))

        result = lookup_absolute_latest_version("org.example", "lib")

        assert result["found"] is False
        assert result["latest_version"] == ""
        assert "No stable versions found" in result.get("error", "")


