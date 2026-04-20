import io
import json
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlparse

import pytest

import dependabot_agent.github_dependabot as github_dependabot
from dependabot_agent.github_dependabot import GITHUB_API_VERSION
from dependabot_agent.github_dependabot import fetch_dependabot_alerts


class _FakeResponse:
	def __init__(self, payload: bytes) -> None:
		self._payload = payload

	def __enter__(self) -> "_FakeResponse":
		return self

	def __exit__(self, exc_type, exc, tb) -> bool:
		return False

	def read(self) -> bytes:
		return self._payload


def test_fetch_dependabot_alerts_requires_github_token(monkeypatch: pytest.MonkeyPatch) -> None:
	monkeypatch.delenv("GITHUB_TOKEN", raising=False)

	with pytest.raises(ValueError, match="GITHUB_TOKEN"):
		fetch_dependabot_alerts.invoke({"repo": "octo-org/demo-repo"})


def test_fetch_dependabot_alerts_supports_owner_repo(monkeypatch: pytest.MonkeyPatch) -> None:
	monkeypatch.setenv("GITHUB_TOKEN", "test-token")
	captured: dict[str, object] = {}

	def fake_urlopen(request, timeout: int) -> _FakeResponse:
		captured["request"] = request
		captured["timeout"] = timeout
		payload = json.dumps(
			[
				{
					"number": 7,
					"state": "open",
					"html_url": "https://github.com/octo-org/demo-repo/security/dependabot/7",
					"created_at": "2026-04-14T10:00:00Z",
					"security_advisory": {
						"severity": "high",
						"summary": "Vulnerable dependency",
					},
					"security_vulnerability": {
						"severity": "high",
						"package": {"name": "requests", "ecosystem": "pip"},
					},
					"dependency": {
						"manifest_path": "requirements.txt",
						"package": {"name": "requests"},
					},
				}
			]
		).encode("utf-8")
		return _FakeResponse(payload)

	monkeypatch.setattr(github_dependabot, "urlopen", fake_urlopen)

	result = fetch_dependabot_alerts.invoke(
		{
			"repo": "octo-org/demo-repo",
			"state": "open",
			"severity": "high",
			"ecosystem": "pip",
			"per_page": 50,
			"after": "cursor_abc123",
		}
	)

	request = captured["request"]
	parsed = urlparse(request.full_url)
	query = parse_qs(parsed.query)

	assert parsed.path == "/repos/octo-org/demo-repo/dependabot/alerts"
	assert query == {
		"state": ["open"],
		"severity": ["high"],
		"ecosystem": ["pip"],
		"per_page": ["50"],
		"after": ["cursor_abc123"],
	}
	assert request.get_header("Accept") == "application/vnd.github+json"
	assert request.get_header("Authorization") == "Bearer test-token"
	assert request.get_header("X-github-api-version") == GITHUB_API_VERSION
	assert request.get_method() == "GET"
	assert captured["timeout"] == 20
	assert result == {
		"owner": "octo-org",
		"repository": "demo-repo",
		"full_name": "octo-org/demo-repo",
		"count": 1,
		"alerts": [
			{
				"number": 7,
				"state": "open",
				"severity": "high",
				"summary": "Vulnerable dependency",
				"package": "requests",
				"ecosystem": "pip",
				"manifest_path": "requirements.txt",
				"html_url": "https://github.com/octo-org/demo-repo/security/dependabot/7",
				"created_at": "2026-04-14T10:00:00Z",
				"dismissed_at": None,
				"fixed_at": None,
			}
		],
	}


def test_fetch_dependabot_alerts_uses_default_owner(monkeypatch: pytest.MonkeyPatch) -> None:
	monkeypatch.setenv("GITHUB_TOKEN", "test-token")
	monkeypatch.setenv("GITHUB_DEFAULT_OWNER", "example-org")
	captured: dict[str, object] = {}

	def fake_urlopen(request, timeout: int) -> _FakeResponse:
		captured["request"] = request
		return _FakeResponse(b"[]")

	monkeypatch.setattr(github_dependabot, "urlopen", fake_urlopen)

	result = fetch_dependabot_alerts.invoke({"repo": "demo-repo"})

	request = captured["request"]
	assert urlparse(request.full_url).path == "/repos/example-org/demo-repo/dependabot/alerts"
	assert result["owner"] == "example-org"
	assert result["repository"] == "demo-repo"
	assert result["count"] == 0


def test_fetch_dependabot_alerts_surfaces_github_errors(monkeypatch: pytest.MonkeyPatch) -> None:
	monkeypatch.setenv("GITHUB_TOKEN", "test-token")

	def fake_urlopen(request, timeout: int) -> _FakeResponse:
		raise HTTPError(
			request.full_url,
			404,
			"Not Found",
			hdrs=None,
			fp=io.BytesIO(b'{"message":"Dependabot alerts are disabled for this repository"}'),
		)

	monkeypatch.setattr(github_dependabot, "urlopen", fake_urlopen)

	with pytest.raises(RuntimeError, match="octo-org/demo-repo: Dependabot alerts are disabled for this repository"):
		fetch_dependabot_alerts.invoke({"repo": "octo-org/demo-repo"})

