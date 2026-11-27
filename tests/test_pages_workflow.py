"""The Pages workflow keeps an empty token and a separate deploy job."""

from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PAGES = ROOT / ".github" / "workflows" / "pages.yml"

# Defaults of the pinned actions, used when a step omits the name.
# actions/upload-pages-artifact v5.0.0 input `name`;
# actions/deploy-pages v5.0.1 input `artifact_name`.
UPLOAD_ARTIFACT_NAME = "github-pages"
DEPLOY_ARTIFACT_NAME = "github-pages"


def _strip_comment(line: str) -> str:
    in_single = False
    in_double = False
    for index, char in enumerate(line):
        if char == "'" and not in_double:
            in_single = not in_single
        elif char == '"' and not in_single:
            in_double = not in_double
        elif char == "#" and not in_single and not in_double:
            if index == 0 or line[index - 1] in " \t":
                return line[:index].rstrip()
    return line.rstrip()


def _scalar(value: str):
    if value == "{}":
        return {}
    if value == "[]":
        return []
    if value in ("", "null", "~"):
        return None
    if value in ("true", "false"):
        return value == "true"
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        return value[1:-1]
    return value


class _Parser:
    """Indentation parser for the mapping-and-list subset pages.yml uses."""

    def __init__(self, text: str) -> None:
        self.lines = text.splitlines()
        self.index = 0

    def parse(self) -> dict:
        parsed = self._parse_map(0)
        if not isinstance(parsed, dict):
            raise ValueError("workflow root is not a mapping")
        return parsed

    def _parse_map(self, indent: int) -> dict:
        mapping: dict = {}
        while self.index < len(self.lines):
            found = self._peek_content()
            if found is None:
                break
            line_indent, content = found
            if line_indent < indent or content.startswith("- "):
                break
            if line_indent > indent:
                raise ValueError(f"unexpected indent {line_indent} under {indent}: {content}")
            self.index += 1
            key, separator, rest = content.partition(":")
            if not separator:
                raise ValueError(f"expected a mapping key: {content}")
            key = key.strip()
            rest = rest.strip()
            if rest in ("|", "|-", "|+", ">", ">-", ">+"):
                mapping[key] = self._block(line_indent)
            elif rest == "":
                mapping[key] = self._nested(line_indent)
            else:
                mapping[key] = _scalar(rest)
        return mapping

    def _nested(self, parent_indent: int):
        found = self._peek_content()
        if found is None:
            return None
        line_indent, content = found
        if line_indent <= parent_indent:
            return None
        if content.startswith("- "):
            return self._parse_list(line_indent)
        return self._parse_map(line_indent)

    def _parse_list(self, dash_indent: int) -> list:
        items = []
        while self.index < len(self.lines):
            found = self._peek_content()
            if found is None:
                break
            line_indent, content = found
            if line_indent != dash_indent or not content.startswith("- "):
                break
            self.index += 1
            item_text = content[2:].strip()
            key_indent = dash_indent + 2
            item: dict = {}
            if item_text:
                key, separator, rest = item_text.partition(":")
                if not separator:
                    items.append(_scalar(item_text))
                    continue
                rest = rest.strip()
                if rest in ("|", "|-", "|+", ">", ">-", ">+"):
                    item[key.strip()] = self._block(key_indent)
                elif rest == "":
                    item[key.strip()] = self._nested(key_indent)
                else:
                    item[key.strip()] = _scalar(rest)
            while self.index < len(self.lines):
                nxt = self._peek_content()
                if nxt is None:
                    break
                next_indent, next_content = nxt
                if next_indent != key_indent or next_content.startswith("- "):
                    break
                self.index += 1
                key, separator, rest = next_content.partition(":")
                if not separator:
                    raise ValueError(f"expected a mapping key: {next_content}")
                rest = rest.strip()
                if rest in ("|", "|-", "|+", ">", ">-", ">+"):
                    item[key.strip()] = self._block(next_indent)
                elif rest == "":
                    item[key.strip()] = self._nested(next_indent)
                else:
                    item[key.strip()] = _scalar(rest)
            items.append(item)
        return items

    def _block(self, parent_indent: int) -> str:
        block: list[str] = []
        content_indent = None
        while self.index < len(self.lines):
            raw = self.lines[self.index]
            if raw.strip() == "":
                if content_indent is None:
                    self.index += 1
                    continue
                block.append("")
                self.index += 1
                continue
            line_indent = len(raw) - len(raw.lstrip(" "))
            if line_indent <= parent_indent:
                break
            if content_indent is None:
                content_indent = line_indent
            block.append(raw[content_indent:])
            self.index += 1
        return "\n".join(block)

    def _peek_content(self) -> tuple[int, str] | None:
        while self.index < len(self.lines):
            raw = self.lines[self.index]
            stripped = _strip_comment(raw).strip()
            if stripped == "":
                self.index += 1
                continue
            indent = len(raw) - len(raw.lstrip(" "))
            return indent, stripped
        return None


def parse_pages_workflow(text: str) -> dict:
    return _Parser(text).parse()


def _uses_step(job: dict, action: str) -> dict:
    matches = [step for step in job["steps"] if action in str(step.get("uses", ""))]
    if len(matches) != 1:
        raise AssertionError(f"expected one {action} step, found {len(matches)}")
    return matches[0]


class PagesWorkflowTests(unittest.TestCase):
    def test_deploy_job_is_least_privilege_and_names_the_artifact(self) -> None:
        doc = parse_pages_workflow(PAGES.read_text(encoding="utf-8"))

        self.assertIn("permissions", doc)
        self.assertEqual(doc["permissions"], {})

        jobs = doc["jobs"]
        self.assertEqual(set(jobs), {"main-only", "build", "deploy"})

        main_only = jobs["main-only"]
        build = jobs["build"]
        deploy = jobs["deploy"]

        self.assertEqual(main_only.get("permissions", {}), {})
        self.assertNotIn("environment", main_only)
        self.assertNotIn("environment", build)
        self.assertIn("refs/heads/main", main_only["steps"][0]["run"])
        self.assertIn("refs/tags/", main_only["steps"][0]["run"])

        self.assertEqual(build["permissions"], {"contents": "read"})
        self.assertEqual(build["needs"], "main-only")
        self.assertEqual(deploy["needs"], "build")
        self.assertEqual(deploy["permissions"], {"pages": "write", "id-token": "write"})
        self.assertEqual(set(deploy["permissions"]), {"pages", "id-token"})
        self.assertEqual(deploy["environment"]["name"], "github-pages")

        self.assertEqual(doc["concurrency"]["group"], "pages")
        self.assertIs(doc["concurrency"]["cancel-in-progress"], False)

        upload = _uses_step(build, "actions/upload-pages-artifact@")
        deployment = _uses_step(deploy, "actions/deploy-pages@")
        upload_name = upload.get("with", {}).get("name", UPLOAD_ARTIFACT_NAME)
        deploy_name = deployment.get("with", {}).get("artifact_name", DEPLOY_ARTIFACT_NAME)
        self.assertEqual(upload_name, deploy_name)
        self.assertEqual(upload_name, "github-pages")
        self.assertTrue(any(step.get("run") == "scripts/check_site.sh _site" for step in build["steps"]))
