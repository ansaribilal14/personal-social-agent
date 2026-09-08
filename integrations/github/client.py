"""GitHub REST client - issues, comments, labels (spec sections 33, 51).

Used as the authoritative editorial review surface. Never used as a database.
Token comes from env (GITHUB_TOKEN); Actions provides one automatically.
"""
from __future__ import annotations

import os

import requests

API = "https://api.github.com"


class GitHubError(Exception):
    pass


class GitHubClient:
    def __init__(self, token: str | None = None, repo: str | None = None,
                 session: requests.Session | None = None):
        self.token = token or os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_PAT")
        self.repo = repo or os.environ.get("GITHUB_REPOSITORY")
        self.session = session or requests.Session()

    def _headers(self) -> dict:
        if not self.token:
            raise GitHubError("GITHUB_TOKEN not configured")
        if not self.repo:
            raise GitHubError("GITHUB_REPOSITORY not configured")
        return {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    def _url(self, path: str) -> str:
        return f"{API}/repos/{self.repo}/{path.lstrip('/')}"

    def _request(self, method: str, path: str, json_body: dict | None = None,
                 params: dict | None = None) -> requests.Response:
        try:
            resp = self.session.request(method, self._url(path), json=json_body,
                                        params=params, headers=self._headers(),
                                        timeout=30)
        except (requests.Timeout, requests.ConnectionError) as exc:
            raise GitHubError(f"github unreachable: {type(exc).__name__}") from exc
        if resp.status_code >= 400:
            raise GitHubError(f"github API error HTTP {resp.status_code} on {path}")
        return resp

    # ---------------------------------------------------------------- issues
    def create_issue(self, title: str, body: str,
                     labels: list[str] | None = None) -> dict:
        payload: dict = {"title": title, "body": body}
        if labels:
            payload["labels"] = labels
        resp = self._request("POST", "issues", payload)
        return resp.json()

    def list_open_issues(self, label: str | None = None,
                         limit: int = 50) -> list[dict]:
        path = f"issues?state=open&per_page={int(limit)}"
        if label:
            path += f"&labels={label}"
        resp = self._request("GET", path)
        return resp.json()

    def add_comment(self, issue_number: int, body: str) -> dict:
        resp = self._request("POST", f"issues/{issue_number}/comments",
                             {"body": body})
        return resp.json()

    def list_comments(self, issue_number: int, since: str | None = None) -> list[dict]:
        params = {"per_page": 100}
        if since:
            params["since"] = since
        resp = self._request("GET", f"issues/{issue_number}/comments", params=params)
        return resp.json()

    def update_issue(self, issue_number: int, state: str | None = None,
                     title: str | None = None, body: str | None = None,
                     labels: list[str] | None = None) -> dict:
        payload: dict = {}
        if state:
            payload["state"] = state  # 'open' | 'closed'
        if title:
            payload["title"] = title
        if body is not None:
            payload["body"] = body
        if labels is not None:
            payload["labels"] = labels
        resp = self._request("PATCH", f"issues/{issue_number}", payload)
        return resp.json()

    def get_issue(self, issue_number: int) -> dict:
        resp = self._request("GET", f"issues/{issue_number}")
        return resp.json()

    def ensure_labels(self, labels: list[dict]) -> None:
        """labels: [{'name':..., 'color':'0e8a16','description':...}]"""
        existing = {l["name"] for l in self._request(
            "GET", "labels", params={"per_page": 100}).json()}
        for spec in labels:
            if spec["name"] not in existing:
                try:
                    self._request("POST", "labels", spec)
                except GitHubError:
                    pass  # label creation is best-effort
