"""Unit tests for bot base classes — search and apply.

Requirement traceability:
    FR-044  Search abstraction (RawJob, BaseSearcher)
    FR-046  Applier abstraction (ApplyResult, BaseApplier)
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from bot.apply.base import ApplyResult, BaseApplier
from bot.search.base import BaseSearcher, RawJob

# ===================================================================
# RawJob
# ===================================================================


class TestRawJob:
    """RawJob dataclass construction and fields."""

    def test_create_raw_job(self):
        job = RawJob(
            title="Software Engineer",
            company="Acme Corp",
            location="Remote",
            salary="$120K",
            description="Build things.",
            apply_url="https://example.com/apply",
            platform="linkedin",
            external_id="abc-123",
            posted_at="2026-03-01",
        )
        assert job.title == "Software Engineer"
        assert job.platform == "linkedin"
        assert job.external_id == "abc-123"

    def test_raw_job_optional_fields(self):
        job = RawJob(
            title="Dev",
            company="Co",
            location="NY",
            salary=None,
            description="",
            apply_url="https://example.com",
            platform="indeed",
            external_id="x",
            posted_at=None,
        )
        assert job.salary is None
        assert job.posted_at is None


# ===================================================================
# BaseSearcher
# ===================================================================


class TestBaseSearcher:
    """BaseSearcher is abstract and cannot be instantiated directly."""

    def test_cannot_instantiate_abstract(self):
        with pytest.raises(TypeError):
            BaseSearcher()

    def test_subclass_must_implement_search(self):
        class IncompleteSearcher(BaseSearcher):
            pass

        with pytest.raises(TypeError):
            IncompleteSearcher()

    def test_concrete_subclass_works(self):
        class TestSearcher(BaseSearcher):
            def search(self, criteria, page=None):
                yield RawJob(
                    title="Test", company="Co", location="Remote",
                    salary=None, description="", apply_url="https://x.com",
                    platform="test", external_id="1", posted_at=None,
                )

        searcher = TestSearcher()
        results = list(searcher.search(None))
        assert len(results) == 1
        assert results[0].title == "Test"


# ===================================================================
# ApplyResult
# ===================================================================


class TestApplyResult:
    """ApplyResult dataclass fields and defaults."""

    def test_success_result(self):
        r = ApplyResult(success=True)
        assert r.success is True
        assert r.error_message is None
        assert r.captcha_detected is False
        assert r.manual_required is False

    def test_failure_with_error(self):
        r = ApplyResult(success=False, error_message="Button not found")
        assert r.success is False
        assert r.error_message == "Button not found"

    def test_captcha_result(self):
        r = ApplyResult(success=False, captcha_detected=True, error_message="CAPTCHA")
        assert r.captcha_detected is True

    def test_manual_required_result(self):
        r = ApplyResult(success=False, manual_required=True, error_message="External ATS")
        assert r.manual_required is True


# ===================================================================
# BaseApplier
# ===================================================================


class TestBaseApplier:
    """BaseApplier abstract class and helper methods."""

    def test_cannot_instantiate_abstract(self):
        with pytest.raises(TypeError):
            BaseApplier(MagicMock())

    def test_detect_captcha_no_captcha(self):
        class TestApplier(BaseApplier):
            def _do_apply(self, job, resume_pdf_path, cover_letter_text, profile):
                return ApplyResult(success=True)

        page = MagicMock()
        page.query_selector.return_value = None
        applier = TestApplier(page)
        assert applier._detect_captcha() is False

    def test_detect_captcha_found(self):
        class TestApplier(BaseApplier):
            def _do_apply(self, job, resume_pdf_path, cover_letter_text, profile):
                return ApplyResult(success=True)

        page = MagicMock()
        # First selector check returns a match
        page.query_selector.return_value = MagicMock()
        applier = TestApplier(page)
        assert applier._detect_captcha() is True

    def test_human_type_calls_type_per_char(self):
        class TestApplier(BaseApplier):
            def _do_apply(self, job, resume_pdf_path, cover_letter_text, profile):
                return ApplyResult(success=True)

        page = MagicMock()
        locator = MagicMock()
        applier = TestApplier(page)

        with patch("bot.apply.base.time.sleep"):
            applier._human_type(locator, "abc")

        assert locator.type.call_count == 3
        locator.type.assert_any_call("a")
        locator.type.assert_any_call("b")
        locator.type.assert_any_call("c")

    def test_random_pause_sleeps(self):
        class TestApplier(BaseApplier):
            def _do_apply(self, job, resume_pdf_path, cover_letter_text, profile):
                return ApplyResult(success=True)

        page = MagicMock()
        applier = TestApplier(page)

        with patch("bot.apply.base.time.sleep") as mock_sleep:
            applier._random_pause(0.1, 0.2)
            mock_sleep.assert_called_once()
            call_arg = mock_sleep.call_args[0][0]
            assert 0.1 <= call_arg <= 0.2
