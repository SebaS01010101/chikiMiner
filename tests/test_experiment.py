from pathlib import Path

import pandas as pd

from chikiminer.experiment import BenchmarkResult, choose_recommendation, equivalent_results, select_fixed_sample
from chikiminer.github.models import ApiOutcome, WorkflowListing
from chikiminer.models import RepositoryRef


def test_fixed_sample_is_reproducible_and_adds_edge_case_probes(tmp_path: Path) -> None:
    source = tmp_path / "input.csv"
    pd.DataFrame([{"name": "Owner/Repo"}, {"name": "owner/repo"}, {"name": "other/project"}]).to_csv(source, index=False)

    sample = select_fixed_sample(source, 2, probes=("probe/repo", "owner/repo"))

    assert [str(repo) for repo in sample] == ["Owner/Repo", "other/project", "probe/repo"]


def test_recommendation_requires_equivalence_and_no_errors() -> None:
    rest = BenchmarkResult("rest", None, None, 10, 10, 1.0, 100, 0, 0, 0, 0, 0.1, 0.2, {"SUCCESS": 10}, equivalent_to_rest=True)
    unstable = BenchmarkResult("graphql", "head", 50, 10, 1, 0.5, 200, 10, 1, 0, 0, 0.1, 0.2, {"SUCCESS": 9, "ERROR": 1}, equivalent_to_rest=True)
    stable = BenchmarkResult("graphql", "two-phase", 25, 10, 2, 0.3, 200, 2, 0, 0, 0, 0.1, 0.2, {"SUCCESS": 10}, native_graphql_successes=10, equivalent_to_rest=True)

    assert choose_recommendation([rest, unstable, stable]) == "graphql:two-phase:batch=25"


def test_experiment_does_not_mark_two_error_sets_as_equivalent() -> None:
    repo = RepositoryRef(owner="owner", name="repo")
    baseline = {repo.canonical_name: WorkflowListing(repo=repo, outcome=ApiOutcome.ERROR)}
    candidate = {repo.canonical_name: WorkflowListing(repo=repo, outcome=ApiOutcome.ERROR)}

    assert equivalent_results(baseline, candidate) is False
