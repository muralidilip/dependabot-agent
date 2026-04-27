"""Test cleanup dependencies functionality."""

import pytest

from dependabot_agent.helpers.build_file import (
    analyze_pins_for_cleanup,
    extract_pinned_dependencies_with_version,
    find_all_transitive_occurrences,
    find_redundant_pins,
    parse_transitive_dependencies,
    remove_redundant_pins,
)


# Also available via helpers package
from dependabot_agent.helpers import (
    analyze_pins_for_cleanup as analyze_pins_helper,
    find_all_transitive_occurrences as find_transitive_helper,
)


class TestExtractPinnedDependencies:
    """Test extraction of pinned dependencies from build files."""

    def test_gradle_simple_pin(self):
        """Extract simple pinned dependency from Gradle."""
        content = """dependencies {
    implementation 'org.springframework.boot:spring-boot-starter:3.2.0'
    implementation 'tools.jackson.core:jackson-core:3.1.2'
}"""
        pins = extract_pinned_dependencies_with_version(content, "gradle")

        assert len(pins) == 2
        assert any(p["artifact_id"] == "spring-boot-starter" and p["version"] == "3.2.0" for p in pins)
        assert any(p["artifact_id"] == "jackson-core" and p["version"] == "3.1.2" for p in pins)

    def test_gradle_parentheses_format(self):
        """Extract dependency with parentheses format."""
        content = """dependencies {
    implementation('org.apache.commons:commons-lang3:3.14.0')
}"""
        pins = extract_pinned_dependencies_with_version(content, "gradle")

        assert len(pins) == 1
        assert pins[0]["artifact_id"] == "commons-lang3"
        assert pins[0]["version"] == "3.14.0"

    def test_gradle_skips_exclusion_blocks(self):
        """Don't extract dependencies with exclusion blocks as simple pins."""
        content = """dependencies {
    implementation('org.springframework.boot:spring-boot-starter-web:3.2.0') {
        exclude group: 'org.springframework.boot', module: 'spring-boot-starter-tomcat'
    }
    implementation 'simple:dependency:1.0.0'
}"""
        pins = extract_pinned_dependencies_with_version(content, "gradle")

        # Should only get the simple dependency, not the one with exclusion
        assert len(pins) == 1
        assert pins[0]["artifact_id"] == "dependency"

    def test_maven_simple_pin(self):
        """Extract pinned dependency from Maven POM."""
        content = """<dependencies>
    <dependency>
        <groupId>tools.jackson.core</groupId>
        <artifactId>jackson-core</artifactId>
        <version>3.1.2</version>
    </dependency>
</dependencies>"""
        pins = extract_pinned_dependencies_with_version(content, "maven")

        assert len(pins) == 1
        assert pins[0]["group_id"] == "tools.jackson.core"
        assert pins[0]["artifact_id"] == "jackson-core"
        assert pins[0]["version"] == "3.1.2"


class TestParseTransitiveDependencies:
    """Test parsing of dependency tree to find transitive versions."""

    def test_gradle_only_nested_deps(self):
        """Only parse nested (transitive) dependencies, not top-level."""
        dep_tree = """+--- org.springframework.boot:spring-boot-starter:3.2.0
|    +--- org.springframework:spring-core:6.1.0
|    \\--- org.yaml:snakeyaml:2.0
+--- com.github.spullara.mustache.java:compiler:0.9.10"""

        transitive = parse_transitive_dependencies(dep_tree, "gradle")

        # Only nested deps should be captured
        assert "org.springframework:spring-core" in transitive
        assert transitive["org.springframework:spring-core"] == "6.1.0"
        assert "org.yaml:snakeyaml" in transitive

        # Top-level deps should NOT be captured
        assert "org.springframework.boot:spring-boot-starter" not in transitive
        assert "com.github.spullara.mustache.java:compiler" not in transitive

    def test_gradle_version_override(self):
        """Parse version override (-> notation) in Gradle tree."""
        dep_tree = """+--- org.springframework.boot:spring-boot-starter:3.2.0
|    +--- tools.jackson.core:jackson-core:3.1.0 -> 3.1.2
|    \\--- org.springframework:spring-core:6.1.0 -> 6.1.2"""

        transitive = parse_transitive_dependencies(dep_tree, "gradle")

        # Should use the resolved version (right side of ->), not declared
        assert transitive["tools.jackson.core:jackson-core"] == "3.1.2"
        assert transitive["org.springframework:spring-core"] == "6.1.2"

    def test_maven_only_nested_deps(self):
        """Only parse nested (transitive) dependencies for Maven."""
        dep_tree = """[INFO] +- org.springframework.boot:spring-boot-starter:jar:3.2.0:compile
[INFO] |  +- org.springframework:spring-core:jar:6.1.0:compile
[INFO] |  \\- org.yaml:snakeyaml:jar:2.0:compile
[INFO] +- com.github.spullara.mustache.java:compiler:jar:0.9.10:compile"""

        transitive = parse_transitive_dependencies(dep_tree, "maven")

        # Only nested deps should be captured
        assert "org.springframework:spring-core" in transitive
        assert "org.yaml:snakeyaml" in transitive

        # Top-level deps should NOT be captured
        assert "org.springframework.boot:spring-boot-starter" not in transitive
        assert "com.github.spullara.mustache.java:compiler" not in transitive


class TestFindRedundantPins:
    """Test identification of redundant pinned dependencies."""

    def test_finds_redundant_when_same_version_transitive(self):
        """Find pin that matches transitive version exactly."""
        build_content = """dependencies {
    implementation 'tools.jackson.core:jackson-core:3.1.2'
}"""
        # jackson-core is NESTED under spring-boot-starter (transitive)
        dep_tree = """+--- org.springframework.boot:spring-boot-starter:3.2.0
|    +--- tools.jackson.core:jackson-databind:3.1.0
|    |    \\--- tools.jackson.core:jackson-core:3.1.0 -> 3.1.2"""

        redundant = find_redundant_pins(build_content, dep_tree, "gradle")

        assert len(redundant) == 1
        assert redundant[0]["artifact_id"] == "jackson-core"
        assert redundant[0]["version"] == "3.1.2"

    def test_not_redundant_when_only_top_level(self):
        """Don't flag pin if dependency only appears at top level (not transitive)."""
        build_content = """dependencies {
    implementation 'com.github.spullara.mustache.java:compiler:0.9.10'
}"""
        # mustache compiler is only at TOP LEVEL, not nested
        dep_tree = """+--- org.springframework.boot:spring-boot-starter:3.2.0
|    \\--- org.springframework:spring-core:6.1.0
+--- com.github.spullara.mustache.java:compiler:0.9.10"""

        redundant = find_redundant_pins(build_content, dep_tree, "gradle")

        # mustache.java:compiler should NOT be flagged - it's only top-level
        assert len(redundant) == 0

    def test_not_redundant_when_different_version(self):
        """Don't flag pin if transitive provides different version."""
        build_content = """dependencies {
    implementation 'tools.jackson.core:jackson-core:3.1.2'
}"""
        # Transitive provides 3.1.0, which is different from pinned 3.1.2
        dep_tree = """+--- org.springframework.boot:spring-boot-starter:3.2.0
|    +--- tools.jackson.core:jackson-databind:3.1.0
|    |    \\--- tools.jackson.core:jackson-core:3.1.0"""

        redundant = find_redundant_pins(build_content, dep_tree, "gradle")

        # Should not be flagged - the pin is actually doing something (forcing 3.1.2)
        assert len(redundant) == 0

    def test_not_redundant_when_not_in_tree(self):
        """Don't flag pin if dependency not in tree at all."""
        build_content = """dependencies {
    implementation 'org.apache.commons:commons-lang3:3.14.0'
}"""
        dep_tree = """+--- org.springframework.boot:spring-boot-starter:3.2.0
|    \\--- org.springframework:spring-core:6.1.0"""

        redundant = find_redundant_pins(build_content, dep_tree, "gradle")

        # commons-lang3 is not in tree, so the pin is needed
        assert len(redundant) == 0

    def test_multiple_redundant_pins(self):
        """Find multiple redundant pins."""
        build_content = """dependencies {
    implementation 'tools.jackson.core:jackson-core:3.1.2'
    implementation 'org.springframework:spring-core:6.1.2'
    implementation 'org.yaml:snakeyaml:2.0'
}"""
        # All three are NESTED under spring-boot-starter
        dep_tree = """+--- org.springframework.boot:spring-boot-starter:3.2.0
|    +--- tools.jackson.core:jackson-core:3.1.0 -> 3.1.2
|    +--- org.springframework:spring-core:6.1.0 -> 6.1.2
|    \\--- org.yaml:snakeyaml:2.0"""

        redundant = find_redundant_pins(build_content, dep_tree, "gradle")

        assert len(redundant) == 3
        artifacts = [r["artifact_id"] for r in redundant]
        assert "jackson-core" in artifacts
        assert "spring-core" in artifacts
        assert "snakeyaml" in artifacts


class TestRemoveRedundantPins:
    """Test removal of redundant pins from build files."""

    def test_remove_single_gradle_pin(self):
        """Remove a single redundant pin from Gradle file."""
        content = """dependencies {
    implementation 'org.springframework.boot:spring-boot-starter:3.2.0'
    implementation 'tools.jackson.core:jackson-core:3.1.2'
    testImplementation 'junit:junit:4.13.2'
}"""
        redundant = [{
            "group_id": "tools.jackson.core",
            "artifact_id": "jackson-core",
            "version": "3.1.2",
            "config_type": "implementation",
        }]

        result = remove_redundant_pins(content, redundant, "gradle")

        assert "spring-boot-starter:3.2.0" in result
        assert "jackson-core" not in result
        assert "junit:4.13.2" in result

    def test_remove_multiple_gradle_pins(self):
        """Remove multiple redundant pins from Gradle file."""
        content = """dependencies {
    implementation 'org.springframework.boot:spring-boot-starter:3.2.0'
    implementation 'tools.jackson.core:jackson-core:3.1.2'
    implementation 'org.springframework:spring-core:6.1.2'
    testImplementation 'junit:junit:4.13.2'
}"""
        redundant = [
            {
                "group_id": "tools.jackson.core",
                "artifact_id": "jackson-core",
                "version": "3.1.2",
                "config_type": "implementation",
            },
            {
                "group_id": "org.springframework",
                "artifact_id": "spring-core",
                "version": "6.1.2",
                "config_type": "implementation",
            },
        ]

        result = remove_redundant_pins(content, redundant, "gradle")

        assert "spring-boot-starter:3.2.0" in result
        assert "jackson-core" not in result
        assert "spring-core" not in result
        assert "junit:4.13.2" in result

    def test_remove_maven_pin(self):
        """Remove a redundant pin from Maven POM file."""
        content = """<dependencies>
    <dependency>
        <groupId>org.springframework.boot</groupId>
        <artifactId>spring-boot-starter</artifactId>
        <version>3.2.0</version>
    </dependency>
    <dependency>
        <groupId>tools.jackson.core</groupId>
        <artifactId>jackson-core</artifactId>
        <version>3.1.2</version>
    </dependency>
</dependencies>"""
        redundant = [{
            "group_id": "tools.jackson.core",
            "artifact_id": "jackson-core",
            "version": "3.1.2",
        }]

        result = remove_redundant_pins(content, redundant, "maven")

        assert "spring-boot-starter" in result
        assert "jackson-core" not in result


class TestEndToEndCleanup:
    """Test full cleanup scenario."""

    def test_spring_boot_upgrade_makes_pin_redundant(self):
        """Simulate a real scenario: Spring Boot upgrade makes jackson pin redundant."""
        # After upgrading Spring Boot, it now provides jackson-core:3.1.2 transitively
        build_content = """plugins {
    id 'org.springframework.boot' version '4.0.5'
}

dependencies {
    implementation 'org.springframework.boot:spring-boot-starter-webflux'
    implementation 'tools.jackson.core:jackson-core:3.1.2'
}"""

        # Dependency tree shows jackson-core is provided transitively (NESTED) at 3.1.2
        dep_tree = """+--- org.springframework.boot:spring-boot-starter-webflux:4.0.5
|    +--- org.springframework.boot:spring-boot-starter-jackson:4.0.5
|    |    \\--- tools.jackson.core:jackson-databind:3.1.2
|    |         \\--- tools.jackson.core:jackson-core:3.1.2"""

        # Find redundant pins
        redundant = find_redundant_pins(build_content, dep_tree, "gradle")

        assert len(redundant) == 1
        assert redundant[0]["artifact_id"] == "jackson-core"

        # Remove redundant pins
        cleaned = remove_redundant_pins(build_content, redundant, "gradle")

        assert "spring-boot-starter-webflux" in cleaned
        assert "jackson-core" not in cleaned
        # Plugins block should be unchanged
        assert "version '4.0.5'" in cleaned

    def test_direct_dependency_not_removed(self):
        """Direct dependencies (only at top level) should never be removed."""
        build_content = """dependencies {
    implementation 'org.springframework.boot:spring-boot-starter-webflux'
    implementation 'com.github.spullara.mustache.java:compiler:0.9.10'
}"""

        # mustache.java:compiler is ONLY at top level, not nested
        dep_tree = """+--- org.springframework.boot:spring-boot-starter-webflux:4.0.5
|    +--- org.springframework:spring-core:6.1.2
|    \\--- org.yaml:snakeyaml:2.0
+--- com.github.spullara.mustache.java:compiler:0.9.10"""

        # Find redundant pins
        redundant = find_redundant_pins(build_content, dep_tree, "gradle")

        # mustache.java should NOT be flagged as redundant
        assert len(redundant) == 0


class TestFindAllTransitiveOccurrences:
    """Test finding all transitive occurrences of a dependency in the tree."""

    def test_finds_single_occurrence(self):
        """Find a dependency that appears once transitively."""
        dep_tree = """+--- org.springframework.boot:spring-boot-starter:3.2.0
|    +--- org.springframework:spring-core:6.1.0
|    |    \\--- tools.jackson.core:jackson-core:3.1.2
|    \\--- org.yaml:snakeyaml:2.0"""

        occurrences = find_all_transitive_occurrences(
            dep_tree, "tools.jackson.core", "jackson-core", "gradle"
        )

        assert len(occurrences) == 1
        assert occurrences[0]["parent"] == "org.springframework:spring-core"
        assert occurrences[0]["version"] == "3.1.2"
        assert occurrences[0]["depth"] == 2

    def test_finds_multiple_occurrences(self):
        """Find a dependency that appears multiple times transitively."""
        dep_tree = """+--- org.springframework.boot:spring-boot-starter-web:3.2.0
|    +--- org.springframework:spring-core:6.1.0
|    |    \\--- tools.jackson.core:jackson-core:3.1.2
+--- org.springframework.boot:spring-boot-starter-json:3.2.0
|    +--- tools.jackson.core:jackson-databind:3.1.2
|    |    \\--- tools.jackson.core:jackson-core:3.1.2"""

        occurrences = find_all_transitive_occurrences(
            dep_tree, "tools.jackson.core", "jackson-core", "gradle"
        )

        assert len(occurrences) == 2
        parents = {occ["parent"] for occ in occurrences}
        assert "org.springframework:spring-core" in parents
        assert "tools.jackson.core:jackson-databind" in parents

    def test_no_occurrences_for_top_level_only(self):
        """No transitive occurrences if dependency is only at top level."""
        dep_tree = """+--- org.springframework.boot:spring-boot-starter:3.2.0
|    \\--- org.springframework:spring-core:6.1.0
+--- tools.jackson.core:jackson-core:3.1.2"""

        occurrences = find_all_transitive_occurrences(
            dep_tree, "tools.jackson.core", "jackson-core", "gradle"
        )

        # jackson-core is top-level, not transitive
        assert len(occurrences) == 0

    def test_handles_version_override(self):
        """Correctly extracts resolved version when there's an override."""
        dep_tree = """+--- org.springframework.boot:spring-boot-starter:3.2.0
|    \\--- tools.jackson.core:jackson-core:3.1.0 -> 3.1.2"""

        occurrences = find_all_transitive_occurrences(
            dep_tree, "tools.jackson.core", "jackson-core", "gradle"
        )

        assert len(occurrences) == 1
        # Should use resolved version (3.1.2), not declared (3.1.0)
        assert occurrences[0]["version"] == "3.1.2"


class TestAnalyzePinsForCleanup:
    """Test pin analysis and classification."""

    def test_classifies_direct_only(self):
        """Pin with no transitive occurrences is classified as direct_only."""
        build_content = """dependencies {
    implementation 'com.example:my-lib:1.0.0'
}"""
        # my-lib is not in the tree at all (no transitive bringing it in)
        dep_tree = """+--- org.springframework.boot:spring-boot-starter:3.2.0
|    \\--- org.springframework:spring-core:6.1.0"""

        from dependabot_agent.helpers.build_file import analyze_pins_for_cleanup

        analyzed = analyze_pins_for_cleanup(build_content, dep_tree, "gradle")

        assert len(analyzed) == 1
        assert analyzed[0]["artifact_id"] == "my-lib"
        assert analyzed[0]["classification"] == "direct_only"

    def test_classifies_redundant(self):
        """Pin that matches transitive version is classified as redundant."""
        build_content = """dependencies {
    implementation 'tools.jackson.core:jackson-core:3.1.2'
}"""
        # jackson-core is transitively provided at same version
        dep_tree = """+--- org.springframework.boot:spring-boot-starter:3.2.0
|    +--- tools.jackson.core:jackson-databind:3.1.2
|    |    \\--- tools.jackson.core:jackson-core:3.1.2"""

        from dependabot_agent.helpers.build_file import analyze_pins_for_cleanup

        analyzed = analyze_pins_for_cleanup(build_content, dep_tree, "gradle")

        assert len(analyzed) == 1
        assert analyzed[0]["artifact_id"] == "jackson-core"
        assert analyzed[0]["classification"] == "redundant"

    def test_classifies_version_forcing(self):
        """Pin that forces different version is classified as version_forcing."""
        build_content = """dependencies {
    implementation 'tools.jackson.core:jackson-core:3.1.5'
}"""
        # jackson-core transitive is 3.1.2, but pin forces 3.1.5
        dep_tree = """+--- org.springframework.boot:spring-boot-starter:3.2.0
|    +--- tools.jackson.core:jackson-databind:3.1.2
|    |    \\--- tools.jackson.core:jackson-core:3.1.2 -> 3.1.5"""

        from dependabot_agent.helpers.build_file import analyze_pins_for_cleanup

        analyzed = analyze_pins_for_cleanup(build_content, dep_tree, "gradle")

        assert len(analyzed) == 1
        assert analyzed[0]["artifact_id"] == "jackson-core"
        assert analyzed[0]["classification"] == "version_forcing"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

