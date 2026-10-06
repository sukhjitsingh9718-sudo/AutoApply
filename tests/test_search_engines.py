"""Unit tests for LinkedIn and Indeed search engines.

Requirement traceability:
    FR-044 — LinkedIn/Indeed job search
    FR-047 — LinkedIn Easy Apply automation
    FR-048 — Indeed Quick Apply automation
    ME-5   — Test coverage increase to 70%
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, PropertyMock, patch

from bot.apply.indeed import IndeedApplier
from bot.apply.linkedin import LinkedInApplier
from bot.search.base import RawJob
from bot.search.indeed import IndeedSearcher
from bot.search.linkedin import LinkedInSearcher
from core.filter import ScoredJob

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_criteria(job_titles=None, locations=None, remote_only=False, max_results=100):
    criteria = MagicMock()
    criteria.job_titles = job_titles or ["Software Engineer"]
    criteria.locations = locations or ["Remote"]
    criteria.remote_only = remote_only
    criteria.max_results_per_search = max_results
    return criteria


def _make_page(url="https://www.linkedin.com/jobs/search/"):
    page = MagicMock()
    page.url = url
    page.query_selector.return_value = None
    page.query_selector_all.return_value = []
    return page


def _make_profile(**overrides):
    profile = MagicMock()
    profile.full_name = overrides.get("full_name", "Jane Doe")
    profile.first_name = overrides.get("first_name", "Jane")
    profile.last_name = overrides.get("last_name", "Doe")
    profile.email = overrides.get("email", "jane@example.com")
    profile.phone_full = overrides.get("phone_full", "+1-555-0100")
    profile.linkedin_url = overrides.get("linkedin_url", None)
    profile.portfolio_url = overrides.get("portfolio_url", None)
    profile.location = overrides.get("location", "Remote")
    profile.screening_answers = overrides.get("screening_answers", {})
    return profile


def _make_scored_job(apply_url, platform):
    raw = RawJob(
        title="Software Engineer", company="Acme", location="Remote",
        salary="$130K", description="Build things.",
        apply_url=apply_url, platform=platform,
        external_id=f"{platform}-123", posted_at=None,
    )
    return ScoredJob(id="test-uuid", raw=raw, score=85,
                     pass_filter=True, skip_reason=None)


# ===================================================================
# LinkedInSearcher
# ===================================================================


class TestLinkedInSearcher:
    """FR-044: LinkedIn job search engine."""

    def test_no_page_yields_nothing(self):
        searcher = LinkedInSearcher()
        results = list(searcher.search(_make_criteria(), page=None))
        assert results == []

    @patch("bot.search.linkedin.time.sleep")
    def test_login_wall_yields_nothing(self, _sleep):
        page = _make_page("https://www.linkedin.com/login")
        searcher = LinkedInSearcher()
        results = list(searcher.search(_make_criteria(), page=page))
        assert results == []

    @patch("bot.search.linkedin.time.sleep")
    def test_authwall_yields_nothing(self, _sleep):
        page = _make_page("https://www.linkedin.com/authwall")
        searcher = LinkedInSearcher()
        results = list(searcher.search(_make_criteria(), page=page))
        assert results == []

    def test_delayed_authwall_redirect_yields_nothing(self):
        page = _make_page()

        # Simulate LinkedIn landing on authwall after initial navigation.
        type(page).url = PropertyMock(
            return_value=(
                "https://www.linkedin.com/authwall"
                "?sessionRedirect=https%3A%2F%2Fwww.linkedin.com%2Fjobs"
            )
        )

        searcher = LinkedInSearcher()
        results = list(
            searcher.search(
                _make_criteria(),
                page=page,
            )
        )

        assert results == []
        page.query_selector_all.assert_not_called()

    @patch("bot.search.linkedin.time.sleep")
    def test_no_job_cards_yields_nothing(self, _sleep):
        page = _make_page()
        page.query_selector_all.return_value = []

        searcher = LinkedInSearcher()
        results = list(searcher.search(_make_criteria(), page=page))

        assert results == []

    @patch("bot.search.linkedin.time.sleep")
    def test_extracts_job_from_card(self, _sleep):
        page = _make_page()
        card = MagicMock()
        card.get_attribute.return_value = "12345"

        title_el = MagicMock()
        title_el.inner_text.return_value = "Software Engineer"
        company_el = MagicMock()
        company_el.inner_text.return_value = "Acme Corp"
        location_el = MagicMock()
        location_el.inner_text.return_value = "San Francisco, CA"
        desc_el = MagicMock()
        desc_el.inner_text.return_value = "Build amazing things."
        salary_el = MagicMock()
        salary_el.inner_text.return_value = "$150K"

        # First call returns cards, second returns empty (stop pagination)
        page.query_selector_all.side_effect = [[card], []]

        def qs(selector):
            if "job-title" in selector or "t-24" in selector:
                return title_el
            if "company-name" in selector or "t-black" in selector:
                return company_el
            if "tvm__text" in selector or "bullet" in selector:
                return location_el
            if "description" in selector or "job-details" in selector:
                return desc_el
            if "highlight" in selector or "salary" in selector:
                return salary_el
            if "Next" in selector or "pagination" in selector:
                return None
            return None

        page.query_selector.side_effect = qs

        searcher = LinkedInSearcher()
        results = list(searcher.search(_make_criteria(), page=page))

        assert len(results) == 1
        assert results[0].title == "Software Engineer"
        assert results[0].company == "Acme Corp"
        assert results[0].platform == "linkedin"
        assert results[0].external_id == "linkedin-12345"

    @patch("bot.search.linkedin.time.sleep")
    def test_remote_filter_appended_to_url(self, _sleep):
        page = _make_page()
        page.query_selector_all.return_value = []
        searcher = LinkedInSearcher()
        list(searcher.search(_make_criteria(remote_only=True), page=page))
        call_url = page.goto.call_args[0][0]
        assert "f_WT=2" in call_url

    @patch("bot.search.linkedin.time.sleep")
    def test_max_results_respected(self, _sleep):
        page = _make_page()
        cards = []
        for i in range(5):
            card = MagicMock()
            card.get_attribute.return_value = str(i)
            cards.append(card)

        page.query_selector_all.side_effect = [cards, []]

        title_el = MagicMock()
        title_el.inner_text.return_value = "Engineer"
        company_el = MagicMock()
        company_el.inner_text.return_value = "Corp"

        def qs(selector):
            if "job-title" in selector or "t-24" in selector:
                return title_el
            if "company-name" in selector or "t-black" in selector:
                return company_el
            return None

        page.query_selector.side_effect = qs

        searcher = LinkedInSearcher()
        results = list(searcher.search(_make_criteria(max_results=2), page=page))
        assert len(results) <= 2

    @patch("bot.search.linkedin.time.sleep")
    def test_search_exception_handled(self, _sleep):
        page = _make_page()
        page.goto.side_effect = Exception("Network error")
        searcher = LinkedInSearcher()
        results = list(searcher.search(_make_criteria(), page=page))
        assert results == []

    @patch("bot.search.linkedin.time.sleep")
    def test_extract_job_no_title_returns_none(self, _sleep):
        page = _make_page()
        card = MagicMock()
        card.get_attribute.return_value = "123"
        page.query_selector.return_value = None

        searcher = LinkedInSearcher()
        result = searcher._extract_job(page, card)
        assert result is None

    @patch("bot.search.linkedin.time.sleep")
    def test_extract_job_no_id_returns_none(self, _sleep):
        page = _make_page()
        card = MagicMock()
        card.get_attribute.return_value = ""  # no job ID
        title_el = MagicMock()
        title_el.inner_text.return_value = "Dev"
        company_el = MagicMock()
        company_el.inner_text.return_value = "Corp"

        def qs(selector):
            if "job-title" in selector or "t-24" in selector:
                return title_el
            if "company-name" in selector or "t-black" in selector:
                return company_el
            return None

        page.query_selector.side_effect = qs
        page.url = "https://www.linkedin.com/jobs/search/"

        searcher = LinkedInSearcher()
        result = searcher._extract_job(page, card)
        assert result is None

    @patch("bot.search.linkedin.time.sleep")
    def test_extract_job_id_from_url(self, _sleep):
        """Falls back to extracting job ID from currentJobId= in URL."""
        page = _make_page()
        card = MagicMock()
        card.get_attribute.return_value = ""  # no data-occludable-job-id
        page.url = "https://www.linkedin.com/jobs/search/?currentJobId=99999&other=1"

        title_el = MagicMock()
        title_el.inner_text.return_value = "Dev"
        company_el = MagicMock()
        company_el.inner_text.return_value = "Corp"

        def qs(selector):
            if "job-title" in selector or "t-24" in selector:
                return title_el
            if "company-name" in selector or "t-black" in selector:
                return company_el
            return None

        page.query_selector.side_effect = qs

        searcher = LinkedInSearcher()
        result = searcher._extract_job(page, card)
        assert result is not None
        assert result.external_id == "linkedin-99999"

    @patch("bot.search.linkedin.time.sleep")
    def test_extract_job_click_failure_returns_none(self, _sleep):
        """Card click exception returns None."""
        page = _make_page()
        card = MagicMock()
        card.click.side_effect = Exception("Element detached")

        searcher = LinkedInSearcher()
        result = searcher._extract_job(page, card)
        assert result is None

    @patch("bot.search.linkedin.time.sleep")
    def test_card_extraction_exception_skipped(self, _sleep):
        """Exception propagating from _extract_job is caught at loop level."""
        page = _make_page()
        good_card = MagicMock()
        good_card.get_attribute.return_value = "777"

        title_el = MagicMock()
        title_el.inner_text.return_value = "Good Job"
        company_el = MagicMock()
        company_el.inner_text.return_value = "Good Corp"

        def qs(selector):
            if "job-title" in selector or "t-24" in selector:
                return title_el
            if "company-name" in selector or "t-black" in selector:
                return company_el
            if "Next" in selector or "pagination" in selector:
                return None
            return None

        page.query_selector.side_effect = qs
        page.query_selector_all.side_effect = [[good_card], []]

        searcher = LinkedInSearcher()

        # Patch _extract_job to raise on first call, succeed on retry
        call_count = {"n": 0}
        original_extract = searcher._extract_job

        def patched_extract(p, c):
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise RuntimeError("Unexpected DOM error")
            return original_extract(p, c)

        with patch.object(searcher, "_extract_job", side_effect=patched_extract):
            # First card raises -> caught at line 105-106, loop continues
            # No more cards -> pagination empty -> done
            results = list(searcher.search(_make_criteria(), page=page))

        assert results == []  # The only card raised an exception

    @patch("bot.search.linkedin.time.sleep")
    def test_pagination_clicks_next(self, _sleep):
        """When next button exists, it clicks and continues."""
        page = _make_page()
        card1 = MagicMock()
        card1.get_attribute.return_value = "111"
        card2 = MagicMock()
        card2.get_attribute.return_value = "222"

        title_el = MagicMock()
        title_el.inner_text.return_value = "Engineer"
        company_el = MagicMock()
        company_el.inner_text.return_value = "Corp"

        next_btn = MagicMock()
        next_btn.is_enabled.return_value = True

        # Page 1: card1, Page 2: card2, Page 3: empty
        page.query_selector_all.side_effect = [[card1], [card2], []]

        def qs(selector):
            if "job-title" in selector or "t-24" in selector:
                return title_el
            if "company-name" in selector or "t-black" in selector:
                return company_el
            if "Next" in selector or "pagination" in selector:
                return next_btn
            return None

        page.query_selector.side_effect = qs

        searcher = LinkedInSearcher()
        results = list(searcher.search(_make_criteria(), page=page))
        assert len(results) == 2
        assert next_btn.click.call_count == 2

# ===================================================================
# IndeedSearcher
# ===================================================================


class TestIndeedSearcher:
    """FR-044: Indeed job search engine."""

    def test_no_page_yields_nothing(self):
        searcher = IndeedSearcher()
        results = list(searcher.search(_make_criteria(), page=None))
        assert results == []

    @patch("bot.search.indeed.time.sleep")
    def test_no_job_cards_yields_nothing(self, _sleep):
        page = _make_page("https://www.indeed.com/jobs")
        page.query_selector_all.return_value = []
        searcher = IndeedSearcher()
        results = list(searcher.search(_make_criteria(), page=page))
        assert results == []

    @patch("bot.search.indeed.time.sleep")
    def test_extracts_job_from_card(self, _sleep):
        page = _make_page("https://www.indeed.com/jobs")
        card = MagicMock()
        card.get_attribute.return_value = "abc123"

        title_link = MagicMock()
        title_link.inner_text.return_value = "Backend Developer"
        card.query_selector.side_effect = lambda sel: (
            title_link if "jobTitle" in sel or "data-jk" in sel
            else MagicMock(inner_text=MagicMock(return_value="Acme"))
            if "companyName" in sel
            else MagicMock(inner_text=MagicMock(return_value="NYC"))
            if "location" in sel or "companyLocation" in sel
            else None
        )

        page.query_selector_all.side_effect = [[card], []]
        page.query_selector.return_value = None  # no description, no next

        searcher = IndeedSearcher()
        results = list(searcher.search(_make_criteria(), page=page))
        assert len(results) == 1
        assert results[0].title == "Backend Developer"
        assert results[0].platform == "indeed"

    @patch("bot.search.indeed.time.sleep")
    def test_search_exception_handled(self, _sleep):
        page = _make_page("https://www.indeed.com/jobs")
        page.goto.side_effect = Exception("Network error")
        searcher = IndeedSearcher()
        results = list(searcher.search(_make_criteria(), page=page))
        assert results == []

    @patch("bot.search.indeed.time.sleep")
    def test_extract_job_no_title_link_returns_none(self, _sleep):
        page = _make_page("https://www.indeed.com/jobs")
        card = MagicMock()
        card.query_selector.return_value = None
        searcher = IndeedSearcher()
        result = searcher._extract_job(page, card)
        assert result is None

    @patch("bot.search.indeed.time.sleep")
    def test_extract_job_no_jk_returns_none(self, _sleep):
        page = _make_page("https://www.indeed.com/jobs")
        card = MagicMock()
        card.get_attribute.return_value = ""  # no data-jk
        title_link = MagicMock()
        title_link.inner_text.return_value = "Dev"
        title_link.get_attribute.return_value = ""  # no href with jk=

        card.query_selector.side_effect = lambda sel: (
            title_link if "jobTitle" in sel or "data-jk" in sel else None
        )

        searcher = IndeedSearcher()
        result = searcher._extract_job(page, card)
        assert result is None

    @patch("bot.search.indeed.time.sleep")
    def test_extract_job_id_from_href(self, _sleep):
        """Falls back to extracting job ID from href jk= parameter."""
        page = _make_page("https://www.indeed.com/jobs")
        card = MagicMock()
        card.get_attribute.return_value = ""  # no data-jk

        title_link = MagicMock()
        title_link.inner_text.return_value = "Dev"
        title_link.get_attribute.return_value = "/viewjob?jk=xyz789&from=search"

        company_el = MagicMock()
        company_el.inner_text.return_value = "Acme"

        def card_qs(selector):
            if "jobTitle" in selector or "data-jk" in selector:
                return title_link
            if "companyName" in selector:
                return company_el
            return None

        card.query_selector.side_effect = card_qs
        page.query_selector.return_value = None

        searcher = IndeedSearcher()
        result = searcher._extract_job(page, card)
        assert result is not None
        assert result.external_id == "indeed-xyz789"

    @patch("bot.search.indeed.time.sleep")
    def test_extract_job_empty_title_returns_none(self, _sleep):
        """Title link exists but inner_text is empty."""
        page = _make_page("https://www.indeed.com/jobs")
        card = MagicMock()
        title_link = MagicMock()
        title_link.inner_text.return_value = "   "  # whitespace only

        card.query_selector.side_effect = lambda sel: (
            title_link if "jobTitle" in sel or "data-jk" in sel else None
        )

        searcher = IndeedSearcher()
        result = searcher._extract_job(page, card)
        assert result is None

    @patch("bot.search.indeed.time.sleep")
    def test_extract_job_click_exception_handled(self, _sleep):
        """title_link.click() exception is caught gracefully."""
        page = _make_page("https://www.indeed.com/jobs")
        card = MagicMock()
        card.get_attribute.return_value = "abc"

        title_link = MagicMock()
        title_link.inner_text.return_value = "Developer"
        title_link.click.side_effect = Exception("Element detached")

        company_el = MagicMock()
        company_el.inner_text.return_value = "Corp"

        def card_qs(selector):
            if "jobTitle" in selector or "data-jk" in selector:
                return title_link
            if "companyName" in selector:
                return company_el
            return None

        card.query_selector.side_effect = card_qs
        page.query_selector.return_value = None  # no desc

        searcher = IndeedSearcher()
        result = searcher._extract_job(page, card)
        assert result is not None
        assert result.title == "Developer"

    @patch("bot.search.indeed.time.sleep")
    def test_card_extraction_exception_skipped(self, _sleep):
        """Exception during _extract_job is caught, other cards processed."""
        page = _make_page("https://www.indeed.com/jobs")

        bad_card = MagicMock()
        bad_card.query_selector.side_effect = Exception("DOM error")

        good_card = MagicMock()
        good_card.get_attribute.return_value = "good1"
        title_link = MagicMock()
        title_link.inner_text.return_value = "Good Job"
        company_el = MagicMock()
        company_el.inner_text.return_value = "Good Corp"
        good_card.query_selector.side_effect = lambda sel: (
            title_link if "jobTitle" in sel or "data-jk" in sel
            else company_el if "companyName" in sel
            else None
        )

        page.query_selector_all.side_effect = [[bad_card, good_card], []]
        page.query_selector.return_value = None  # no next, no desc

        searcher = IndeedSearcher()
        results = list(searcher.search(_make_criteria(), page=page))
        assert len(results) == 1

    @patch("bot.search.indeed.time.sleep")
    def test_pagination_clicks_next(self, _sleep):
        """When next link exists, clicks and continues to next page."""
        page = _make_page("https://www.indeed.com/jobs")

        card1 = MagicMock()
        card1.get_attribute.return_value = "j1"
        card2 = MagicMock()
        card2.get_attribute.return_value = "j2"

        title_link = MagicMock()
        title_link.inner_text.return_value = "Dev"
        company_el = MagicMock()
        company_el.inner_text.return_value = "Co"

        for card in [card1, card2]:
            card.query_selector.side_effect = lambda sel: (
                title_link if "jobTitle" in sel or "data-jk" in sel
                else company_el if "companyName" in sel
                else None
            )

        next_link = MagicMock()
        # Page 1: card1, Page 2: card2, Page 3: empty
        page.query_selector_all.side_effect = [[card1], [card2], []]

        def qs(selector):
            if "pagination" in selector or "Next Page" in selector:
                return next_link
            return None

        page.query_selector.side_effect = qs

        searcher = IndeedSearcher()
        results = list(searcher.search(_make_criteria(), page=page))
        assert len(results) == 2
        assert next_link.click.call_count == 2

    @patch("bot.search.indeed.time.sleep")
    def test_remaining_limit_stops_mid_page(self, _sleep):
        """found >= remaining inside card loop exits early."""
        page = _make_page("https://www.indeed.com/jobs")

        cards = []
        for i in range(5):
            card = MagicMock()
            card.get_attribute.return_value = f"j{i}"
            title_link = MagicMock()
            title_link.inner_text.return_value = f"Job {i}"
            company_el = MagicMock()
            company_el.inner_text.return_value = "Co"
            card.query_selector.side_effect = lambda sel, tl=title_link, ce=company_el: (
                tl if "jobTitle" in sel or "data-jk" in sel
                else ce if "companyName" in sel
                else None
            )
            cards.append(card)

        page.query_selector_all.side_effect = [cards]
        page.query_selector.return_value = None

        searcher = IndeedSearcher()
        results = list(searcher.search(_make_criteria(max_results=2), page=page))
        assert len(results) == 2


# ===================================================================
# LinkedInApplier
# ===================================================================


class TestLinkedInApplier:
    """FR-047: LinkedIn Easy Apply automation."""

    @patch("bot.apply.base.time.sleep")
    def test_successful_easy_apply(self, _sleep):
        page = _make_page("https://www.linkedin.com/jobs/view/123/")
        easy_btn = MagicMock()
        submit_btn = MagicMock()
        submit_btn.is_visible.return_value = True
        confirm = MagicMock()

        call_n = {"n": 0}

        def qs(selector):
            call_n["n"] += 1
            if "Easy Apply" in selector or "jobs-apply-button" in selector:
                return easy_btn
            if "Submit" in selector:
                return submit_btn
            if "artdeco-modal" in selector or "modal-close" in selector:
                return confirm
            return None

        page.query_selector.side_effect = qs
        applier = LinkedInApplier(page)
        job = _make_scored_job("https://www.linkedin.com/jobs/view/123/", "linkedin")
        result = applier.apply(job, Path("/tmp/r.pdf"), "cover", _make_profile())
        assert result.success is True

    @patch("bot.apply.base.time.sleep")
    def test_captcha_detected(self, _sleep):
        page = _make_page("https://www.linkedin.com/jobs/view/123/")
        page.query_selector.return_value = MagicMock()
        applier = LinkedInApplier(page)
        job = _make_scored_job("https://www.linkedin.com/jobs/view/123/", "linkedin")
        result = applier.apply(job, None, "", _make_profile())
        assert result.success is False
        assert result.captcha_detected is True

    @patch("bot.apply.base.time.sleep")
    def test_no_easy_apply_button(self, _sleep):
        page = _make_page("https://www.linkedin.com/jobs/view/123/")
        page.query_selector.return_value = None
        applier = LinkedInApplier(page)
        job = _make_scored_job("https://www.linkedin.com/jobs/view/123/", "linkedin")
        result = applier.apply(job, None, "", _make_profile())
        assert result.success is False
        assert result.manual_required is True
        assert "Easy Apply" in result.error_message

    @patch("bot.apply.base.time.sleep")
    def test_exception_returns_failure(self, _sleep):
        page = _make_page()
        page.goto.side_effect = Exception("Connection refused")
        applier = LinkedInApplier(page)
        job = _make_scored_job("https://www.linkedin.com/jobs/view/123/", "linkedin")
        result = applier.apply(job, None, "", _make_profile())
        assert result.success is False
        assert "Connection refused" in result.error_message

    @patch("bot.apply.base.time.sleep")
    def test_ran_out_of_steps(self, _sleep):
        page = _make_page("https://www.linkedin.com/jobs/view/123/")
        easy_btn = MagicMock()

        def qs(selector):
            if "Easy Apply" in selector or "jobs-apply-button" in selector:
                return easy_btn
            return None

        page.query_selector.side_effect = qs
        applier = LinkedInApplier(page)
        job = _make_scored_job("https://www.linkedin.com/jobs/view/123/", "linkedin")
        result = applier.apply(job, None, "", _make_profile())
        assert result.success is False
        assert "ran out of steps" in result.error_message

    @patch("bot.apply.base.time.sleep")
    def test_fill_phone_field(self, _sleep):
        page = _make_page()
        phone_input = MagicMock()
        phone_input.input_value.return_value = ""

        def qs(selector):
            if "phone" in selector:
                return phone_input
            return None

        page.query_selector.side_effect = qs
        applier = LinkedInApplier(page)
        applier._fill_form_fields(_make_profile(phone_full="+1-555-1234"))
        assert phone_input.type.call_count == len("+1-555-1234")

    @patch("bot.apply.base.time.sleep")
    def test_upload_resume(self, _sleep):
        page = _make_page()
        fi = MagicMock()
        page.query_selector.return_value = fi
        applier = LinkedInApplier(page)
        applier._upload_resume(Path("/tmp/resume.pdf"))
        fi.set_input_files.assert_called_once()

    @patch("bot.apply.base.time.sleep")
    def test_fill_cover_letter(self, _sleep):
        page = _make_page()
        ta = MagicMock()
        ta.input_value.return_value = ""

        def qs(selector):
            if "cover" in selector:
                return ta
            return None

        page.query_selector.side_effect = qs
        applier = LinkedInApplier(page)
        applier._fill_cover_letter("My cover letter")
        ta.fill.assert_called_once_with("My cover letter")

    @patch("bot.apply.base.time.sleep")
    def test_fill_cover_letter_empty_noop(self, _sleep):
        page = _make_page()
        applier = LinkedInApplier(page)
        applier._fill_cover_letter("")
        page.query_selector.assert_not_called()


class TestLinkedInApplierRegistration:
    """FR-047: LinkedIn applier registered."""

    def test_linkedin_in_appliers(self):
        from bot.bot import APPLIERS
        assert "linkedin" in APPLIERS
        assert APPLIERS["linkedin"] is LinkedInApplier


# ===================================================================
# IndeedApplier
# ===================================================================


class TestIndeedApplier:
    """FR-048: Indeed Quick Apply automation."""

    @patch("bot.apply.base.time.sleep")
    def test_successful_quick_apply(self, _sleep):
        page = _make_page("https://www.indeed.com/viewjob?jk=abc")
        apply_btn = MagicMock()
        submit_btn = MagicMock()
        submit_btn.is_visible.return_value = True

        def qs(selector):
            if "indeedApply" in selector or "Apply now" in selector:
                return apply_btn
            if "Submit" in selector or "submit" in selector:
                return submit_btn
            return None

        page.query_selector.side_effect = qs
        applier = IndeedApplier(page)
        job = _make_scored_job("https://www.indeed.com/viewjob?jk=abc", "indeed")
        result = applier.apply(job, Path("/tmp/r.pdf"), "cover", _make_profile())
        assert result.success is True

    @patch("bot.apply.base.time.sleep")
    def test_captcha_detected(self, _sleep):
        page = _make_page("https://www.indeed.com/viewjob?jk=abc")
        page.query_selector.return_value = MagicMock()
        applier = IndeedApplier(page)
        job = _make_scored_job("https://www.indeed.com/viewjob?jk=abc", "indeed")
        result = applier.apply(job, None, "", _make_profile())
        assert result.success is False
        assert result.captcha_detected is True

    @patch("bot.apply.base.time.sleep")
    def test_no_apply_button(self, _sleep):
        page = _make_page("https://www.indeed.com/viewjob?jk=abc")
        page.query_selector.return_value = None
        applier = IndeedApplier(page)
        job = _make_scored_job("https://www.indeed.com/viewjob?jk=abc", "indeed")
        result = applier.apply(job, None, "", _make_profile())
        assert result.success is False
        assert result.manual_required is True

    @patch("bot.apply.base.time.sleep")
    def test_redirect_to_external_ats(self, _sleep):
        page = _make_page("https://www.indeed.com/viewjob?jk=abc")
        apply_btn = MagicMock()

        def qs(selector):
            if "indeedApply" in selector or "Apply now" in selector:
                return apply_btn
            return None

        page.query_selector.side_effect = qs
        # After clicking apply, URL changes to external site
        page.url = "https://external-ats.com/apply"

        applier = IndeedApplier(page)
        job = _make_scored_job("https://www.indeed.com/viewjob?jk=abc", "indeed")
        result = applier.apply(job, None, "", _make_profile())
        assert result.success is False
        assert result.manual_required is True
        assert "external ATS" in result.error_message

    @patch("bot.apply.base.time.sleep")
    def test_exception_returns_failure(self, _sleep):
        page = _make_page()
        page.goto.side_effect = Exception("Network error")
        applier = IndeedApplier(page)
        job = _make_scored_job("https://www.indeed.com/viewjob?jk=abc", "indeed")
        result = applier.apply(job, None, "", _make_profile())
        assert result.success is False
        assert "Network error" in result.error_message

    @patch("bot.apply.base.time.sleep")
    def test_fill_form_fields(self, _sleep):
        page = _make_page()
        name_input = MagicMock()
        name_input.input_value.return_value = ""
        email_input = MagicMock()
        email_input.input_value.return_value = ""

        def qs(selector):
            if "name" in selector and "email" not in selector and "phone" not in selector:
                return name_input
            if "email" in selector:
                return email_input
            return None

        page.query_selector.side_effect = qs
        applier = IndeedApplier(page)
        applier._fill_form_fields(_make_profile(full_name="John", email="j@test.com"))
        assert name_input.type.call_count == len("John")
        assert email_input.type.call_count == len("j@test.com")


class TestIndeedApplierRegistration:
    """FR-048: Indeed applier registered."""

    def test_indeed_in_appliers(self):
        from bot.bot import APPLIERS
        assert "indeed" in APPLIERS
        assert APPLIERS["indeed"] is IndeedApplier
