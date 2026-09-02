"""GraphQL query construction and response parsing."""

from __future__ import annotations

import json
from enum import Enum
from typing import Mapping, Sequence

from chikiminer.models import RateLimitInfo, RepositoryRef, WorkflowEntry

from .models import GraphQLBatchResult, GraphQLErrorInfo, WorkflowListing, ApiOutcome
from .rate_limit import parse_graphql_rate_limit


class GraphQLQueryMode(str, Enum):
    HEAD = "head"
    DEFAULT_BRANCH_ONE_QUERY = "default-branch"
    DEFAULT_BRANCH_TWO_PHASE = "two-phase"


def _gql_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _header(alias: str, repo: RepositoryRef, body: str) -> str:
    return f"{alias}: repository(owner: {_gql_string(repo.owner)}, name: {_gql_string(repo.name)}) {{ {body} }}"


def _wrap(fields: Sequence[str]) -> str:
    return "query ChikiMinerWorkflowTree {\n" + "\n".join(fields) + "\nrateLimit { cost limit remaining used resetAt }\n}"


def build_head_query(repos: Sequence[RepositoryRef]) -> dict[str, object]:
    """Build the experimental ``HEAD:.github/workflows`` query."""

    aliases = {
        f"r{index}": repo
        for index, repo in enumerate(repos)
    }
    fields = [
        _header(alias, repo, 'object(expression: "HEAD:.github/workflows") { ... on Tree { entries { name type } } }')
        for alias, repo in aliases.items()
    ]
    return {"query": _wrap(fields), "aliases": aliases, "mode": GraphQLQueryMode.HEAD}


def build_default_branch_query(repos: Sequence[RepositoryRef]) -> dict[str, object]:
    """Build the experimental one-query default-branch traversal."""

    aliases = {f"r{index}": repo for index, repo in enumerate(repos)}
    body = (
        "defaultBranchRef { name target { ... on Commit { "
        'file(path: ".github/workflows") { type object { ... on Tree { entries { name type } } } }'
        " } } }"
    )
    return {"query": _wrap([_header(alias, repo, body) for alias, repo in aliases.items()]), "aliases": aliases, "mode": GraphQLQueryMode.DEFAULT_BRANCH_ONE_QUERY}


def build_default_branch_name_query(repos: Sequence[RepositoryRef]) -> dict[str, object]:
    """Build phase one of the two-phase default-branch strategy."""

    aliases = {f"r{index}": repo for index, repo in enumerate(repos)}
    body = "defaultBranchRef { name }"
    return {"query": _wrap([_header(alias, repo, body) for alias, repo in aliases.items()]), "aliases": aliases, "mode": GraphQLQueryMode.DEFAULT_BRANCH_TWO_PHASE}


def build_default_branch_tree_query(branches: Mapping[str, tuple[RepositoryRef, str]]) -> dict[str, object]:
    """Build phase two, using the branch returned by phase one."""

    aliases = {alias: repo for alias, (repo, _branch) in branches.items()}
    fields = []
    for alias, (repo, branch) in branches.items():
        expression = f"{branch}:.github/workflows"
        body = f"object(expression: {_gql_string(expression)}) {{ ... on Tree {{ entries {{ name type }} }} }}"
        fields.append(_header(alias, repo, body))
    return {"query": _wrap(fields), "aliases": aliases, "mode": GraphQLQueryMode.HEAD}


def _entries_from_tree(tree: object) -> tuple[WorkflowEntry, ...]:
    if tree is None:
        return ()
    if not isinstance(tree, Mapping):
        raise ValueError("GraphQL tree is not an object")
    entries = tree.get("entries")
    if entries is None:
        return ()
    if not isinstance(entries, list):
        raise ValueError("GraphQL tree entries is not a list")
    normalized: list[WorkflowEntry] = []
    for item in entries:
        if not isinstance(item, Mapping):
            raise ValueError("GraphQL tree entry is not an object")
        name = item.get("name")
        entry_type = item.get("type")
        if not isinstance(name, str) or not isinstance(entry_type, str):
            raise ValueError("GraphQL tree entry lacks name/type")
        normalized.append(WorkflowEntry.from_api_entry(name, entry_type))
    return tuple(normalized)


def _tree_from_node(node: Mapping[str, object], mode: GraphQLQueryMode) -> object:
    if mode is GraphQLQueryMode.HEAD:
        return node.get("object")
    if mode is GraphQLQueryMode.DEFAULT_BRANCH_ONE_QUERY:
        ref = node.get("defaultBranchRef")
        if ref is None:
            return None
        if not isinstance(ref, Mapping):
            raise ValueError("defaultBranchRef is not an object")
        target = ref.get("target")
        if target is None:
            return None
        if not isinstance(target, Mapping):
            raise ValueError("defaultBranchRef.target is not an object")
        file_entry = target.get("file")
        if file_entry is None:
            return None
        if not isinstance(file_entry, Mapping):
            raise ValueError("Commit.file is not an object")
        return file_entry.get("object")
    if mode is GraphQLQueryMode.DEFAULT_BRANCH_TWO_PHASE:
        return None
    raise ValueError("phase two must parse a tree query")


def _error_info(raw: object, alias: str | None = None) -> GraphQLErrorInfo:
    if not isinstance(raw, Mapping):
        return GraphQLErrorInfo(message=str(raw), alias=alias)
    message = str(raw.get("message", "GraphQL error"))
    error_type = str(raw.get("type")) if raw.get("type") is not None else None
    text = f"{message} {error_type or ''}".lower()
    rate_limited = any(marker in text for marker in ("rate limit", "secondary rate", "abuse", "too many requests", "resource exhausted"))
    resource_limit = any(marker in text for marker in ("resource limit", "complexity", "timeout", "timed out", "too many nodes"))
    retryable = rate_limited or resource_limit
    return GraphQLErrorInfo(
        message=message,
        error_type=error_type,
        alias=alias,
        retryable=retryable,
        resource_limit=resource_limit,
        rate_limited=rate_limited,
    )


def parse_batch_response(
    payload: Mapping[str, object],
    repos: Sequence[RepositoryRef],
    *,
    mode: GraphQLQueryMode,
    backend: str = "graphql",
    attempts: int = 1,
    headers: Mapping[str, str] | None = None,
    response_bytes: int = 0,
    aliases: Mapping[str, RepositoryRef] | None = None,
) -> GraphQLBatchResult:
    """Process GraphQL ``data`` and ``errors`` independently by alias."""

    headers = headers or {}
    rate_limit = parse_graphql_rate_limit(payload, headers)
    aliases = dict(aliases or {f"r{index}": repo for index, repo in enumerate(repos)})
    result = GraphQLBatchResult(aliases=dict(aliases), rate_limit=rate_limit, response_bytes=response_bytes)

    raw_errors = payload.get("errors")
    if raw_errors is None:
        raw_errors = []
    if not isinstance(raw_errors, list):
        raw_errors = [{"message": "GraphQL errors field is not a list"}]

    for raw_error in raw_errors:
        path = raw_error.get("path") if isinstance(raw_error, Mapping) else None
        alias = path[0] if isinstance(path, list) and path and isinstance(path[0], str) else None
        info = _error_info(raw_error, alias)
        if alias in aliases:
            result.errors[alias] = info
        else:
            result.global_errors.append(info)

    data = payload.get("data")
    if not isinstance(data, Mapping):
        for alias in aliases:
            if alias not in result.errors:
                result.global_errors.append(GraphQLErrorInfo(message="GraphQL response has no data", alias=alias, retryable=True))
        return result

    for alias, repo in aliases.items():
        if alias in result.errors:
            continue
        if alias not in data:
            result.errors[alias] = GraphQLErrorInfo(
                message=f"GraphQL response omitted alias {alias}",
                alias=alias,
                retryable=True,
            )
            continue
        node = data.get(alias)
        if node is None:
            result.results[alias] = WorkflowListing(
                repo=repo,
                outcome=ApiOutcome.NOT_FOUND,
                backend=backend,
                attempts=attempts,
                rate_limit=rate_limit,
                response_bytes=response_bytes,
            )
            continue
        if not isinstance(node, Mapping):
            result.errors[alias] = GraphQLErrorInfo(message="repository result is not an object", alias=alias)
            continue
        try:
            default_branch = None
            if mode is GraphQLQueryMode.DEFAULT_BRANCH_TWO_PHASE:
                ref = node.get("defaultBranchRef")
                if ref is None:
                    entries = ()
                elif not isinstance(ref, Mapping):
                    raise ValueError("defaultBranchRef is not an object")
                else:
                    branch = ref.get("name")
                    if not isinstance(branch, str) or not branch:
                        raise ValueError("defaultBranchRef.name is not a string")
                    default_branch = branch
                    entries = ()
            else:
                if mode is GraphQLQueryMode.DEFAULT_BRANCH_ONE_QUERY:
                    ref = node.get("defaultBranchRef")
                    if ref is not None:
                        if not isinstance(ref, Mapping):
                            raise ValueError("defaultBranchRef is not an object")
                        branch = ref.get("name")
                        if not isinstance(branch, str) or not branch:
                            raise ValueError("defaultBranchRef.name is not a string")
                        default_branch = branch
                tree = _tree_from_node(node, mode)
                entries = _entries_from_tree(tree)
            result.results[alias] = WorkflowListing(
                repo=repo,
                outcome=ApiOutcome.SUCCESS,
                entries=entries,
                backend=backend,
                attempts=attempts,
                rate_limit=rate_limit,
                response_bytes=response_bytes,
                default_branch=default_branch,
            )
        except ValueError as exc:
            result.errors[alias] = GraphQLErrorInfo(message=str(exc), alias=alias)

    if result.global_errors:
        # Data that is present and not associated with an alias error remains
        # usable. Missing aliases are handled by the caller as retryable.
        for alias in aliases:
            if alias not in result.results and alias not in result.errors:
                result.errors[alias] = GraphQLErrorInfo(
                    message=result.global_errors[0].message,
                    alias=alias,
                    retryable=result.global_errors[0].retryable,
                    resource_limit=result.global_errors[0].resource_limit,
                    rate_limited=result.global_errors[0].rate_limited,
                )
    return result
