"""Unit tests for Workday and Ashby ATS appliers.

Requirement traceability:
    FR-070 — Workday ATS form-filling applier
    FR-071 — Ashby ATS form-filling applier
    ME-5   — Test coverage increase to 70%
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from bot.apply.ashby import AshbyApplier
from bot.apply.workday import WorkdayApplier
from bot.search.base import RawJob
from core.filter import ScoredJob

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _make_profile(**overrides):
    """Create a mock UserProfile for applier tests."""
    profile = MagicMock()
    profile.full_name = overrides.get("full_name", "Jane Doe")
    profile.first_name = overrides.get("first_name", "Jane")
    profile.last_name = overrides.get("last_name", "Doe")
    profile.email = overrides.get("email", "jane@example.com")
    profile.phone = overrides.get("phone", "555-0100")
    profile.phone_full = overrides.get("phone_full", "+1-555-0100")
    profile.phone_country_code = overrides.get("phone_country_code", "+1")
    profile.linkedin_url = overrides.get("linkedin_url", "https://linkedin.com/in/janedoe")
    profile.portfolio_url = overrides.get("portfolio_url", "https://janedoe.dev")
    profile.location = overrides.get("location", "New York, NY")
    profile.city = overrides.get("city", "New York")
    profile.state = overrides.get("state", "NY")
    profile.zip_code = overrides.get("zip_code", "10001")
    profile.country = overrides.get("country", "United States")
    profile.address_line1 = overrides.get("address_line1", "123 Main St")
    profile.bio = overrides.get("bio", "Experienced engineer.")
    profile.screening_answers = overrides.get("screening_answers", {})
    return profile


def _make_scored_job(
    apply_url="https://company.myworkdayjobs.com/en-US/careers/job/123",
    platform="workday",
):
    raw = RawJob(
        title="Software Engineer",
        company="Acme Corp",
        location="Remote",
        salary="$130K",
        description="Build great software.",
        apply_url=apply_url,
        platform=platform,
        external_id=f"{platform}-123",
        posted_at=None,
    )
    return ScoredJob(
        id="test-uuid", raw=raw, score=85,
        pass_filter=True, skip_reason=None,
    )


def _make_page(url="https://company.myworkdayjobs.com/"):
    """Create a MagicMock Playwright page."""
    page = MagicMock()
    page.url = url
    page.query_selector.return_value = None
    page.query_selector_all.return_value = []
    return page


# ===================================================================
# WorkdayApplier
# ===================================================================


class TestWorkdayApplierSuccess:
    """FR-070: Workday happy path — multi-step form submission."""

    @patch("bot.apply.base.time.sleep")
    def test_successful_submit_on_thank_you(self, _sleep):
        page = _make_page()
        step = {"n": 0}

        def qs(selector):
            if "adventureButton" in selector or "applyManually" in selector:
                btn = MagicMock()
                btn.is_visible.return_value = True
                return btn
            if "thankYouMessage" in selector or "Thank" in selector:
                if step["n"] > 0:
                    el = MagicMock()
                    el.is_visible.return_value = True
                    return el
            if "bottom-navigation-next-button" in selector and "Submit" not in selector:
                step["n"] += 1
                btn = MagicMock()
                btn.is_visible.return_value = True
                return btn
            return None

        page.query_selector.side_effect = qs
        applier = WorkdayApplier(page)
        result = applier.apply(
            _make_scored_job(), Path("/tmp/resume.pdf"), "cover", _make_profile()
        )
        assert result.success is True

    @patch("bot.apply.base.time.sleep")
    def test_captcha_detected_at_start(self, _sleep):
        page = _make_page()
        page.query_selector.return_value = MagicMock()
        applier = WorkdayApplier(page)
        result = applier.apply(_make_scored_job(), None, "", _make_profile())
        assert result.success is False
        assert result.captcha_detected is True

    @patch("bot.apply.base.time.sleep")
    def test_no_apply_button(self, _sleep):
        page = _make_page()
        page.query_selector.return_value = None
        applier = WorkdayApplier(page)
        result = applier.apply(_make_scored_job(), None, "", _make_profile())
        assert result.success is False
        assert result.manual_required is True
        assert "Apply button not found" in result.error_message

    @patch("bot.apply.base.time.sleep")
    def test_exception_returns_failure(self, _sleep):
        page = _make_page()
        page.goto.side_effect = Exception("Network timeout")
        applier = WorkdayApplier(page)
        result = applier.apply(_make_scored_job(), None, "", _make_profile())
        assert result.success is False
        assert "Network timeout" in result.error_message

    @patch("bot.apply.base.time.sleep")
    def test_ran_out_of_steps(self, _sleep):
        page = _make_page()

        def qs(selector):
            if "adventureButton" in selector or "applyManually" in selector:
                btn = MagicMock()
                btn.is_visible.return_value = True
                return btn
            if "bottom-navigation-next-button" in selector and "Submit" not in selector:
                btn = MagicMock()
                btn.is_visible.return_value = True
                return btn
            return None

        page.query_selector.side_effect = qs
        applier = WorkdayApplier(page)
        result = applier.apply(_make_scored_job(), None, "", _make_profile())
        assert result.success is False
        assert "ran out of steps" in result.error_message


class TestWorkdayFormFields:
    """FR-070: Workday form field filling."""

    @patch("bot.apply.base.time.sleep")
    def test_fill_my_information(self, _sleep):
        page = _make_page()
        first_input = MagicMock()
        first_input.is_visible.return_value = True
        first_input.input_value.return_value = ""
        email_input = MagicMock()
        email_input.is_visible.return_value = True
        email_input.input_value.return_value = ""

        def qs(selector):
            if "firstName" in selector:
                return first_input
            if "email" in selector and "signIn" not in selector:
                return email_input
            return None

        page.query_selector.side_effect = qs
        applier = WorkdayApplier(page)
        profile = _make_profile(first_name="Alice", email="alice@test.com")
        applier._fill_my_information(profile)
        assert first_input.type.call_count == len("Alice")
        assert email_input.type.call_count == len("alice@test.com")

    @patch("bot.apply.base.time.sleep")
    def test_fill_my_information_skips_prefilled(self, _sleep):
        page = _make_page()
        first_input = MagicMock()
        first_input.is_visible.return_value = True
        first_input.input_value.return_value = "Already Filled"

        def qs(selector):
            if "firstName" in selector:
                return first_input
            return None

        page.query_selector.side_effect = qs
        applier = WorkdayApplier(page)
        applier._fill_my_information(_make_profile())
        first_input.type.assert_not_called()

    @patch("bot.apply.base.time.sleep")
    def test_fill_my_experience_uploads_resume(self, _sleep):
        page = _make_page()
        file_input = MagicMock()

        def qs(selector):
            if "file" in selector:
                return file_input
            return None

        page.query_selector.side_effect = qs
        resume_path = Path("/tmp/resume.pdf")
        applier = WorkdayApplier(page)
        applier._fill_my_experience(_make_profile(), resume_path)
        file_input.set_input_files.assert_called_once_with(str(resume_path))

    @patch("bot.apply.base.time.sleep")
    def test_fill_my_experience_no_resume(self, _sleep):
        page = _make_page()
        applier = WorkdayApplier(page)
        applier._fill_my_experience(_make_profile(), None)
        page.query_selector.assert_not_called()

    @patch("bot.apply.base.time.sleep")
    def test_fill_application_questions_textarea(self, _sleep):
        page = _make_page()
        ta = MagicMock()
        ta.is_visible.return_value = True
        ta.input_value.return_value = ""
        page.query_selector_all.return_value = [ta]
        applier = WorkdayApplier(page)
        applier._fill_application_questions(_make_profile(), "My cover letter")
        ta.fill.assert_called_once_with("My cover letter")

    @patch("bot.apply.base.time.sleep")
    def test_fill_application_questions_empty_cover(self, _sleep):
        page = _make_page()
        ta = MagicMock()
        ta.is_visible.return_value = True
        ta.input_value.return_value = ""
        page.query_selector_all.return_value = [ta]
        applier = WorkdayApplier(page)
        applier._fill_application_questions(_make_profile(), "")
        ta.fill.assert_not_called()


class TestWorkdayScreeningAnswers:
    """FR-070: Screening question answering."""

    @patch("bot.apply.base.time.sleep")
    def test_answer_screening_questions_matches_keyword(self, _sleep):
        page = _make_page()
        label = MagicMock()
        label.inner_text.return_value = "Are you authorized to work?"
        label.get_attribute.return_value = "auth-input"
        page.query_selector_all.return_value = [label]

        inp = MagicMock()
        inp.is_visible.return_value = True
        inp.input_value.return_value = ""

        def qs(selector):
            if "#auth-input" in selector:
                return inp
            return None

        page.query_selector.side_effect = qs
        profile = _make_profile(screening_answers={"work_authorization": "Yes"})
        applier = WorkdayApplier(page)
        applier._answer_screening_questions(profile)
        assert inp.type.call_count == len("Yes")

    @patch("bot.apply.base.time.sleep")
    def test_answer_screening_no_answers(self, _sleep):
        page = _make_page()
        profile = _make_profile(screening_answers={})
        applier = WorkdayApplier(page)
        applier._answer_screening_questions(profile)
        page.query_selector_all.assert_not_called()


class TestWorkdayNavigation:
    """FR-070: Workday form navigation helpers."""

    @patch("bot.apply.base.time.sleep")
    def test_click_next_by_automation_id(self, _sleep):
        page = _make_page()
        btn = MagicMock()
        btn.is_visible.return_value = True

        def qs(selector):
            if "bottom-navigation-next-button" in selector:
                return btn
            return None

        page.query_selector.side_effect = qs
        applier = WorkdayApplier(page)
        assert applier._click_next_or_submit() is True
        btn.click.assert_called_once()

    @patch("bot.apply.base.time.sleep")
    def test_click_next_fallback(self, _sleep):
        page = _make_page()
        btn = MagicMock()
        btn.is_visible.return_value = True

        def qs(selector):
            if "Next" in selector and "bottom-navigation" not in selector:
                return btn
            return None

        page.query_selector.side_effect = qs
        applier = WorkdayApplier(page)
        assert applier._click_next_or_submit() is True

    @patch("bot.apply.base.time.sleep")
    def test_click_next_not_found(self, _sleep):
        page = _make_page()
        page.query_selector.return_value = None
        applier = WorkdayApplier(page)
        assert applier._click_next_or_submit() is False

    @patch("bot.apply.base.time.sleep")
    def test_click_submit(self, _sleep):
        page = _make_page()
        btn = MagicMock()
        btn.is_visible.return_value = True

        def qs(selector):
            if "Submit" in selector:
                return btn
            return None

        page.query_selector.side_effect = qs
        applier = WorkdayApplier(page)
        assert applier._click_submit() is True
        btn.click.assert_called_once()

    @patch("bot.apply.base.time.sleep")
    def test_is_submitted_true(self, _sleep):
        page = _make_page()
        el = MagicMock()
        el.is_visible.return_value = True

        def qs(selector):
            if "thankYouMessage" in selector:
                return el
            return None

        page.query_selector.side_effect = qs
        applier = WorkdayApplier(page)
        assert applier._is_submitted() is True

    @patch("bot.apply.base.time.sleep")
    def test_is_submitted_false(self, _sleep):
        page = _make_page()
        page.query_selector.return_value = None
        applier = WorkdayApplier(page)
        assert applier._is_submitted() is False

    @patch("bot.apply.base.time.sleep")
    def test_get_page_errors_found(self, _sleep):
        page = _make_page()
        err = MagicMock()
        err.is_visible.return_value = True
        err.inner_text.return_value = "This field is required"

        def qs(selector):
            if "errorMessage" in selector:
                return err
            return None

        page.query_selector.side_effect = qs
        applier = WorkdayApplier(page)
        assert applier._get_page_errors() == "This field is required"

    @patch("bot.apply.base.time.sleep")
    def test_get_page_errors_none(self, _sleep):
        page = _make_page()
        page.query_selector.return_value = None
        applier = WorkdayApplier(page)
        assert applier._get_page_errors() is None


class TestWorkdayDropdown:
    """FR-070: Workday dropdown selection helper."""

    @patch("bot.apply.base.time.sleep")
    def test_select_dropdown_success(self, _sleep):
        page = _make_page()
        btn = MagicMock()
        btn.is_visible.return_value = True
        option = MagicMock()
        option.is_visible.return_value = True

        def qs(selector):
            if "listbox" in selector and "option" in selector:
                return option
            if "gender" in selector:
                return btn
            return None

        page.query_selector.side_effect = qs
        applier = WorkdayApplier(page)
        applier._select_dropdown("gender", "Male")
        btn.click.assert_called_once()
        option.click.assert_called_once()

    @patch("bot.apply.base.time.sleep")
    def test_select_dropdown_no_button(self, _sleep):
        page = _make_page()
        page.query_selector.return_value = None
        applier = WorkdayApplier(page)
        applier._select_dropdown("gender", "Male")


class TestWorkdayAuth:
    """FR-070: Workday auth page handling."""

    @patch("bot.apply.base.time.sleep")
    def test_handle_auth_with_email(self, _sleep):
        page = _make_page()
        email_input = MagicMock()
        email_input.input_value.return_value = ""
        signin_btn = MagicMock()
        signin_btn.is_visible.return_value = True

        def qs(selector):
            if "createAccountLink" in selector:
                return MagicMock()
            # The actual selector is: '[data-automation-id="email"], [data-automation-id="signIn-email"]'
            if 'data-automation-id="email"' in selector:
                return email_input
            if "signInSubmitButton" in selector or "createAccountSubmitButton" in selector:
                return signin_btn
            return None

        page.query_selector.side_effect = qs
        profile = _make_profile(email="test@corp.com", screening_answers={})
        applier = WorkdayApplier(page)
        applier._handle_auth_page(profile)

        assert email_input.type.call_count == len("test@corp.com")
        signin_btn.click.assert_called_once()

    @patch("bot.apply.base.time.sleep")
    def test_handle_auth_already_signed_in(self, _sleep):
        page = _make_page()
        page.query_selector.return_value = None
        applier = WorkdayApplier(page)
        applier._handle_auth_page(_make_profile())


class TestWorkdayVoluntaryDisclosures:
    """FR-070: Voluntary disclosures and self-identification."""

    @patch("bot.apply.base.time.sleep")
    def test_fill_voluntary_disclosures(self, _sleep):
        page = _make_page()
        applier = WorkdayApplier(page)
        profile = _make_profile(screening_answers={
            "gender": "Male", "ethnicity": "Asian", "veteran_status": "No",
        })
        applier._fill_voluntary_disclosures(profile)

    @patch("bot.apply.base.time.sleep")
    def test_fill_self_identification(self, _sleep):
        page = _make_page()
        applier = WorkdayApplier(page)
        profile = _make_profile(screening_answers={"disability_status": "No"})
        applier._fill_self_identification(profile)


class TestWorkdayCoverageGaps:
    """ME-5: Cover remaining branches in WorkdayApplier."""

    @patch("bot.apply.base.time.sleep")
    def test_captcha_detected_during_form_loop(self, _sleep):
        """Line 88: CAPTCHA detected inside the multi-step form loop."""
        page = _make_page()
        call_count = {"n": 0}

        def qs(selector):
            # Apply button succeeds
            if "adventureButton" in selector or "applyManually" in selector:
                btn = MagicMock()
                btn.is_visible.return_value = True
                return btn
            # No CAPTCHA at start (first 5 captcha checks return None)
            if "captcha" in selector or "recaptcha" in selector or "sitekey" in selector:
                call_count["n"] += 1
                # First 5 calls: no captcha (start check), then captcha on loop
                if call_count["n"] <= 5:
                    return None
                return MagicMock()  # CAPTCHA found in loop
            return None

        page.query_selector.side_effect = qs
        applier = WorkdayApplier(page)
        result = applier.apply(_make_scored_job(), None, "", _make_profile())
        assert result.success is False
        assert result.captcha_detected is True
        assert "CAPTCHA detected" in result.error_message

    @patch("bot.apply.base.time.sleep")
    def test_form_error_logged_on_step_gt_0(self, _sleep):
        """Line 100: error_text logged when step > 0."""
        page = _make_page()
        step_counter = {"n": 0}

        def qs(selector):
            if "adventureButton" in selector or "applyManually" in selector:
                btn = MagicMock()
                btn.is_visible.return_value = True
                return btn
            if "errorMessage" in selector:
                step_counter["n"] += 1
                if step_counter["n"] > 1:
                    err = MagicMock()
                    err.is_visible.return_value = True
                    err.inner_text.return_value = "Field required"
                    return err
                return None
            if "bottom-navigation-next-button" in selector and "Submit" not in selector:
                btn = MagicMock()
                btn.is_visible.return_value = True
                return btn
            if "thankYouMessage" in selector:
                if step_counter["n"] > 2:
                    el = MagicMock()
                    el.is_visible.return_value = True
                    return el
                return None
            return None

        page.query_selector.side_effect = qs
        page.query_selector_all.return_value = []
        applier = WorkdayApplier(page)
        result = applier.apply(_make_scored_job(), None, "", _make_profile())
        assert result.success is True

    @patch("bot.apply.base.time.sleep")
    def test_submit_on_final_review_page(self, _sleep):
        """Lines 112-116: click_submit succeeds on final review page."""
        page = _make_page()
        step = {"n": 0}

        def qs(selector):
            if "adventureButton" in selector or "applyManually" in selector:
                btn = MagicMock()
                btn.is_visible.return_value = True
                return btn
            # Next button found on step 0, not found on step 1
            if "bottom-navigation-next-button" in selector and "Submit" not in selector:
                step["n"] += 1
                if step["n"] <= 1:
                    btn = MagicMock()
                    btn.is_visible.return_value = True
                    return btn
                return None
            # Submit button found when Next is not available
            if "Submit" in selector:
                btn = MagicMock()
                btn.is_visible.return_value = True
                return btn
            # Submitted check after submit click
            if "thankYouMessage" in selector or "Thank" in selector:
                if step["n"] > 1:
                    el = MagicMock()
                    el.is_visible.return_value = True
                    return el
                return None
            return None

        page.query_selector.side_effect = qs
        page.query_selector_all.return_value = []
        applier = WorkdayApplier(page)
        result = applier.apply(_make_scored_job(), None, "", _make_profile())
        assert result.success is True

    @patch("bot.apply.base.time.sleep")
    def test_final_is_submitted_check(self, _sleep):
        """Line 122: _is_submitted after loop exhaustion without next/submit."""
        page = _make_page()
        step = {"n": 0}

        def qs(selector):
            if "adventureButton" in selector or "applyManually" in selector:
                btn = MagicMock()
                btn.is_visible.return_value = True
                return btn
            # No next button, no submit button -> breaks loop immediately
            if "bottom-navigation-next-button" in selector:
                return None
            if "Submit" in selector:
                return None
            # Final _is_submitted check returns True
            if "thankYouMessage" in selector or "Thank" in selector:
                step["n"] += 1
                if step["n"] > 5:  # After loop tries
                    el = MagicMock()
                    el.is_visible.return_value = True
                    return el
                return None
            return None

        page.query_selector.side_effect = qs
        page.query_selector_all.return_value = []
        applier = WorkdayApplier(page)
        result = applier.apply(_make_scored_job(), None, "", _make_profile())
        assert result.success is True

    @patch("bot.apply.base.time.sleep")
    def test_apply_button_fallback_text(self, _sleep):
        """Lines 154-155: fallback Apply button by text."""
        page = _make_page()
        call_count = {"n": 0}

        def qs(selector):
            # First 4 specific selectors return None
            if "adventureButton" in selector or "applyManually" in selector:
                return None
            if "jobPostingApplyButton" in selector:
                return None
            # Fallback text-based selector
            if 'has-text("Apply")' in selector:
                btn = MagicMock()
                btn.is_visible.return_value = True
                return btn
            # CAPTCHA: no
            if "captcha" in selector or "recaptcha" in selector or "sitekey" in selector:
                return None
            # submitted immediately
            if "thankYouMessage" in selector or "Thank" in selector:
                call_count["n"] += 1
                if call_count["n"] > 0:
                    el = MagicMock()
                    el.is_visible.return_value = True
                    return el
            return None

        page.query_selector.side_effect = qs
        page.query_selector_all.return_value = []
        applier = WorkdayApplier(page)
        result = applier.apply(_make_scored_job(), None, "", _make_profile())
        assert result.success is True

    @patch("bot.apply.base.time.sleep")
    def test_auth_page_fills_password(self, _sleep):
        """Lines 193-199: password fill from screening_answers."""
        page = _make_page()
        email_input = MagicMock()
        email_input.input_value.return_value = ""
        pw_input = MagicMock()
        signin_btn = MagicMock()
        signin_btn.is_visible.return_value = True

        def qs(selector):
            if "createAccountLink" in selector:
                return MagicMock()
            if 'data-automation-id="email"' in selector:
                return email_input
            if "password" in selector:
                return pw_input
            if "signInSubmitButton" in selector or "createAccountSubmitButton" in selector:
                return signin_btn
            return None

        page.query_selector.side_effect = qs
        profile = _make_profile(
            email="test@corp.com",
            screening_answers={"workday_password": "s3cret"}
        )
        applier = WorkdayApplier(page)
        applier._handle_auth_page(profile)
        assert pw_input.type.call_count == len("s3cret")

    @patch("bot.apply.base.time.sleep")
    def test_fill_my_information_skips_empty_value(self, _sleep):
        """Line 230: skip field when profile value is empty/None."""
        page = _make_page()
        page.query_selector.return_value = None
        applier = WorkdayApplier(page)
        profile = _make_profile(first_name="", email="")
        applier._fill_my_information(profile)
        # No assertion needed — just confirming no error and the branch is covered

    @patch("bot.apply.base.time.sleep")
    def test_fill_my_experience_upload_exception(self, _sleep):
        """Lines 266-267: resume upload failure handled gracefully."""
        page = _make_page()
        file_input = MagicMock()
        file_input.set_input_files.side_effect = Exception("Permission denied")

        def qs(selector):
            if "file" in selector:
                return file_input
            return None

        page.query_selector.side_effect = qs
        applier = WorkdayApplier(page)
        applier._fill_my_experience(_make_profile(), Path("/tmp/resume.pdf"))
        assert file_input.set_input_files.call_count == 3
        file_input.set_input_files.assert_any_call("/tmp/resume.pdf")

    @patch("bot.apply.base.time.sleep")
    def test_screening_questions_label_text_exception(self, _sleep):
        """Lines 320-322: label.inner_text() raises exception."""
        page = _make_page()
        label = MagicMock()
        label.inner_text.side_effect = Exception("Detached element")
        page.query_selector_all.return_value = [label]

        profile = _make_profile(screening_answers={"work_authorization": "Yes"})
        applier = WorkdayApplier(page)
        applier._answer_screening_questions(profile)
        # Should not raise — exception is caught and logged

    @patch("bot.apply.base.time.sleep")
    def test_click_submit_all_selectors_fail(self, _sleep):
        """Line 399: all 4 submit selectors return None."""
        page = _make_page()
        page.query_selector.return_value = None
        applier = WorkdayApplier(page)
        assert applier._click_submit() is False

    @patch("bot.apply.base.time.sleep")
    def test_is_submitted_exception_path(self, _sleep):
        """Lines 416-418: query_selector raises during submitted check."""
        page = _make_page()
        call_count = {"n": 0}

        def qs(selector):
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise Exception("Element detached")
            return None

        page.query_selector.side_effect = qs
        applier = WorkdayApplier(page)
        assert applier._is_submitted() is False

    @patch("bot.apply.base.time.sleep")
    def test_get_page_errors_inner_text_exception(self, _sleep):
        """Lines 433-435: inner_text() raises on error element."""
        page = _make_page()
        err_el = MagicMock()
        err_el.is_visible.return_value = True
        err_el.inner_text.side_effect = Exception("Detached")

        def qs(selector):
            if "errorMessage" in selector:
                return err_el
            return None

        page.query_selector.side_effect = qs
        applier = WorkdayApplier(page)
        assert applier._get_page_errors() == "Unknown error"

    @patch("bot.apply.base.time.sleep")
    def test_select_dropdown_listbox_timeout(self, _sleep):
        """Lines 464-466: listbox wait_for_selector times out."""
        page = _make_page()
        btn = MagicMock()
        btn.is_visible.return_value = True

        def qs(selector):
            if "gender" in selector:
                return btn
            return None

        page.query_selector.side_effect = qs
        page.wait_for_selector.side_effect = Exception("Timeout waiting for listbox")
        applier = WorkdayApplier(page)
        applier._select_dropdown("gender", "Male")
        btn.click.assert_called_once()


class TestWorkdayRegistration:
    """FR-070: Workday registered in pipeline."""

    def test_workday_in_appliers(self):
        from bot.bot import APPLIERS
        assert "workday" in APPLIERS
        assert APPLIERS["workday"] is WorkdayApplier

    def test_ats_detection_routes_to_workday(self):
        from core.filter import detect_ats
        assert detect_ats("https://company.myworkdayjobs.com/en-US/careers/job/123") == "workday"


# ===================================================================
# AshbyApplier
# ===================================================================


class TestAshbyApplierSuccess:
    """FR-071: Ashby happy path."""

    @patch("bot.apply.base.time.sleep")
    def test_successful_application(self, _sleep):
        page = _make_page("https://jobs.ashbyhq.com/acme/app/123")
        submit_btn = MagicMock()
        success_el = MagicMock()

        def qs(selector):
            if 'type="submit"' in selector:
                return submit_btn
            if "submitted" in selector.lower() or "Thank" in selector:
                return success_el
            return None

        page.query_selector.side_effect = qs
        applier = AshbyApplier(page)
        job = _make_scored_job("https://jobs.ashbyhq.com/acme/app/123", "ashby")
        result = applier.apply(job, Path("/tmp/resume.pdf"), "cover", _make_profile())
        assert result.success is True

    @patch("bot.apply.base.time.sleep")
    def test_captcha_detected(self, _sleep):
        page = _make_page("https://jobs.ashbyhq.com/acme/app/123")
        page.query_selector.return_value = MagicMock()
        applier = AshbyApplier(page)
        job = _make_scored_job("https://jobs.ashbyhq.com/acme/app/123", "ashby")
        result = applier.apply(job, None, "", _make_profile())
        assert result.success is False
        assert result.captcha_detected is True

    @patch("bot.apply.base.time.sleep")
    def test_no_submit_button(self, _sleep):
        page = _make_page("https://jobs.ashbyhq.com/acme/app/123")
        page.query_selector.return_value = None
        applier = AshbyApplier(page)
        job = _make_scored_job("https://jobs.ashbyhq.com/acme/app/123", "ashby")
        result = applier.apply(job, None, "", _make_profile())
        assert result.success is False
        assert result.manual_required is True

    @patch("bot.apply.base.time.sleep")
    def test_exception_returns_failure(self, _sleep):
        page = _make_page("https://jobs.ashbyhq.com/acme/app/123")
        page.goto.side_effect = Exception("Timeout")
        applier = AshbyApplier(page)
        job = _make_scored_job("https://jobs.ashbyhq.com/acme/app/123", "ashby")
        result = applier.apply(job, None, "", _make_profile())
        assert result.success is False
        assert "Timeout" in result.error_message

    @patch("bot.apply.base.time.sleep")
    def test_form_error_after_submit(self, _sleep):
        page = _make_page("https://jobs.ashbyhq.com/acme/app/123")
        submit_btn = MagicMock()
        error_el = MagicMock()
        error_el.is_visible.return_value = True
        error_el.inner_text.return_value = "Email is required"

        def qs(selector):
            if 'type="submit"' in selector:
                return submit_btn
            if "alert" in selector or "error" in selector:
                return error_el
            return None

        page.query_selector.side_effect = qs
        applier = AshbyApplier(page)
        job = _make_scored_job("https://jobs.ashbyhq.com/acme/app/123", "ashby")
        result = applier.apply(job, None, "", _make_profile())
        assert result.success is False
        assert "Ashby form error" in result.error_message


class TestAshbyFormFields:
    """FR-071: Ashby form field filling."""

    @patch("bot.apply.base.time.sleep")
    def test_fill_name_and_email(self, _sleep):
        page = _make_page()
        name_input = MagicMock()
        name_input.is_visible.return_value = True
        name_input.input_value.return_value = ""
        email_input = MagicMock()
        email_input.is_visible.return_value = True
        email_input.input_value.return_value = ""

        def qs(selector):
            if 'name="name"' in selector or 'name*="Name"' in selector:
                return name_input
            if 'name="email"' in selector or 'type="email"' in selector:
                return email_input
            return None

        page.query_selector.side_effect = qs
        applier = AshbyApplier(page)
        profile = _make_profile(full_name="Alice Smith", email="alice@test.com")
        applier._fill_form_fields(profile)
        assert name_input.type.call_count == len("Alice Smith")
        assert email_input.type.call_count == len("alice@test.com")

    @patch("bot.apply.base.time.sleep")
    def test_fill_linkedin_url(self, _sleep):
        page = _make_page()
        li_input = MagicMock()
        li_input.is_visible.return_value = True
        li_input.input_value.return_value = ""

        def qs(selector):
            if "linkedin" in selector.lower():
                return li_input
            return None

        page.query_selector.side_effect = qs
        applier = AshbyApplier(page)
        profile = _make_profile(linkedin_url="https://linkedin.com/in/alice")
        applier._fill_form_fields(profile)
        assert li_input.type.call_count == len("https://linkedin.com/in/alice")

    @patch("bot.apply.base.time.sleep")
    def test_skips_prefilled_fields(self, _sleep):
        page = _make_page()
        inp = MagicMock()
        inp.is_visible.return_value = True
        inp.input_value.return_value = "Already Filled"
        page.query_selector.return_value = inp
        applier = AshbyApplier(page)
        applier._fill_form_fields(_make_profile())
        inp.type.assert_not_called()


class TestAshbyResumeUpload:
    """FR-071: Ashby resume upload."""

    @patch("bot.apply.base.time.sleep")
    def test_upload_resume(self, _sleep):
        page = _make_page()
        file_input = MagicMock()

        def qs(selector):
            if 'type="file"' in selector:
                return file_input
            return None

        page.query_selector.side_effect = qs
        resume_path = Path("/tmp/resume.pdf")
        applier = AshbyApplier(page)
        applier._upload_resume(resume_path)
        file_input.set_input_files.assert_called_once_with(str(resume_path))

    @patch("bot.apply.base.time.sleep")
    def test_upload_resume_no_input(self, _sleep):
        page = _make_page()
        page.query_selector.return_value = None
        applier = AshbyApplier(page)
        applier._upload_resume(Path("/tmp/resume.pdf"))

    @patch("bot.apply.base.time.sleep")
    def test_upload_resume_exception_handled(self, _sleep):
        page = _make_page()
        fi = MagicMock()
        fi.set_input_files.side_effect = Exception("Upload failed")
        page.query_selector.return_value = fi
        applier = AshbyApplier(page)
        applier._upload_resume(Path("/tmp/resume.pdf"))


class TestAshbyCoverLetter:
    """FR-071: Ashby cover letter filling."""

    @patch("bot.apply.base.time.sleep")
    def test_fill_cover_letter(self, _sleep):
        page = _make_page()
        ta = MagicMock()
        ta.is_visible.return_value = True
        ta.input_value.return_value = ""

        def qs(selector):
            if "cover" in selector.lower():
                return ta
            return None

        page.query_selector.side_effect = qs
        applier = AshbyApplier(page)
        applier._fill_cover_letter("My cover letter")
        ta.fill.assert_called_once_with("My cover letter")

    @patch("bot.apply.base.time.sleep")
    def test_fill_cover_letter_empty(self, _sleep):
        page = _make_page()
        applier = AshbyApplier(page)
        applier._fill_cover_letter("")
        page.query_selector.assert_not_called()

    @patch("bot.apply.base.time.sleep")
    def test_fill_additional_info_fallback(self, _sleep):
        page = _make_page()
        ta = MagicMock()
        ta.is_visible.return_value = True
        ta.input_value.return_value = ""

        def qs(selector):
            if "additional" in selector.lower():
                return ta
            return None

        page.query_selector.side_effect = qs
        applier = AshbyApplier(page)
        applier._fill_cover_letter("Text")
        ta.fill.assert_called_once_with("Text")


class TestAshbyCustomQuestions:
    """FR-071: Ashby custom question answering."""

    @patch("bot.apply.base.time.sleep")
    def test_answer_text_input(self, _sleep):
        page = _make_page()
        label = MagicMock()
        label.inner_text.return_value = "Years of experience"
        label.get_attribute.return_value = "exp-input"
        page.query_selector_all.return_value = [label]
        inp = MagicMock()
        inp.is_visible.return_value = True
        inp.input_value.return_value = ""
        inp.evaluate.return_value = "input"

        def qs(selector):
            if "#exp-input" in selector:
                return inp
            return None

        page.query_selector.side_effect = qs
        profile = _make_profile(screening_answers={"years of experience": "5"})
        applier = AshbyApplier(page)
        applier._answer_custom_questions(profile)
        assert inp.type.call_count == len("5")

    @patch("bot.apply.base.time.sleep")
    def test_answer_select(self, _sleep):
        page = _make_page()
        label = MagicMock()
        label.inner_text.return_value = "Work authorization"
        label.get_attribute.return_value = "auth-sel"
        page.query_selector_all.return_value = [label]
        sel = MagicMock()
        sel.is_visible.return_value = True
        sel.input_value.return_value = ""
        sel.evaluate.return_value = "select"

        def qs(selector):
            if "#auth-sel" in selector:
                return sel
            return None

        page.query_selector.side_effect = qs
        profile = _make_profile(screening_answers={"work authorization": "Yes"})
        applier = AshbyApplier(page)
        applier._answer_custom_questions(profile)
        sel.select_option.assert_called_once_with(label="Yes")

    @patch("bot.apply.base.time.sleep")
    def test_answer_textarea(self, _sleep):
        page = _make_page()
        label = MagicMock()
        label.inner_text.return_value = "Tell us about yourself"
        label.get_attribute.return_value = "about-ta"
        page.query_selector_all.return_value = [label]
        ta = MagicMock()
        ta.is_visible.return_value = True
        ta.input_value.return_value = ""
        ta.evaluate.return_value = "textarea"

        def qs(selector):
            if "#about-ta" in selector:
                return ta
            return None

        page.query_selector.side_effect = qs
        profile = _make_profile(screening_answers={"tell us about yourself": "Great dev"})
        applier = AshbyApplier(page)
        applier._answer_custom_questions(profile)
        ta.fill.assert_called_once_with("Great dev")

    @patch("bot.apply.base.time.sleep")
    def test_no_answers_skips(self, _sleep):
        page = _make_page()
        profile = _make_profile(screening_answers={})
        applier = AshbyApplier(page)
        applier._answer_custom_questions(profile)
        page.query_selector_all.assert_not_called()


class TestAshbyCoverageGaps:
    """ME-5: Cover remaining branches in AshbyApplier."""

    @patch("bot.apply.base.time.sleep")
    def test_apply_button_clicked_on_detail_page(self, _sleep):
        """Lines 82-83: Apply button visible on job detail page."""
        page = _make_page("https://jobs.ashbyhq.com/acme/app/123")
        apply_btn = MagicMock()
        apply_btn.is_visible.return_value = True
        submit_btn = MagicMock()
        success_el = MagicMock()

        def qs(selector):
            if "Apply" in selector and 'type="submit"' not in selector:
                return apply_btn
            if 'type="submit"' in selector:
                return submit_btn
            if "submitted" in selector.lower() or "Thank" in selector:
                return success_el
            return None

        page.query_selector.side_effect = qs
        applier = AshbyApplier(page)
        job = _make_scored_job("https://jobs.ashbyhq.com/acme/app/123", "ashby")
        result = applier.apply(job, None, "", _make_profile())
        assert result.success is True
        apply_btn.click.assert_called_once()

    @patch("bot.apply.base.time.sleep")
    def test_no_success_no_error_returns_success(self, _sleep):
        """Line 141: no success indicator and no error — assumes success."""
        page = _make_page("https://jobs.ashbyhq.com/acme/app/123")
        submit_btn = MagicMock()

        def qs(selector):
            if 'type="submit"' in selector:
                return submit_btn
            return None

        page.query_selector.side_effect = qs
        applier = AshbyApplier(page)
        job = _make_scored_job("https://jobs.ashbyhq.com/acme/app/123", "ashby")
        result = applier.apply(job, None, "", _make_profile())
        assert result.success is True

    @patch("bot.apply.base.time.sleep")
    def test_fill_form_skips_empty_value(self, _sleep):
        """Line 158: skip field when profile value is empty/None."""
        page = _make_page()
        page.query_selector.return_value = None
        applier = AshbyApplier(page)
        profile = _make_profile(full_name="", first_name="", email="")
        applier._fill_form_fields(profile)

    @patch("bot.apply.base.time.sleep")
    def test_fill_portfolio_url(self, _sleep):
        """Lines 185-186: portfolio URL field is filled."""
        page = _make_page()
        portfolio_input = MagicMock()
        portfolio_input.is_visible.return_value = True
        portfolio_input.input_value.return_value = ""

        def qs(selector):
            if "website" in selector or "portfolio" in selector or "Portfolio" in selector:
                return portfolio_input
            return None

        page.query_selector.side_effect = qs
        profile = _make_profile(portfolio_url="https://janedoe.dev")
        applier = AshbyApplier(page)
        applier._fill_form_fields(profile)
        assert portfolio_input.type.call_count == len("https://janedoe.dev")

    @patch("bot.apply.base.time.sleep")
    def test_fill_location_field(self, _sleep):
        """Lines 195-196: location field is filled."""
        page = _make_page()
        loc_input = MagicMock()
        loc_input.is_visible.return_value = True
        loc_input.input_value.return_value = ""

        def qs(selector):
            if "location" in selector or "Location" in selector:
                return loc_input
            return None

        page.query_selector.side_effect = qs
        profile = _make_profile(location="New York, NY")
        applier = AshbyApplier(page)
        applier._fill_form_fields(profile)
        assert loc_input.type.call_count == len("New York, NY")

    @patch("bot.apply.base.time.sleep")
    def test_custom_questions_label_exception(self, _sleep):
        """Lines 258-260: label.inner_text() raises exception."""
        page = _make_page()
        label = MagicMock()
        label.inner_text.side_effect = Exception("Detached element")
        page.query_selector_all.return_value = [label]

        profile = _make_profile(screening_answers={"some_key": "some_value"})
        applier = AshbyApplier(page)
        applier._answer_custom_questions(profile)
        # Should not raise

    @patch("bot.apply.base.time.sleep")
    def test_custom_questions_skips_empty_answer_value(self, _sleep):
        """Line 265: skip answer when value is empty."""
        page = _make_page()
        label = MagicMock()
        label.inner_text.return_value = "years of experience"
        page.query_selector_all.return_value = [label]

        profile = _make_profile(screening_answers={"years of experience": ""})
        applier = AshbyApplier(page)
        applier._answer_custom_questions(profile)
        label.get_attribute.assert_not_called()


class TestAshbyRegistration:
    """FR-071: Ashby registered in pipeline."""

    def test_ashby_in_appliers(self):
        from bot.bot import APPLIERS
        assert "ashby" in APPLIERS
        assert APPLIERS["ashby"] is AshbyApplier

    def test_ats_detection_routes_to_ashby(self):
        from core.filter import detect_ats
        assert detect_ats("https://jobs.ashbyhq.com/acme/app/123") == "ashby"
