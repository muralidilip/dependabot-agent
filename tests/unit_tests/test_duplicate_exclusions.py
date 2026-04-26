"""Tests for duplicate exclusion removal functionality."""

import pytest

from dependabot_agent.helpers.build_file import (
    remove_duplicate_exclusions,
    remove_duplicate_exclusions_gradle,
    remove_duplicate_exclusions_maven,
)


class TestRemoveDuplicateExclusionsGradle:
    """Tests for Gradle duplicate exclusion removal."""

    def test_removes_duplicate_in_same_block(self):
        """Duplicate exclusions in the same dependency block should be removed."""
        content = """
implementation('org.example:foo:1.0') {
    exclude group: 'org.bad', module: 'thing'
    exclude group: 'org.bad', module: 'thing'
    exclude group: 'org.other', module: 'stuff'
}
"""
        result, count = remove_duplicate_exclusions_gradle(content)

        assert count == 1
        assert result.count("exclude group: 'org.bad', module: 'thing'") == 1
        assert result.count("exclude group: 'org.other', module: 'stuff'") == 1

    def test_keeps_same_exclusion_in_different_blocks(self):
        """Same exclusion in DIFFERENT dependency blocks should NOT be removed."""
        content = """
implementation('org.example:foo:1.0') {
    exclude group: 'org.bad', module: 'thing'
}

implementation('org.example:bar:2.0') {
    exclude group: 'org.bad', module: 'thing'
}
"""
        result, count = remove_duplicate_exclusions_gradle(content)

        assert count == 0
        assert result.count("exclude group: 'org.bad', module: 'thing'") == 2

    def test_removes_multiple_duplicates(self):
        """Multiple duplicates of the same exclusion should all be removed except one."""
        content = """
implementation('org.example:foo:1.0') {
    exclude group: 'a', module: 'b'
    exclude group: 'a', module: 'b'
    exclude group: 'a', module: 'b'
}
"""
        result, count = remove_duplicate_exclusions_gradle(content)

        assert count == 2
        assert result.count("exclude group: 'a', module: 'b'") == 1

    def test_no_duplicates_returns_unchanged(self):
        """Content without duplicates should be returned unchanged."""
        content = """
implementation('org.example:foo:1.0') {
    exclude group: 'a', module: 'b'
    exclude group: 'c', module: 'd'
}
"""
        result, count = remove_duplicate_exclusions_gradle(content)

        assert count == 0
        assert "exclude group: 'a', module: 'b'" in result
        assert "exclude group: 'c', module: 'd'" in result

    def test_api_configuration_type(self):
        """Works with api configuration type."""
        content = """
api('org.example:foo:1.0') {
    exclude group: 'org.bad', module: 'thing'
    exclude group: 'org.bad', module: 'thing'
}
"""
        result, count = remove_duplicate_exclusions_gradle(content)

        assert count == 1


class TestRemoveDuplicateExclusionsMaven:
    """Tests for Maven duplicate exclusion removal."""

    def test_removes_duplicate_in_same_dependency(self):
        """Duplicate exclusions in the same dependency block should be removed."""
        content = """
<dependency>
    <groupId>org.example</groupId>
    <artifactId>foo</artifactId>
    <version>1.0</version>
    <exclusions>
        <exclusion>
            <groupId>org.bad</groupId>
            <artifactId>thing</artifactId>
        </exclusion>
        <exclusion>
            <groupId>org.bad</groupId>
            <artifactId>thing</artifactId>
        </exclusion>
    </exclusions>
</dependency>
"""
        result, count = remove_duplicate_exclusions_maven(content)

        assert count == 1
        assert result.count("<groupId>org.bad</groupId>") == 1

    def test_keeps_same_exclusion_in_different_dependencies(self):
        """Same exclusion in different dependency blocks should NOT be removed."""
        content = """
<dependency>
    <groupId>org.example</groupId>
    <artifactId>foo</artifactId>
    <exclusions>
        <exclusion>
            <groupId>org.bad</groupId>
            <artifactId>thing</artifactId>
        </exclusion>
    </exclusions>
</dependency>
<dependency>
    <groupId>org.example</groupId>
    <artifactId>bar</artifactId>
    <exclusions>
        <exclusion>
            <groupId>org.bad</groupId>
            <artifactId>thing</artifactId>
        </exclusion>
    </exclusions>
</dependency>
"""
        result, count = remove_duplicate_exclusions_maven(content)

        assert count == 0


class TestRemoveDuplicateExclusionsWrapper:
    """Tests for the wrapper function."""

    def test_gradle_build_system(self):
        """Wrapper correctly delegates to Gradle implementation."""
        content = """
implementation('org.example:foo:1.0') {
    exclude group: 'org.bad', module: 'thing'
    exclude group: 'org.bad', module: 'thing'
}
"""
        result, count = remove_duplicate_exclusions(content, "gradle")

        assert count == 1

    def test_maven_build_system(self):
        """Wrapper correctly delegates to Maven implementation."""
        content = """
<dependency>
    <groupId>org.example</groupId>
    <artifactId>foo</artifactId>
    <exclusions>
        <exclusion>
            <groupId>org.bad</groupId>
            <artifactId>thing</artifactId>
        </exclusion>
        <exclusion>
            <groupId>org.bad</groupId>
            <artifactId>thing</artifactId>
        </exclusion>
    </exclusions>
</dependency>
"""
        result, count = remove_duplicate_exclusions(content, "maven")

        assert count == 1

