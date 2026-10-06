"""Integration coverage for the real AutoApply job pipeline."""

from unittest.mock import MagicMock, patch

from bot.apply.base import ApplyResult
from bot.search.base import RawJob
from bot.state import BotState
from config.settings import (
    AppConfig,
    BotConfig,
    SearchCriteria,
    UserProfile,
)
from db.database import Database


def _config() -> AppConfig:
    return AppConfig(
        profile=UserProfile(
            first_name="Pipeline",
            last_name="Test",
            email="pipeline@example.com",
            phone="5551234567",
            city="Test City",
            state="TS",
            bio="Integration test profile",
        ),
        search_criteria=SearchCriteria(
            job_titles=["Software Engineer"],
            locations=["Remote"],
            remote_only=False,
            salary_min=None,
            keywords_include=[],
            keywords_exclude=[],
        ),
        bot=BotConfig(
            enabled_platforms=["linkedin"],
            min_match_score=75,
            max_applications_per_day=50,
            apply_mode="full_auto",
        ),
    )


def _job() -> RawJob:
    return RawJob(
        title="Software Engineer",
        company="Pipeline Test Corp",
        location="Remote",
        salary="$120K",
        description="Build and maintain software systems.",
        # Discovered via LinkedIn but application is hosted by Greenhouse.
        apply_url="https://boards.greenhouse.io/pipeline-test/jobs/12345",
        platform="linkedin",
        external_id="linkedin-pipeline-integration-12345",
        posted_at=None,
    )


def _searcher_for(state: BotState, job: RawJob):
    searcher_cls = MagicMock()
    searcher = MagicMock()

    def search(*args, **kwargs):
        yield job
        state.stop()

    searcher.search.side_effect = search
    searcher_cls.return_value = searcher
    return searcher_cls


@patch("bot.bot.time.sleep")
@patch("bot.bot._save_job_description", return_value=None)
@patch("bot.bot._generate_docs")
@patch("bot.bot.BrowserManager")
def test_real_score_route_save_and_dedup(
    mock_browser_manager,
    mock_generate_docs,
    _mock_save_description,
    _mock_sleep,
    tmp_path,
):
    """A qualifying job is scored, routed, saved, then deduplicated."""

    from bot.bot import run_bot

    db = Database(tmp_path / "pipeline.db")
    config = _config()
    job = _job()

    mock_generate_docs.return_value = (
        tmp_path / "resume.pdf",
        tmp_path / "cover-letter.txt",
        "Pipeline integration cover letter",
        None,
    )

    page = MagicMock()
    mock_browser_manager.return_value.get_page.return_value = page

    greenhouse_cls = MagicMock()
    greenhouse = MagicMock()
    greenhouse.apply.return_value = ApplyResult(success=True)
    greenhouse_cls.return_value = greenhouse

    # ------------------------------------------------------------------
    # First run: real score_job() should pass, Greenhouse should be chosen,
    # and real _save_application() should persist the application.
    # ------------------------------------------------------------------
    first_state = BotState()
    first_state.start()
    first_searcher = _searcher_for(first_state, job)

    with (
        patch("bot.bot.SEARCHERS", {"linkedin": first_searcher}),
        patch("bot.bot.APPLIERS", {"greenhouse": greenhouse_cls}),
        patch(
            "core.portal_auth.PortalAuthManager.detect_login_wall",
            return_value=False,
        ),
    ):
        run_bot(first_state, config, db)

    assert db.exists(job.external_id, job.platform) is True

    with db._connect() as conn:
        matching = conn.execute(
            """
            SELECT *
            FROM applications
            WHERE external_id = ? AND platform = ?
            """,
            (job.external_id, job.platform),
        ).fetchall()

    assert len(matching) == 1

    application = matching[0]

    assert application["job_title"] == "Software Engineer"
    assert application["company"] == "Pipeline Test Corp"
    assert application["match_score"] >= config.bot.min_match_score
    assert application["status"] == "applied"

    # URL fingerprint must override the discovery platform.
    greenhouse_cls.assert_called_once()
    greenhouse.apply.assert_called_once()

    # ------------------------------------------------------------------
    # Second run: real score_job() should detect the existing DB record
    # and stop before docs/application routing.
    # ------------------------------------------------------------------
    mock_generate_docs.reset_mock()
    greenhouse_cls.reset_mock()
    greenhouse.apply.reset_mock()

    second_state = BotState()
    second_state.start()
    second_searcher = _searcher_for(second_state, job)

    with (
        patch("bot.bot.SEARCHERS", {"linkedin": second_searcher}),
        patch("bot.bot.APPLIERS", {"greenhouse": greenhouse_cls}),
        patch(
            "core.portal_auth.PortalAuthManager.detect_login_wall",
            return_value=False,
        ),
    ):
        run_bot(second_state, config, db)

    # Duplicate must never reach generation/application.
    mock_generate_docs.assert_not_called()
    greenhouse_cls.assert_not_called()
    greenhouse.apply.assert_not_called()

    with db._connect() as conn:
        matching_after = conn.execute(
            """
            SELECT *
            FROM applications
            WHERE external_id = ? AND platform = ?
            """,
            (job.external_id, job.platform),
        ).fetchall()

    # Still exactly one application.
    assert len(matching_after) == 1
