"""Test binary search upgrades functionality."""

import pytest
from unittest.mock import patch, MagicMock

from dependabot_agent.nodes.binary_search import binary_search_upgrades_node
from dependabot_agent.helpers.build_file import (
    apply_upgrades_to_content,
    version_already_applied,
)
from dependabot_agent.state import AgentState


class TestBinarySearchUpgrades:
    """Test the binary search upgrade identification logic."""

    def _make_state(
        self,
        planned_upgrades: list,
        pending_indices: list,
        good_upgrades: list = None,
        bad_upgrades: list = None,
        deferred_indices: list = None,
        build_output: str = "",
    ) -> AgentState:
        """Helper to create a test state."""
        return {
            "messages": [],
            "repo": "test/repo",
            "workspace": "/tmp/test-workspace",
            "build_system": "gradle",
            "build_file": "/tmp/test-workspace/build.gradle",
            "original_build_content": "plugins { id 'java' }",
            "current_build_content": "plugins { id 'java' }",
            "alerts": [],
            "dependency_tree": "",
            "planned_upgrades": planned_upgrades,
            "good_upgrades": good_upgrades or [],
            "bad_upgrades": bad_upgrades or [],
            "pending_upgrade_indices": pending_indices,
            "deferred_upgrade_indices": deferred_indices or [],
            "tested_upgrade_sets": [],
            "build_success": False,
            "build_output": build_output,
            "upgrade_attempt_count": 0,
            "exclusion_attempt_count": 0,
            "max_exclusion_retries": 3,
            "no_upgrades_possible": False,
            "has_changes": False,
            "error": "",
            "pr_url": "",
            "remaining_vulnerabilities": [],
            "exhausted_upgrades": [],
            "verified_clean": False,
            "verification_attempt_count": 0,
        }

    def test_empty_pending_no_deferred(self):
        """When pending is empty and no deferred, binary search is complete."""
        state = self._make_state(
            planned_upgrades=[{"group_id": "org.test", "artifact_id": "test", "current_version": "1.0", "target_version": "1.1"}],
            pending_indices=[],
            deferred_indices=[],
        )

        result = binary_search_upgrades_node(state)

        assert result["pending_upgrade_indices"] == []
        assert "complete" in result["messages"][0].content.lower()

    def test_empty_pending_with_deferred(self):
        """When pending is empty but deferred exists, process deferred."""
        state = self._make_state(
            planned_upgrades=[
                {"group_id": "org.test", "artifact_id": "a", "current_version": "1.0", "target_version": "1.1"},
                {"group_id": "org.test", "artifact_id": "b", "current_version": "1.0", "target_version": "1.1"},
            ],
            pending_indices=[],
            deferred_indices=[0, 1],
        )

        result = binary_search_upgrades_node(state)

        assert result["pending_upgrade_indices"] == [0, 1]
        assert result["deferred_upgrade_indices"] == []
        assert "deferred" in result["messages"][0].content.lower()

    def test_single_pending_marked_as_bad(self):
        """When only one upgrade pending and validation failed, mark as bad."""
        upgrade = {"group_id": "org.test", "artifact_id": "bad-lib", "current_version": "1.0", "target_version": "2.0"}
        state = self._make_state(
            planned_upgrades=[upgrade],
            pending_indices=[0],
            build_output="Error: incompatible types",
        )

        result = binary_search_upgrades_node(state)

        assert len(result["bad_upgrades"]) == 1
        assert result["bad_upgrades"][0]["artifact_id"] == "bad-lib"
        assert "Error: incompatible" in result["bad_upgrades"][0]["failure_reason"]
        assert result["pending_upgrade_indices"] == []

    @patch("dependabot_agent.nodes.binary_search.run_compile_only")
    @patch("dependabot_agent.nodes.binary_search.write_build_file")
    @patch("dependabot_agent.nodes.binary_search.revert_build_file")
    def test_first_half_passes(self, mock_revert, mock_write, mock_compile):
        """When first half compiles successfully, mark as good and continue with second half."""
        mock_compile.invoke.return_value = {"success": True, "stdout": "BUILD SUCCESS"}
        mock_write.invoke.return_value = {"status": "ok"}
        mock_revert.invoke.return_value = {"status": "reverted"}

        upgrades = [
            {"group_id": "org.test", "artifact_id": "a", "current_version": "1.0", "target_version": "1.1"},
            {"group_id": "org.test", "artifact_id": "b", "current_version": "1.0", "target_version": "1.1"},
            {"group_id": "org.test", "artifact_id": "c", "current_version": "1.0", "target_version": "1.1"},
            {"group_id": "org.test", "artifact_id": "d", "current_version": "1.0", "target_version": "1.1"},
        ]
        state = self._make_state(
            planned_upgrades=upgrades,
            pending_indices=[0, 1, 2, 3],
        )

        result = binary_search_upgrades_node(state)

        # First half [0,1] should be marked as good
        assert len(result["good_upgrades"]) == 2
        assert result["good_upgrades"][0]["artifact_id"] == "a"
        assert result["good_upgrades"][1]["artifact_id"] == "b"
        # Second half [2,3] should be pending
        assert result["pending_upgrade_indices"] == [2, 3]

    @patch("dependabot_agent.nodes.binary_search.run_compile_only")
    @patch("dependabot_agent.nodes.binary_search.write_build_file")
    @patch("dependabot_agent.nodes.binary_search.revert_build_file")
    def test_first_half_fails(self, mock_revert, mock_write, mock_compile):
        """When first half fails, narrow down and defer second half."""
        mock_compile.invoke.return_value = {"success": False, "stdout": "COMPILATION FAILED"}
        mock_write.invoke.return_value = {"status": "ok"}
        mock_revert.invoke.return_value = {"status": "reverted"}

        upgrades = [
            {"group_id": "org.test", "artifact_id": "a", "current_version": "1.0", "target_version": "1.1"},
            {"group_id": "org.test", "artifact_id": "b", "current_version": "1.0", "target_version": "1.1"},
            {"group_id": "org.test", "artifact_id": "c", "current_version": "1.0", "target_version": "1.1"},
            {"group_id": "org.test", "artifact_id": "d", "current_version": "1.0", "target_version": "1.1"},
        ]
        state = self._make_state(
            planned_upgrades=upgrades,
            pending_indices=[0, 1, 2, 3],
        )

        result = binary_search_upgrades_node(state)

        # First half [0,1] should be pending (to narrow down)
        assert result["pending_upgrade_indices"] == [0, 1]
        # Second half [2,3] should be deferred
        assert result["deferred_upgrade_indices"] == [2, 3]

    @patch("dependabot_agent.nodes.binary_search.run_compile_only")
    @patch("dependabot_agent.nodes.binary_search.write_build_file")
    @patch("dependabot_agent.nodes.binary_search.revert_build_file")
    def test_full_binary_search_scenario(self, mock_revert, mock_write, mock_compile):
        """Simulate a full binary search where upgrade #1 is bad."""
        # Upgrade 1 (index 1) is bad, others are good
        def compile_side_effect(args):
            # Check which upgrades are being tested based on previous write
            # For simplicity, we'll track via call count
            call_count = mock_compile.invoke.call_count
            if call_count == 1:
                # Testing [0,1] - should fail because 1 is bad
                return {"success": False, "stdout": "FAIL"}
            elif call_count == 2:
                # Testing [0] - should pass
                return {"success": True, "stdout": "SUCCESS"}
            else:
                # Testing remaining - should pass
                return {"success": True, "stdout": "SUCCESS"}

        mock_compile.invoke.side_effect = compile_side_effect
        mock_write.invoke.return_value = {"status": "ok"}
        mock_revert.invoke.return_value = {"status": "reverted"}

        upgrades = [
            {"group_id": "org.test", "artifact_id": "good1", "current_version": "1.0", "target_version": "1.1"},
            {"group_id": "org.test", "artifact_id": "bad1", "current_version": "1.0", "target_version": "1.1"},
            {"group_id": "org.test", "artifact_id": "good2", "current_version": "1.0", "target_version": "1.1"},
            {"group_id": "org.test", "artifact_id": "good3", "current_version": "1.0", "target_version": "1.1"},
        ]

        # Iteration 1: Test [0,1] -> FAIL
        state = self._make_state(
            planned_upgrades=upgrades,
            pending_indices=[0, 1, 2, 3],
        )
        result1 = binary_search_upgrades_node(state)
        assert result1["pending_upgrade_indices"] == [0, 1]  # Narrow down
        assert result1["deferred_upgrade_indices"] == [2, 3]  # Deferred

        # Iteration 2: Test [0] -> PASS
        state2 = self._make_state(
            planned_upgrades=upgrades,
            pending_indices=[0, 1],
            deferred_indices=[2, 3],
        )
        result2 = binary_search_upgrades_node(state2)
        assert upgrades[0] in result2["good_upgrades"]  # 0 is good
        assert result2["pending_upgrade_indices"] == [1]  # 1 is pending

        # Iteration 3: Single [1] -> BAD
        state3 = self._make_state(
            planned_upgrades=upgrades,
            pending_indices=[1],
            good_upgrades=[upgrades[0]],
            deferred_indices=[2, 3],
            build_output="Error with bad1",
        )
        result3 = binary_search_upgrades_node(state3)
        assert len(result3["bad_upgrades"]) == 1
        assert result3["bad_upgrades"][0]["artifact_id"] == "bad1"
        # Should now process deferred
        assert result3["pending_upgrade_indices"] == [2, 3]


class TestApplyUpgradesToContent:
    """Test the programmatic upgrade application."""

    def test_gradle_plugin_version_update(self):
        """Test updating Gradle plugin versions."""
        content = """plugins {
    id 'java'
    id 'org.springframework.boot' version '3.2.0'
    id 'io.spring.dependency-management' version '1.1.0'
}"""
        upgrades = [
            {"group_id": "org.springframework.boot", "artifact_id": "", "current_version": "3.2.0", "target_version": "3.2.5"},
        ]

        result = apply_upgrades_to_content(content, upgrades, "gradle")

        assert "version '3.2.5'" in result
        assert "version '3.2.0'" not in result

    def test_gradle_dependency_version_update(self):
        """Test updating Gradle dependency versions."""
        content = """dependencies {
    implementation 'org.apache.commons:commons-lang3:3.12.0'
    implementation 'com.google.guava:guava:31.0-jre'
}"""
        upgrades = [
            {"group_id": "org.apache.commons", "artifact_id": "commons-lang3", "current_version": "3.12.0", "target_version": "3.14.0"},
        ]

        result = apply_upgrades_to_content(content, upgrades, "gradle")

        assert "commons-lang3:3.14.0" in result
        assert "commons-lang3:3.12.0" not in result
        # Other dependency should be unchanged
        assert "guava:31.0-jre" in result

    def test_no_change_when_versions_match(self):
        """Test that nothing changes when current == target."""
        content = "implementation 'org.test:lib:1.0.0'"
        upgrades = [
            {"group_id": "org.test", "artifact_id": "lib", "current_version": "1.0.0", "target_version": "1.0.0"},
        ]

        result = apply_upgrades_to_content(content, upgrades, "gradle")

        assert result == content

    def test_multiple_upgrades(self):
        """Test applying multiple upgrades at once."""
        content = """dependencies {
    implementation 'org.a:liba:1.0'
    implementation 'org.b:libb:2.0'
    implementation 'org.c:libc:3.0'
}"""
        upgrades = [
            {"group_id": "org.a", "artifact_id": "liba", "current_version": "1.0", "target_version": "1.1"},
            {"group_id": "org.b", "artifact_id": "libb", "current_version": "2.0", "target_version": "2.5"},
        ]

        result = apply_upgrades_to_content(content, upgrades, "gradle")

        assert "liba:1.1" in result
        assert "libb:2.5" in result
        assert "libc:3.0" in result  # Unchanged


class TestVersionAlreadyApplied:
    """Test detection of already-applied versions."""

    def test_gradle_plugin_version_detected(self):
        """Detect plugin version that's already at target."""
        content = """plugins {
    id 'org.springframework.boot' version '4.0.5'
}"""
        assert version_already_applied(content, "org.springframework.boot", "4.0.5", "gradle") is True
        assert version_already_applied(content, "org.springframework.boot", "4.0.4", "gradle") is False

    def test_gradle_dependency_version_detected(self):
        """Detect dependency version that's already at target."""
        content = "implementation 'org.apache.commons:commons-lang3:3.14.0'"
        assert version_already_applied(content, "org.apache.commons", "3.14.0", "gradle") is True
        assert version_already_applied(content, "org.apache.commons", "3.13.0", "gradle") is False

    def test_maven_version_detected(self):
        """Detect Maven version in XML."""
        content = """<dependency>
    <groupId>org.springframework.boot</groupId>
    <artifactId>spring-boot-starter</artifactId>
    <version>3.2.5</version>
</dependency>"""
        assert version_already_applied(content, "org.springframework.boot", "3.2.5", "maven") is True
        assert version_already_applied(content, "org.springframework.boot", "3.2.4", "maven") is False


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

