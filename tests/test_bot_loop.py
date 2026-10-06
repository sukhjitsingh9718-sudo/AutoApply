"""Unit tests for bot.bot — main bot loop and helper functions.

Requirement traceability:
    FR-042  Bot main loop (run_bot)
    FR-050  Search-filter-generate-apply pipeline
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
        raw=raw,
        id="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        score=score,
        pass_filter=pass_filter,
        skip_reason=skip_reason,
    )


def _make_config(
    enabled_platforms=None,
    apply_mode="full_auto",
    max_per_day=50,
    delay=0,
    search_interval=0,
):
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


def _make_state(stop_after_first=True):
    """Create a BotState-like mock that stops after first loop iteration."""
    state = MagicMock()
    state.status = "running"
    state.applied_today = 0
    if stop_after_first:
        state.stop_flag = False
        # Set stop_flag to True after first check
        _call_count = {"n": 0}

        def _stop_flag_getter():
            _call_count["n"] += 1
            return _call_count["n"] > 2

        type(state).stop_flag = property(lambda self: _stop_flag_getter())
    else:
        state.stop_flag = True  # Immediate stop
    return state


# ===================================================================
# _wait_while_paused
# ===================================================================


class TestWaitWhilePaused:
    """Tests for _wait_while_paused helper."""

    def test_returns_immediately_when_running(self):
        from bot.bot import _wait_while_paused

        state = MagicMock()
        state.status = "running"
        state.stop_flag = False
        _wait_while_paused(state)  # Should return immediately

    def test_returns_when_stop_flag_set(self):
        from bot.bot import _wait_while_paused

        state = MagicMock()
        state.status = "paused"
        state.stop_flag = True
        _wait_while_paused(state)  # Should return because stop_flag is True

    @patch("bot.bot.time.sleep")
    def test_loops_while_paused(self, mock_sleep):
        from bot.bot import _wait_while_paused

        state = MagicMock()
        # First check: paused+no stop, second check: running
        state.stop_flag = False
        state.status = "paused"

        call_count = 0

        def _dynamic_status():
            nonlocal call_count
            call_count += 1
            return "paused" if call_count <= 1 else "running"

        type(state).status = property(lambda self: _dynamic_status())

        _wait_while_paused(state)
        mock_sleep.assert_called_once_with(1)


# ===================================================================
# _interruptible_sleep
# ===================================================================


class TestInterruptibleSleep:
    """Tests for _interruptible_sleep helper."""

    @patch("bot.bot.time.sleep")
    def test_sleeps_full_duration(self, mock_sleep):
        from bot.bot import _interruptible_sleep

        state = MagicMock()
        state.stop_flag = False
        _interruptible_sleep(state, 3)
        assert mock_sleep.call_count == 3

    @patch("bot.bot.time.sleep")
    def test_stops_early_on_flag(self, mock_sleep):
        from bot.bot import _interruptible_sleep

        state = MagicMock()
        state.stop_flag = True
        _interruptible_sleep(state, 10)
        mock_sleep.assert_not_called()


# ===================================================================
# _wait_for_review
# ===================================================================


class TestWaitForReview:
    """Tests for _wait_for_review wrapper."""

    def test_calls_state_methods(self):
        from bot.bot import _wait_for_review

        state = MagicMock()
        state.wait_for_review.return_value = ("approve", None)
        decision, cl = _wait_for_review(state)
        state.begin_review.assert_called_once()
        state.wait_for_review.assert_called_once()
        assert decision == "approve"
        assert cl is None


# ===================================================================
# _generate_docs
# ===================================================================


class TestGenerateDocs:
    """Tests for _generate_docs helper."""

    @patch("core.ai_engine.generate_documents")
    def test_success_path(self, mock_gen, tmp_path):
        from bot.bot import _generate_docs

        resume_path = tmp_path / "resume.pdf"
        cl_path = tmp_path / "cl.txt"
        cl_path.write_text("Dear Sir...", encoding="utf-8")
        version_meta = {"resume_md_path": str(tmp_path / "resume.md"),
                        "resume_pdf_path": str(resume_path),
                        "llm_provider": "anthropic", "llm_model": "test"}
        mock_gen.return_value = (resume_path, cl_path, version_meta)

        scored = _make_scored()
        config = _make_config()

        r, c, text, meta = _generate_docs(scored, config, tmp_path)
        assert r == resume_path
        assert c == cl_path
        assert text == "Dear Sir..."
        assert meta == version_meta

    @patch("core.ai_engine.generate_documents")
    def test_fallback_on_failure(self, mock_gen, tmp_path):
        from bot.bot import _generate_docs

        mock_gen.side_effect = RuntimeError("API down")

        scored = _make_scored()
        config = _make_config()
        config.profile.fallback_resume_path = str(tmp_path / "fallback.pdf")
        # Create the fallback file
        (tmp_path / "fallback.pdf").write_bytes(b"fake pdf")

        r, c, text, meta = _generate_docs(scored, config, tmp_path)
        assert r == tmp_path / "fallback.pdf"
        assert c is None
        assert text == "Dear Hiring Manager..."
        assert meta is None

    @patch("core.ai_engine.generate_documents")
    def test_fallback_no_resume(self, mock_gen, tmp_path):
        from bot.bot import _generate_docs

        mock_gen.side_effect = RuntimeError("fail")

        scored = _make_scored()
        config = _make_config()
        # No fallback_resume_path

        r, c, text, meta = _generate_docs(scored, config, tmp_path)
        assert r is None
        assert c is None
        assert meta is None


# ===================================================================
# _apply_to_job
# ===================================================================


class TestApplyToJob:
    """Tests for _apply_to_job helper."""

    @patch("bot.bot.APPLIERS", {"greenhouse": MagicMock})
    def test_known_platform(self):
        from bot.bot import _apply_to_job

        scored = _make_scored()
        mock_applier_cls = MagicMock()
        mock_applier_instance = MagicMock()
        mock_applier_instance.apply.return_value = ApplyResult(success=True)
        mock_applier_cls.return_value = mock_applier_instance

        with patch("bot.bot.APPLIERS", {"greenhouse": mock_applier_cls}):
            result = _apply_to_job(scored, "/resume.pdf", "cover", _make_config(), MagicMock())
        assert result.success is True

    def test_unknown_platform(self):
        from bot.bot import _apply_to_job

        scored = _make_scored(raw=FakeRawJob(apply_url="https://custom.com/apply", platform="custom"))
        result = _apply_to_job(scored, "/resume.pdf", "cover", _make_config(), MagicMock())
        assert result.success is False
        assert result.manual_required is True
        assert "No applier" in result.error_message


# ===================================================================
# _save_application
# ===================================================================


class TestSaveApplication:
    """Tests for _save_application helper."""

    def test_success_status(self):
        from bot.bot import _save_application

        db = MagicMock()
        scored = _make_scored()
        result = ApplyResult(success=True)
        _save_application(db, scored, "/r.pdf", "/cl.txt", "cover", result)
        db.save_application.assert_called_once()
        call_kwargs = db.save_application.call_args[1]
        assert call_kwargs["status"] == "applied"

    def test_manual_required_status(self):
        from bot.bot import _save_application

        db = MagicMock()
        scored = _make_scored()
        result = ApplyResult(success=False, manual_required=True)
        _save_application(db, scored, None, None, "", result)
        call_kwargs = db.save_application.call_args[1]
        assert call_kwargs["status"] == "manual_required"

    def test_error_status(self):
        from bot.bot import _save_application

        db = MagicMock()
        scored = _make_scored()
        result = ApplyResult(success=False, error_message="Failed")
        _save_application(db, scored, None, None, "", result)
        call_kwargs = db.save_application.call_args[1]
        assert call_kwargs["status"] == "error"

    def test_db_exception_handled(self):
        from bot.bot import _save_application

        db = MagicMock()
        db.save_application.side_effect = Exception("DB error")
        scored = _make_scored()
        result = ApplyResult(success=True)
        # Should not raise
        _save_application(db, scored, None, None, "", result)

    def test_description_path_saved(self):
        from bot.bot import _save_application

        db = MagicMock()
        scored = _make_scored()
        result = ApplyResult(success=True)
        _save_application(db, scored, None, None, "", result, description_path="/desc.html")
        call_kwargs = db.save_application.call_args[1]
        assert call_kwargs["description_path"] == "/desc.html"


# ===================================================================
# emit (nested in run_bot)
# ===================================================================


class TestRunBotEmit:
    """Tests for the emit function behavior inside run_bot."""

    @patch("bot.bot.BrowserManager")
    def test_emit_socketio_failure_handled(self, mock_bm):
        from bot.bot import run_bot

        state = MagicMock()
        state.stop_flag = True
        config = _make_config()
        db = MagicMock()
        emit_func = MagicMock(side_effect=Exception("SocketIO dead"))

        # run_bot exits immediately since stop_flag=True
        run_bot(state, config, db, emit_func)

    @patch("bot.bot.BrowserManager")
    def test_emit_db_save_failure_handled(self, mock_bm):
        from bot.bot import run_bot

        state = MagicMock()
        state.stop_flag = True
        config = _make_config()
        db = MagicMock()
        db.save_feed_event.side_effect = Exception("DB write fail")

        run_bot(state, config, db)


# ===================================================================
# run_bot — no enabled searchers
# ===================================================================


class TestRunBotNoSearchers:
    """Tests for run_bot when no platforms are enabled."""

    @patch("bot.bot.BrowserManager")
    def test_no_enabled_platforms(self, mock_bm):
        from bot.bot import run_bot

        state = MagicMock()
        state.stop_flag = False
        config = _make_config(enabled_platforms=["nonexistent"])
        db = MagicMock()

        run_bot(state, config, db)
        # Should return without crashing


# ===================================================================
# run_bot — main loop paths
# ===================================================================


class TestRunBotMainLoop:
    """Tests for run_bot main loop paths."""

    def _make_bot_state_with_searcher(self, raw_jobs, stop_after_apply=True):
        """Create a real BotState and a searcher that yields jobs then sets stop."""
        state = BotState()
        state.start()

        mock_searcher_cls = MagicMock()
        mock_searcher_instance = MagicMock()

        def _search(*args, **kwargs):
            for job in raw_jobs:
                yield job
            # After yielding all jobs, stop the bot
            if stop_after_apply:
                state.stop()

        mock_searcher_instance.search.side_effect = _search
        mock_searcher_cls.return_value = mock_searcher_instance
        return state, mock_searcher_cls

    @patch("bot.bot.time.sleep")
    @patch("bot.bot._save_job_description", return_value=None)
    @patch("bot.bot._generate_docs")
    @patch("bot.bot._apply_to_job")
    @patch("bot.bot._save_application")
    @patch("bot.bot.score_job")
    @patch("bot.bot.BrowserManager")
    def test_full_apply_cycle(self, mock_bm, mock_score, mock_save, mock_apply,
                               mock_gen, mock_save_jd, mock_sleep):
        from bot.bot import run_bot

        raw = FakeRawJob()
        scored = _make_scored(raw=raw, score=80, pass_filter=True)
        mock_score.return_value = scored
        mock_gen.return_value = ("/resume.pdf", "/cl.txt", "cover text", None)
        mock_apply.return_value = ApplyResult(success=True)

        state, searcher_cls = self._make_bot_state_with_searcher([raw])
        config = _make_config(enabled_platforms=["linkedin"])
        db = MagicMock()

        with patch("bot.bot.SEARCHERS", {"linkedin": searcher_cls}):
            run_bot(state, config, db)

        mock_score.assert_called()
        mock_apply.assert_called()

    @patch("bot.bot.time.sleep")
    @patch("bot.bot._save_job_description", return_value=None)
    @patch("bot.bot.score_job")
    @patch("bot.bot.BrowserManager")
    def test_filtered_job_skipped(self, mock_bm, mock_score, mock_save_jd, mock_sleep):
        from bot.bot import run_bot

        raw = FakeRawJob()
        scored = _make_scored(raw=raw, pass_filter=False, skip_reason="Too low")
        mock_score.return_value = scored

        state, searcher_cls = self._make_bot_state_with_searcher([raw])
        config = _make_config(enabled_platforms=["linkedin"])
        db = MagicMock()

        with patch("bot.bot.SEARCHERS", {"linkedin": searcher_cls}):
            run_bot(state, config, db)

    @patch("bot.bot.time.sleep")
    @patch("bot.bot.BrowserManager")
    def test_daily_limit_reached(self, mock_bm, mock_sleep):
        from bot.bot import run_bot

        raw = FakeRawJob()
        state = BotState()
        state.start()
        # Manually set applied_today above the limit
        state._applied_today = 50

        search_count = {"n": 0}
        mock_searcher_cls = MagicMock()
        mock_searcher_instance = MagicMock()

        def _search(*args, **kwargs):
            search_count["n"] += 1
            if search_count["n"] > 1:
                state.stop()
                return iter([])
            return iter([raw])

        mock_searcher_instance.search.side_effect = _search
        mock_searcher_cls.return_value = mock_searcher_instance

        config = _make_config(enabled_platforms=["linkedin"], max_per_day=50)
        db = MagicMock()

        with patch("bot.bot.SEARCHERS", {"linkedin": mock_searcher_cls}):
            run_bot(state, config, db)

    @patch("bot.bot.time.sleep")
    @patch("bot.bot._save_job_description", return_value=None)
    @patch("bot.bot._generate_docs")
    @patch("bot.bot._apply_to_job")
    @patch("bot.bot._save_application")
    @patch("bot.bot.score_job")
    @patch("bot.bot.BrowserManager")
    def test_captcha_detected(self, mock_bm, mock_score, mock_save, mock_apply,
                               mock_gen, mock_save_jd, mock_sleep):
        from bot.bot import run_bot

        raw = FakeRawJob()
        scored = _make_scored(raw=raw, pass_filter=True)
        mock_score.return_value = scored
        mock_gen.return_value = ("/r.pdf", "/cl.txt", "cover", None)
        mock_apply.return_value = ApplyResult(success=False, captcha_detected=True)

        state, searcher_cls = self._make_bot_state_with_searcher([raw])
        config = _make_config(enabled_platforms=["linkedin"])
        db = MagicMock()

        with patch("bot.bot.SEARCHERS", {"linkedin": searcher_cls}):
            run_bot(state, config, db)

        assert state.errors_today > 0

    @patch("bot.bot.time.sleep")
    @patch("bot.bot._save_job_description", return_value=None)
    @patch("bot.bot._generate_docs")
    @patch("bot.bot._apply_to_job")
    @patch("bot.bot._save_application")
    @patch("bot.bot.score_job")
    @patch("bot.bot.BrowserManager")
    def test_apply_error(self, mock_bm, mock_score, mock_save, mock_apply,
                          mock_gen, mock_save_jd, mock_sleep):
        from bot.bot import run_bot

        raw = FakeRawJob()
        scored = _make_scored(raw=raw, pass_filter=True)
        mock_score.return_value = scored
        mock_gen.return_value = ("/r.pdf", "/cl.txt", "cover", None)
        mock_apply.return_value = ApplyResult(success=False, error_message="Button missing")

        state, searcher_cls = self._make_bot_state_with_searcher([raw])
        config = _make_config(enabled_platforms=["linkedin"])
        db = MagicMock()

        with patch("bot.bot.SEARCHERS", {"linkedin": searcher_cls}):
            run_bot(state, config, db)

        assert state.errors_today > 0

    @patch("bot.bot.time.sleep")
    @patch("bot.bot.BrowserManager")
    def test_search_exception_handled(self, mock_bm, mock_sleep):
        from bot.bot import run_bot

        mock_searcher_cls = MagicMock()
        mock_searcher_instance = MagicMock()

        state = BotState()
        state.start()

        def _bad_search(*a, **kw):
            state.stop()
            raise Exception("Network error")

        mock_searcher_instance.search.side_effect = _bad_search
        mock_searcher_cls.return_value = mock_searcher_instance

        config = _make_config(enabled_platforms=["linkedin"])
        db = MagicMock()

        with patch("bot.bot.SEARCHERS", {"linkedin": mock_searcher_cls}):
            run_bot(state, config, db)

        assert state.errors_today > 0


# ===================================================================
# run_bot — review mode
# ===================================================================


class TestRunBotReviewMode:
    """Tests for review mode branches."""

    def _make_review_setup(self, raw_jobs):
        """Create BotState and searcher for review mode tests."""
        state = BotState()
        state.start()

        mock_searcher_cls = MagicMock()
        mock_searcher_instance = MagicMock()

        def _search(*args, **kwargs):
            for job in raw_jobs:
                yield job
            state.stop()

        mock_searcher_instance.search.side_effect = _search
        mock_searcher_cls.return_value = mock_searcher_instance
        return state, mock_searcher_cls

    @patch("bot.bot.time.sleep")
    @patch("bot.bot._save_job_description", return_value=None)
    @patch("bot.bot._generate_docs")
    @patch("bot.bot._wait_for_review")
    @patch("bot.bot._apply_to_job")
    @patch("bot.bot._save_application")
    @patch("bot.bot.score_job")
    @patch("bot.bot.BrowserManager")
    def test_review_skip(self, mock_bm, mock_score, mock_save, mock_apply,
                          mock_review, mock_gen, mock_save_jd, mock_sleep):
        from bot.bot import run_bot

        raw = FakeRawJob()
        scored = _make_scored(raw=raw, pass_filter=True)
        mock_score.return_value = scored
        mock_gen.return_value = ("/r.pdf", "/cl.txt", "cover", None)
        mock_review.return_value = ("skip", None)

        state, searcher_cls = self._make_review_setup([raw])
        config = _make_config(enabled_platforms=["linkedin"], apply_mode="review")
        db = MagicMock()

        with patch("bot.bot.SEARCHERS", {"linkedin": searcher_cls}):
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
    def test_review_manual(self, mock_bm, mock_score, mock_save, mock_apply,
                            mock_review, mock_gen, mock_save_jd, mock_sleep):
        from bot.bot import run_bot

        raw = FakeRawJob()
        scored = _make_scored(raw=raw, pass_filter=True)
        mock_score.return_value = scored
        mock_gen.return_value = ("/r.pdf", "/cl.txt", "cover", None)
        mock_review.return_value = ("manual", None)

        state, searcher_cls = self._make_review_setup([raw])
        config = _make_config(enabled_platforms=["linkedin"], apply_mode="review")
        db = MagicMock()

        with patch("bot.bot.SEARCHERS", {"linkedin": searcher_cls}):
            run_bot(state, config, db)

        mock_apply.assert_not_called()
        mock_save.assert_called()

    @patch("bot.bot.time.sleep")
    @patch("bot.bot._save_job_description", return_value=None)
    @patch("bot.bot._generate_docs")
    @patch("bot.bot._wait_for_review")
    @patch("bot.bot._apply_to_job")
    @patch("bot.bot._save_application")
    @patch("bot.bot.score_job")
    @patch("bot.bot.BrowserManager")
    def test_review_edit(self, mock_bm, mock_score, mock_save, mock_apply,
                          mock_review, mock_gen, mock_save_jd, mock_sleep):
        from bot.bot import run_bot

        raw = FakeRawJob()
        scored = _make_scored(raw=raw, pass_filter=True)
        mock_score.return_value = scored
        mock_gen.return_value = ("/r.pdf", "/cl.txt", "original cover", None)
        mock_review.return_value = ("edit", "edited cover letter")
        mock_apply.return_value = ApplyResult(success=True)

        state, searcher_cls = self._make_review_setup([raw])
        config = _make_config(enabled_platforms=["linkedin"], apply_mode="watch")
        db = MagicMock()

        with patch("bot.bot.SEARCHERS", {"linkedin": searcher_cls}):
            run_bot(state, config, db)

        mock_apply.assert_called()

    @patch("bot.bot.time.sleep")
    @patch("bot.bot._save_job_description", return_value=None)
    @patch("bot.bot._generate_docs")
    @patch("bot.bot._wait_for_review")
    @patch("bot.bot._apply_to_job")
    @patch("bot.bot._save_application")
    @patch("bot.bot.score_job")
    @patch("bot.bot.BrowserManager")
    def test_review_stop(self, mock_bm, mock_score, mock_save, mock_apply,
                          mock_review, mock_gen, mock_save_jd, mock_sleep):
        """Review decision='stop' breaks the inner loop."""
        from bot.bot import run_bot

        raw = FakeRawJob()
        scored = _make_scored(raw=raw, pass_filter=True)
        mock_score.return_value = scored
        mock_gen.return_value = ("/r.pdf", "/cl.txt", "cover", None)

        state = BotState()
        state.start()

        def _review_stop(*a, **kw):
            state.stop()  # Ensure bot exits after review
            return ("stop", None)

        mock_review.side_effect = _review_stop

        mock_searcher_cls = MagicMock()
        mock_searcher_instance = MagicMock()
        mock_searcher_instance.search.return_value = iter([raw])
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
    def test_review_approve(self, mock_bm, mock_score, mock_save, mock_apply,
                             mock_review, mock_gen, mock_save_jd, mock_sleep):
        """Review decision='approve' proceeds to apply."""
        from bot.bot import run_bot

        raw = FakeRawJob()
        scored = _make_scored(raw=raw, pass_filter=True)
        mock_score.return_value = scored
        mock_gen.return_value = ("/r.pdf", "/cl.txt", "cover", None)
        mock_review.return_value = ("approve", None)
        mock_apply.return_value = ApplyResult(success=True)

        state, searcher_cls = self._make_review_setup([raw])
        config = _make_config(enabled_platforms=["linkedin"], apply_mode="review")
        db = MagicMock()

        with patch("bot.bot.SEARCHERS", {"linkedin": searcher_cls}):
            run_bot(state, config, db)

        mock_apply.assert_called()


# ===================================================================
# _save_job_description
# ===================================================================


class TestSaveJobDescription:
    """Tests for _save_job_description helper."""

    def test_saves_html_file(self, tmp_path):
        from bot.bot import _save_job_description

        raw = FakeRawJob(
            external_id="abcdef12-long-id",
            company="Acme Corp",
            title="Software Engineer",
            location="Remote",
            salary="$120K",
            description="Great opportunity.\n\nApply now.",
            apply_url="https://example.com/apply",
        )
        scored = _make_scored(raw=raw)
        result = _save_job_description(scored, tmp_path)

        assert result is not None
        assert result.exists()
        content = result.read_text(encoding="utf-8")
        assert "Software Engineer" in content
        assert "Acme Corp" in content
        assert "Great opportunity." in content
        assert "<p>" in content  # multi-paragraph conversion

    def test_returns_none_on_error(self, tmp_path):
        from bot.bot import _save_job_description

        scored = _make_scored()
        with patch("pathlib.Path.write_text", side_effect=OSError("Disk full")):
            result = _save_job_description(scored, tmp_path)
        assert result is None

    def test_html_escaping_in_description(self, tmp_path):
        from bot.bot import _save_job_description

        raw = FakeRawJob(
            description='<script>alert("xss")</script>',
            company='A&B "Corp"',
        )
        scored = _make_scored(raw=raw)
        result = _save_job_description(scored, tmp_path)

        assert result is not None
        content = result.read_text(encoding="utf-8")
        assert "<script>" not in content
        assert "&lt;script&gt;" in content
        assert "A&amp;B" in content

    def test_single_paragraph_description(self, tmp_path):
        from bot.bot import _save_job_description

        raw = FakeRawJob(description="Line one\nLine two\nLine three")
        scored = _make_scored(raw=raw)
        result = _save_job_description(scored, tmp_path)

        assert result is not None
        content = result.read_text(encoding="utf-8")
        assert "<br>" in content


# ===================================================================
# _esc and _plain_to_html
# ===================================================================


class TestHtmlHelpers:
    """Tests for _esc and _plain_to_html."""

    def test_esc_special_chars(self):
        from bot.bot import _esc

        assert _esc('a&b<c>d"e') == "a&amp;b&lt;c&gt;d&quot;e"

    def test_esc_empty_string(self):
        from bot.bot import _esc

        assert _esc("") == ""

    def test_plain_to_html_single_paragraph(self):
        from bot.bot import _plain_to_html

        result = _plain_to_html("line1\nline2")
        assert "<br>" in result
        assert "<p>" not in result

    def test_plain_to_html_multi_paragraph(self):
        from bot.bot import _plain_to_html

        result = _plain_to_html("para1\n\npara2\n\npara3")
        assert result.count("<p>") == 3

    def test_plain_to_html_escapes_html(self):
        from bot.bot import _plain_to_html

        result = _plain_to_html("<b>bold</b>")
        assert "&lt;b&gt;" in result


# ===================================================================
# run_bot — stop flag mid-loop + crash handler
# ===================================================================


class TestRunBotEdgeCases:
    """Tests for stop_flag checks inside the loop and crash handling."""

    @patch("bot.bot.BrowserManager")
    def test_bot_crash_handler(self, mock_bm):
        """Outer except block catches unexpected crash."""
        from bot.bot import run_bot

        mock_bm.side_effect = RuntimeError("Browser init failed")

        state = BotState()
        state.start()
        config = _make_config()
        db = MagicMock()
        emit_func = MagicMock()

        run_bot(state, config, db, emit_func)

        assert state.errors_today > 0
        emit_func.assert_called()
        last_call_data = emit_func.call_args[0][1]
        assert last_call_data["type"] == "ERROR"
        assert "crashed" in last_call_data.get("message", "").lower()

    @patch("bot.bot.time.sleep")
    @patch("bot.bot.BrowserManager")
    def test_stop_flag_between_searchers(self, mock_bm, mock_sleep):
        """Bot stops between searchers when stop_flag is set."""
        from bot.bot import run_bot

        state = BotState()
        state.start()

        searcher1_cls = MagicMock()
        searcher1_instance = MagicMock()

        def _search1(*a, **kw):
            state.stop()
            return iter([])

        searcher1_instance.search.side_effect = _search1
        searcher1_cls.return_value = searcher1_instance

        searcher2_cls = MagicMock()
        searcher2_instance = MagicMock()
        searcher2_cls.return_value = searcher2_instance

        config = _make_config(enabled_platforms=["linkedin", "indeed"])
        db = MagicMock()

        with patch("bot.bot.SEARCHERS", {"linkedin": searcher1_cls, "indeed": searcher2_cls}):
            run_bot(state, config, db)

        searcher2_instance.search.assert_not_called()

    @patch("bot.bot.time.sleep")
    @patch("bot.bot.score_job")
    @patch("bot.bot.BrowserManager")
    def test_stop_flag_mid_job_iteration(self, mock_bm, mock_score, mock_sleep):
        """Bot stops mid-job when stop_flag set during iteration."""
        from bot.bot import run_bot

        state = BotState()
        state.start()

        raw1 = FakeRawJob(external_id="job-1")
        raw2 = FakeRawJob(external_id="job-2")

        mock_searcher_cls = MagicMock()
        mock_searcher_instance = MagicMock()

        def _search(*a, **kw):
            yield raw1
            state.stop()
            yield raw2

        mock_searcher_instance.search.side_effect = _search
        mock_searcher_cls.return_value = mock_searcher_instance

        config = _make_config(enabled_platforms=["linkedin"])
        db = MagicMock()

        with patch("bot.bot.SEARCHERS", {"linkedin": mock_searcher_cls}):
            run_bot(state, config, db)

        assert mock_score.call_count <= 1

    @patch("bot.bot.time.sleep")
    @patch("bot.bot.BrowserManager")
    def test_emit_socketio_failure_during_found(self, mock_bm, mock_sleep):
        """SocketIO emit failure during FOUND event is handled gracefully."""
        from bot.bot import run_bot

        state = BotState()
        state.start()

        raw = FakeRawJob()
        mock_searcher_cls = MagicMock()
        mock_searcher_instance = MagicMock()

        def _search(*a, **kw):
            yield raw
            state.stop()

        mock_searcher_instance.search.side_effect = _search
        mock_searcher_cls.return_value = mock_searcher_instance

        config = _make_config(enabled_platforms=["linkedin"])
        db = MagicMock()
        emit_func = MagicMock(side_effect=Exception("SocketIO dead"))

        with patch("bot.bot.SEARCHERS", {"linkedin": mock_searcher_cls}):
            with patch("bot.bot.score_job") as mock_score:
                mock_score.return_value = _make_scored(raw=raw, pass_filter=False, skip_reason="low")
                run_bot(state, config, db, emit_func)

    @patch("bot.bot.time.sleep")
    @patch("bot.bot.BrowserManager")
    def test_emit_db_failure_during_found(self, mock_bm, mock_sleep):
        """DB save_feed_event failure during FOUND event is handled gracefully."""
        from bot.bot import run_bot

        state = BotState()
        state.start()

        raw = FakeRawJob()
        mock_searcher_cls = MagicMock()
        mock_searcher_instance = MagicMock()

        def _search(*a, **kw):
            yield raw
            state.stop()

        mock_searcher_instance.search.side_effect = _search
        mock_searcher_cls.return_value = mock_searcher_instance

        config = _make_config(enabled_platforms=["linkedin"])
        db = MagicMock()
        db.save_feed_event.side_effect = Exception("DB write fail")

        with patch("bot.bot.SEARCHERS", {"linkedin": mock_searcher_cls}):
            with patch("bot.bot.score_job") as mock_score:
                mock_score.return_value = _make_scored(raw=raw, pass_filter=False, skip_reason="low")
                run_bot(state, config, db)


# ===================================================================
# run_bot — login gate integration
# ===================================================================


class TestRunBotLoginGate:
    """FR-089: run_bot correctly wires login-required application flow."""

    @staticmethod
    def _make_login_setup(raw):
        state = BotState()
        state.start()

        searcher_cls = MagicMock()
        searcher = MagicMock()

        def _search(*args, **kwargs):
            yield raw
            state.stop()

        searcher.search.side_effect = _search
        searcher_cls.return_value = searcher

        return state, searcher_cls

    @patch("bot.bot.time.sleep")
    @patch("bot.bot._save_job_description", return_value=None)
    @patch("bot.bot._generate_docs")
    @patch("bot.bot._apply_to_job")
    @patch("bot.bot._save_application")
    @patch("bot.bot.score_job")
    @patch("bot.bot.BrowserManager")
    def test_login_done_retries_application(
        self,
        mock_bm,
        mock_score,
        mock_save,
        mock_apply,
        mock_gen,
        mock_save_jd,
        mock_sleep,
    ):
        """Completing login retries the same application once."""

        from bot.bot import run_bot

        raw = FakeRawJob(
            apply_url="https://company.wd5.myworkdayjobs.com/job/123",
        )
        scored = _make_scored(raw=raw, pass_filter=True)

        mock_score.return_value = scored
        mock_gen.return_value = (
            "/resume.pdf",
            "/cover.txt",
            "cover text",
            None,
        )

        login_required = ApplyResult(
            success=False,
            login_required=True,
            login_domain="company.wd5.myworkdayjobs.com",
            login_portal_type="workday",
            error_message="Login required",
        )
        applied = ApplyResult(success=True)

        mock_apply.side_effect = [login_required, applied]

        state, searcher_cls = self._make_login_setup(raw)

        original_wait = state.wait_for_login

        def _complete_login():
            assert state.awaiting_login is True
            assert state.login_context["domain"] == (
                "company.wd5.myworkdayjobs.com"
            )
            state.set_login_decision("done")
            return original_wait()

        state.wait_for_login = MagicMock(side_effect=_complete_login)

        config = _make_config(
            enabled_platforms=["linkedin"],
            apply_mode="full_auto",
        )
        db = MagicMock()

        with patch("bot.bot.SEARCHERS", {"linkedin": searcher_cls}):
            run_bot(state, config, db)

        assert mock_apply.call_count == 2
        state.wait_for_login.assert_called_once()

        mock_save.assert_called_once()
        saved_result = mock_save.call_args.args[5]
        assert saved_result.success is True

    @patch("bot.bot.time.sleep")
    @patch("bot.bot._save_job_description", return_value=None)
    @patch("bot.bot._generate_docs")
    @patch("bot.bot._apply_to_job")
    @patch("bot.bot._save_application")
    @patch("bot.bot.score_job")
    @patch("bot.bot.BrowserManager")
    def test_login_skip_saves_for_later_without_retry(
        self,
        mock_bm,
        mock_score,
        mock_save,
        mock_apply,
        mock_gen,
        mock_save_jd,
        mock_sleep,
    ):
        """Skipping login stores the login-required result without retry."""

        from bot.bot import run_bot

        raw = FakeRawJob(
            apply_url="https://company.wd5.myworkdayjobs.com/job/456",
        )
        scored = _make_scored(raw=raw, pass_filter=True)

        mock_score.return_value = scored
        mock_gen.return_value = (
            "/resume.pdf",
            "/cover.txt",
            "cover text",
            None,
        )

        login_required = ApplyResult(
            success=False,
            login_required=True,
            login_domain="company.wd5.myworkdayjobs.com",
            login_portal_type="workday",
            error_message="Login required",
        )

        mock_apply.return_value = login_required

        state, searcher_cls = self._make_login_setup(raw)

        original_wait = state.wait_for_login

        def _skip_login():
            assert state.awaiting_login is True
            state.set_login_decision("skip")
            return original_wait()

        state.wait_for_login = MagicMock(side_effect=_skip_login)

        config = _make_config(
            enabled_platforms=["linkedin"],
            apply_mode="full_auto",
        )
        db = MagicMock()

        with patch("bot.bot.SEARCHERS", {"linkedin": searcher_cls}):
            run_bot(state, config, db)

        mock_apply.assert_called_once()
        state.wait_for_login.assert_called_once()

        mock_save.assert_called_once()
        saved_result = mock_save.call_args.args[5]

        assert saved_result is login_required
        assert saved_result.login_required is True

    @patch("bot.bot.time.sleep")
    @patch("bot.bot._save_job_description", return_value=None)
    @patch("bot.bot._generate_docs")
    @patch("bot.bot._apply_to_job")
    @patch("bot.bot._save_application")
    @patch("bot.bot.score_job")
    @patch("bot.bot.BrowserManager")
    def test_login_stop_exits_without_retry_or_save(
        self,
        mock_bm,
        mock_score,
        mock_save,
        mock_apply,
        mock_gen,
        mock_save_jd,
        mock_sleep,
    ):
        """Stopping at the login gate exits without retrying."""

        from bot.bot import run_bot

        raw = FakeRawJob(
            apply_url="https://company.wd5.myworkdayjobs.com/job/789",
        )
        scored = _make_scored(raw=raw, pass_filter=True)

        mock_score.return_value = scored
        mock_gen.return_value = (
            "/resume.pdf",
            "/cover.txt",
            "cover text",
            None,
        )

        mock_apply.return_value = ApplyResult(
            success=False,
            login_required=True,
            login_domain="company.wd5.myworkdayjobs.com",
            login_portal_type="workday",
            error_message="Login required",
        )

        state, searcher_cls = self._make_login_setup(raw)

        original_wait = state.wait_for_login

        def _stop_at_login():
            assert state.awaiting_login is True
            state.stop()
            return original_wait()

        state.wait_for_login = MagicMock(side_effect=_stop_at_login)

        config = _make_config(
            enabled_platforms=["linkedin"],
            apply_mode="full_auto",
        )
        db = MagicMock()

        with patch("bot.bot.SEARCHERS", {"linkedin": searcher_cls}):
            run_bot(state, config, db)

        mock_apply.assert_called_once()
        state.wait_for_login.assert_called_once()
        mock_save.assert_not_called()

        assert state.stop_flag is True
