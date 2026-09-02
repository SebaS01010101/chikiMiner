from chikiminer.detector import uses_ghaw


def test_matching_pair_is_detected() -> None:
    assert uses_ghaw(["report.md", "report.lock.yml"])


def test_markdown_without_lock_is_not_detected() -> None:
    assert not uses_ghaw(["report.md"])


def test_lock_without_markdown_is_not_detected() -> None:
    assert not uses_ghaw(["report.lock.yml"])


def test_different_bases_are_not_detected() -> None:
    assert not uses_ghaw(["report.md", "other.lock.yml"])


def test_pair_with_unrelated_file_is_detected() -> None:
    assert uses_ghaw(["a.md", "a.lock.yml", "ci.yml"])


def test_yaml_extension_does_not_count() -> None:
    assert not uses_ghaw(["foo.md", "foo.lock.yaml"])


def test_suffix_matching_is_case_sensitive() -> None:
    assert not uses_ghaw(["foo.MD", "foo.lock.yml"])


def test_empty_input_is_not_detected() -> None:
    assert not uses_ghaw([])


def test_directory_like_entry_is_not_a_file() -> None:
    assert not uses_ghaw(["foo.md/", "foo.lock.yml/"])

