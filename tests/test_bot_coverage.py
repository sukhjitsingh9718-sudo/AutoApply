"""Additional coverage tests for bot.bot and bot.browser — targeting uncovered branches.

Requirement traceability:
    ME-5   — Test coverage increase to 70%
    FR-042 — Bot main loop (run_bot) — uncovered paths
    FR-043 — Browser session management — uncovered paths
"""

from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import MagicMock, patch

from bot.apply.base import ApplyResult
from bot.state import BotState
from core.filter import ScoredJob

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@dataclass
class FakeRawJob:
    title: str = "Software Engineer"
    company: str = "Acme Corp"
    location: str = "Remote"
    salary: str | None = "$120K"
    description: str = "Build things."
    apply_url: str = "https://boards.greenhouse.io/acme/jobs/1"
    platform: str = "linkedin"
    external_id: str = "job-001"
    posted_at: str | None = None


def _make_scored(raw=None, score=80, pass_filter=True, skip_reason=""):
    raw = raw or FakeRawJob()
    return ScoredJob(
        raw=raw, id="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        score=score, pass_filter=pass_filter, skip_reason=skip_reason,
    )


def _make_config(enabled_platforms=None, apply_mode="full_auto", max_per_day=50,
                 delay=0, search_interval=0):
    cfg = MagicMock()
    cfg.bot.enabled_platforms = enabled_platforms or ["linkedin"]
    cfg.bot.apply_mode = apply_mode
    cfg.bot.max_applications_per_day = max_per_day
    cfg.bot.delay_between_applications_seconds = delay
    cfg.bot.search_interval_seconds = search_interval
    cfg.bot.cover_letter_template = "Dear Hiring Manager..."
    cfg.search_criteria = MagicMock()
    cfg.profile.fallback_resume_path = None
    cfg.profile.full_name = "Test User"
    cfg.profile.email = "test@example.com"
    cfg.profile.phone_full = "+15551234567"
    cfg.profile.location = "Remote"
    cfg.profile.linkedin_url = None
    cfg.profile.portfolio_url = None
    cfg.profile.bio = "A developer."
    cfg.llm = MagicMock()
    return cfg


# ===================================================================
# run_bot — browser crash
# ===================================================================


class TestRunBotBrowserCrash:
    """FR-042: Bot handles BrowserManager crash gracefully."""

    @patch("bot.bot.BrowserManager")
    def test_browser_init_crash(self, mock_bm):
        from bot.bot import run_bot

        mock_bm.side_effect = RuntimeError("Playwright not installed")
        state = BotState()
        state.start()
        db = MagicMock()
        config = _make_config()

        run_bot(state, config, db)
        assert state.errors_today > 0

    @patch("bot.bot.BrowserManager")
    def test_get_page_crash(self, mock_bm):
        from bot.bot import run_bot

        bm_instance = MagicMock()
        bm_instance.get_page.side_effect = RuntimeError("Chromium not installed")
        mock_bm.return_value = bm_instance

        state = BotState()
        state.start()
        db = MagicMock()
        config = _make_config()

        run_bot(state, config, db)
        assert state.errors_today > 0
        bm_instance.close.assert_called_once()


# ===================================================================
# run_bot — multiple jobs in sequence
# ===================================================================


class TestRunBotMultipleJobs:
    """FR-042: Bot processes multiple jobs in sequence."""

    @patch("bot.bot.time.sleep")
    @patch("bot.bot._save_job_description", return_value=None)
    @patch("bot.bot._generate_docs")
    @patch("bot.bot._apply_to_job")
    @patch("bot.bot._save_application")
    @patch("bot.bot.score_job")
    @patch("bot.bot.BrowserManager")
    def test_two_jobs_both_applied(self, mock_bm, mock_score, mock_save,
                                    mock_apply, mock_gen, mock_save_jd, mock_sleep):
        from bot.bot import run_bot

        raw1 = FakeRawJob(title="Job 1", external_id="job-001")
        raw2 = FakeRawJob(title="Job 2", external_id="job-002")

        scored1 = _make_scored(raw=raw1, pass_filter=True)
        scored2 = _make_scored(raw=raw2, pass_filter=True)

        mock_score.side_effect = [scored1, scored2]
        mock_gen.return_value = ("/r.pdf", "/cl.txt", "cover", None)
        mock_apply.return_value = ApplyResult(success=True)

        state = BotState()
        state.start()

        mock_searcher_cls = MagicMock()
        mock_searcher_instance = MagicMock()

        def _search(*args, **kwargs):
            yield raw1
            yield raw2
            state.stop()

        mock_searcher_instance.search.side_effect = _search
        mock_searcher_cls.return_value = mock_searcher_instance

        config = _make_config(enabled_platforms=["linkedin"])
        db = MagicMock()

        with patch("bot.bot.SEARCHERS", {"linkedin": mock_searcher_cls}):
            run_bot(state, config, db)

        assert mock_apply.call_count == 2
        assert state.applied_today == 2

    @patch("bot.bot.time.sleep")
    @patch("bot.bot._save_job_description", return_value=None)
    @patch("bot.bot._generate_docs")
    @patch("bot.bot._apply_to_job")
    @patch("bot.bot._save_application")
    @patch("bot.bot.score_job")
    @patch("bot.bot.BrowserManager")
    def test_first_filtered_second_applied(self, mock_bm, mock_score, mock_save,
                                            mock_apply, mock_gen, mock_save_jd, mock_sleep):
        from bot.bot import run_bot

        raw1 = FakeRawJob(title="Bad Job", external_id="job-001")
        raw2 = FakeRawJob(title="Good Job", external_id="job-002")

        scored_fail = _make_scored(raw=raw1, pass_filter=False, skip_reason="Too low")
        scored_pass = _make_scored(raw=raw2, pass_filter=True)

        mock_score.side_effect = [scored_fail, scored_pass]
        mock_gen.return_value = ("/r.pdf", "/cl.txt", "cover", None)
        mock_apply.return_value = ApplyResult(success=True)

        state = BotState()
        state.start()

        mock_searcher_cls = MagicMock()
        mock_searcher_instance = MagicMock()

        def _search(*args, **kwargs):
            yield raw1
            yield raw2
            state.stop()

        mock_searcher_instance.search.side_effect = _search
        mock_searcher_cls.return_value = mock_searcher_instance

        config = _make_config(enabled_platforms=["linkedin"])
        db = MagicMock()

        with patch("bot.bot.SEARCHERS", {"linkedin": mock_searcher_cls}):
            run_bot(state, config, db)

        assert mock_apply.call_count == 1
        assert state.applied_today == 1
        assert state.jobs_found_today == 2


# ===================================================================
# run_bot — review mode stop decision
# ===================================================================


class TestRunBotReviewStop:
    """FR-042: Review mode — user decides to stop."""

    @patch("bot.bot.time.sleep")
    @patch("bot.bot._save_job_description", return_value=None)
    @patch("bot.bot._generate_docs")
    @patch("bot.bot._wait_for_review")
    @patch("bot.bot._apply_to_job")
    @patch("bot.bot._save_application")
    @patch("bot.bot.score_job")
    @patch("bot.bot.BrowserManager")
    def test_review_stop_breaks_loop(self, mock_bm, mock_score, mock_save,
                                      mock_apply, mock_review, mock_gen,
                                      mock_save_jd, mock_sleep):
        from bot.bot import run_bot

        raw = FakeRawJob()
        scored = _make_scored(raw=raw, pass_filter=True)
        mock_score.return_value = scored
        mock_gen.return_value = ("/r.pdf", "/cl.txt", "cover", None)
        mock_review.return_value = ("stop", None)

        state = BotState()
        state.start()

        mock_searcher_cls = MagicMock()
        mock_searcher_instance = MagicMock()

        def _search(*args, **kwargs):
            yield raw
            state.stop()

        mock_searcher_instance.search.side_effect = _search
        mock_searcher_cls.return_value = mock_searcher_instance

        config = _make_config(enabled_platforms=["linkedin"], apply_mode="review")
        db = MagicMock()

        with patch("bot.bot.SEARCHERS", {"linkedin": mock_searcher_cls}):
            run_bot(state, config, db)

        mock_apply.assert_not_called()

    @patch("bot.bot.time.sleep")
    @patch("bot.bot._save_job_description", return_value=None)
    @patch("bot.bot._generate_docs")
    @patch("bot.bot._wait_for_review")
    @patch("bot.bot._apply_to_job")
    @patch("bot.bot._save_application")
    @patch("bot.bot.score_job")
    @patch("bot.bot.BrowserManager")
    def test_review_approve(self, mock_bm, mock_score, mock_save,
                             mock_apply, mock_review, mock_gen,
                             mock_save_jd, mock_sleep):
        from bot.bot import run_bot

        raw = FakeRawJob()
        scored = _make_scored(raw=raw, pass_filter=True)
        mock_score.return_value = scored
        mock_gen.return_value = ("/r.pdf", "/cl.txt", "cover", None)
        mock_review.return_value = ("approve", None)
        mock_apply.return_value = ApplyResult(success=True)

        state = BotState()
        state.start()

        mock_searcher_cls = MagicMock()
        mock_searcher_instance = MagicMock()

        def _search(*args, **kwargs):
            yield raw
            state.stop()

        mock_searcher_instance.search.side_effect = _search
        mock_searcher_cls.return_value = mock_searcher_instance

        config = _make_config(enabled_platforms=["linkedin"], apply_mode="watch")
        db = MagicMock()

        with patch("bot.bot.SEARCHERS", {"linkedin": mock_searcher_cls}):
            run_bot(state, config, db)

        mock_apply.assert_called_once()


# ===================================================================
# run_bot — emit inner function
# ===================================================================


class TestRunBotEmitBranches:
    """FR-042: emit() inner function — DB and SocketIO failure paths."""

    @patch("bot.bot.BrowserManager")
    def test_emit_both_socketio_and_db_fail(self, mock_bm):
        from bot.bot import run_bot

        state = MagicMock()
        state.stop_flag = True
        config = _make_config(enabled_platforms=["nonexistent"])
        db = MagicMock()
        db.save_feed_event.side_effect = Exception("DB dead")
        emit_func = MagicMock(side_effect=Exception("SocketIO dead"))

        # With no enabled searchers, it returns early without error
        run_bot(state, config, db, emit_func)


# ===================================================================
# _save_job_description — edge cases
# ===================================================================


class TestSaveJobDescriptionEdgeCases:
    """FR-042: Job description saving — edge cases."""

    def test_none_salary_and_location(self, tmp_path):
        from bot.bot import _save_job_description
        from bot.search.base import RawJob

        raw = RawJob(
            title="Engineer", company="Corp",
            location=None, salary=None,
            description="Build things.",
            apply_url="https://example.com/jobs/1",
            platform="linkedin", external_id="abcd1234-rest",
            posted_at=None,
        )
        scored = ScoredJob(
            raw=raw, id="test-uuid", score=80,
            pass_filter=True, skip_reason="",
        )
        result = _save_job_description(scored, tmp_path)
        assert result is not None
        content = result.read_text(encoding="utf-8")
        assert "Engineer" in content
        assert "Corp" in content

    def test_empty_description(self, tmp_path):
        from bot.bot import _save_job_description
        from bot.search.base import RawJob

        raw = RawJob(
            title="Dev", company="Startup",
            location="Remote", salary="$100K",
            description="",
            apply_url="https://example.com/jobs/1",
            platform="indeed", external_id="efgh5678-rest",
            posted_at=None,
        )
        scored = ScoredJob(
            raw=raw, id="test-uuid2", score=70,
            pass_filter=True, skip_reason="",
        )
        result = _save_job_description(scored, tmp_path)
        assert result is not None

    def test_unicode_company_name(self, tmp_path):
        from bot.bot import _save_job_description
        from bot.search.base import RawJob

        raw = RawJob(
            title="Engineer", company="Caf\u00e9 & B\u00e4r GmbH",
            location="Berlin", salary="\u20ac80K",
            description="German startup.",
            apply_url="https://example.com/jobs/3",
            platform="greenhouse", external_id="ijkl9012-rest",
            posted_at=None,
        )
        scored = ScoredJob(
            raw=raw, id="test-uuid3", score=75,
            pass_filter=True, skip_reason="",
        )
        result = _save_job_description(scored, tmp_path)
        assert result is not None
        # Filename should be safe
        assert "/" not in result.name
        assert "\\" not in result.name


# ===================================================================
# _generate_docs — fallback paths
# ===================================================================


class TestGenerateDocsBranches:
    """FR-042: Document generation — edge cases."""

    @patch("core.ai_engine.generate_documents")
    def test_fallback_resume_not_found(self, mock_gen, tmp_path):
        from bot.bot import _generate_docs

        mock_gen.side_effect = RuntimeError("fail")
        scored = _make_scored()
        config = _make_config()
        config.profile.fallback_resume_path = str(tmp_path / "nonexistent.pdf")

        r, c, text, version_meta = _generate_docs(scored, config, tmp_path)
        assert r is None  # fallback file doesn't exist
        assert c is None

    @patch("core.ai_engine.generate_documents")
    def test_fallback_no_cover_template(self, mock_gen, tmp_path):
        from bot.bot import _generate_docs

        mock_gen.side_effect = RuntimeError("fail")
        scored = _make_scored()
        config = _make_config()
        config.bot.cover_letter_template = ""

        r, c, text, version_meta = _generate_docs(scored, config, tmp_path)
        assert text == ""


# ===================================================================
# BrowserManager — additional edge cases
# ===================================================================


class TestBrowserManagerEdgeCases:
    """FR-043: BrowserManager — additional coverage."""

    def test_get_page_returns_new_page_when_closed(self):
        from bot.browser import BrowserManager

        bm = BrowserManager.__new__(BrowserManager)
        mock_page = MagicMock()
        mock_page.is_closed.return_value = True  # Current page is closed
        bm._page = mock_page
        bm._playwright = MagicMock()
        bm._context = None
        bm.headless = True
        bm.profile_dir = MagicMock()

        mock_context = MagicMock()
        new_page = MagicMock()
        mock_context.new_page.return_value = new_page
        bm._playwright.chromium.launch_persistent_context.return_value = mock_context

        with patch("bot.browser._find_system_chrome", return_value=None):
            with patch.dict("sys.modules", {"playwright": MagicMock(),
                                            "playwright.sync_api": MagicMock()}):
                result = bm.get_page()

        assert result is new_page

    def test_init_creates_profile_dir(self, tmp_path, monkeypatch):
        from bot.browser import BrowserManager

        monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
        config = MagicMock()
        config.bot.apply_mode = "full_auto"

        bm = BrowserManager(config)
        assert bm.profile_dir.exists()
        assert bm.headless is True
        assert bm._playwright is None


# ===================================================================
# _apply_to_job — ATS detection routing
# ===================================================================


class TestApplyToJobRouting:
    """FR-042: _apply_to_job uses detect_ats for platform routing."""

    def test_ats_detected_overrides_raw_platform(self):
        from bot.bot import _apply_to_job

        # Job has platform "linkedin" but URL is a Greenhouse URL
        raw = FakeRawJob(
            apply_url="https://boards.greenhouse.io/acme/jobs/1",
            platform="linkedin",
        )
        scored = _make_scored(raw=raw)

        mock_applier_cls = MagicMock()
        mock_instance = MagicMock()
        mock_instance.apply.return_value = ApplyResult(success=True)
        mock_applier_cls.return_value = mock_instance

        with patch("bot.bot.APPLIERS", {"greenhouse": mock_applier_cls}):
            result = _apply_to_job(scored, "/r.pdf", "cover", _make_config(), MagicMock())

        assert result.success is True
        mock_applier_cls.assert_called_once()

    def test_no_ats_detected_uses_raw_platform(self):
        from bot.bot import _apply_to_job

        raw = FakeRawJob(
            apply_url="https://custom-portal.com/apply",
            platform="linkedin",
        )
        scored = _make_scored(raw=raw)

        mock_applier_cls = MagicMock()
        mock_instance = MagicMock()
        mock_instance.apply.return_value = ApplyResult(success=True)
        mock_applier_cls.return_value = mock_instance

        with patch("bot.bot.APPLIERS", {"linkedin": mock_applier_cls}):
            result = _apply_to_job(scored, "/r.pdf", "cover", _make_config(), MagicMock())

        assert result.success is True
