import pytest
from pydantic import ValidationError

from chikiminer.models import InspectionResult, RepositoryRef, RepositoryStatus, WorkflowEntry


def test_repository_ref_has_case_insensitive_canonical_name() -> None:
    ref = RepositoryRef.from_reference(" Microsoft /VSCode ".replace(" ", ""))
    assert ref.owner == "Microsoft"
    assert ref.name == "VSCode"
    assert ref.canonical_name == "microsoft/vscode"


@pytest.mark.parametrize("value", ["owner", "owner/a/b", "/repo", "owner/", "owner/repo name"])
def test_repository_ref_rejects_invalid_references(value: str) -> None:
    with pytest.raises((ValueError, ValidationError)):
        RepositoryRef.from_reference(value)


def test_workflow_entry_normalizes_rest_and_graphql_types() -> None:
    assert WorkflowEntry.from_api_entry("foo.md", "file").is_file
    assert WorkflowEntry.from_api_entry("foo.md", "blob").is_file
    assert not WorkflowEntry.from_api_entry("nested", "dir").is_file


def test_inspection_result_does_not_allow_error_as_no_match() -> None:
    with pytest.raises(ValidationError):
        InspectionResult(repo="owner/repo", status=RepositoryStatus.ERROR, uses_ghaw=False)


def test_inspection_result_requires_boolean_for_success_statuses() -> None:
    with pytest.raises(ValidationError):
        InspectionResult(repo="owner/repo", status=RepositoryStatus.MATCH)

