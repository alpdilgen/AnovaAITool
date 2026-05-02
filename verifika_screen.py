"""
Verifika QA Tab — Streamlit UI (v3, report-based workflow).

Drives the v3 VerifikaQAClient:
    create_project → upload → create_report → run_report
    → poll /api/QualityIssues?reportId=... (statuses[] array)
    → fetch issues
    → Apply Corrections (locally + back to Verifika via
       /api/QualityIssues/updateTranslationUnits)
"""

from __future__ import annotations

from datetime import datetime
from typing import Dict, List, Optional

import streamlit as st
import streamlit.components.v1 as components

from services.verifika_qa_client import (
    VerifikaQAClient,
    VerifikaError,
    DEFAULT_BASE_URL,
    ISSUE_TYPE_LABELS,
)
from utils.xml_parser import XMLParser


# ─────────────────────────────────────────────────────────────────────────────
# Session-state init
# ─────────────────────────────────────────────────────────────────────────────

def _init_session_state():
    defaults = {
        "verifika_client":            None,
        "verifika_qa_profiles":       [],
        "verifika_qa_profile_id":     None,
        "verifika_project_id":        None,
        "verifika_report_id":         None,
        "verifika_task_id":           None,
        "verifika_issues":            [],
        "verifika_run_status":        "idle",   # idle/running/done/error
        "verifika_last_error":        "",
        "verifika_progress_messages": [],
        "verifika_corrected_xliff":   None,
        "verifika_last_statuses":     [],
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v


def _get_secret(key: str, default: str = "") -> str:
    try:
        if hasattr(st, "secrets") and key in st.secrets:
            return st.secrets[key]
    except Exception:
        pass
    return default


def _build_client() -> Optional[VerifikaQAClient]:
    api_token = _get_secret("verifika_api_token")
    username  = _get_secret("verifika_username")
    password  = _get_secret("verifika_password")
    base_url  = _get_secret("verifika_base_url", DEFAULT_BASE_URL)

    if not (api_token or (username and password)):
        st.error(
            "Verifika credentials not configured. Add to Streamlit secrets:\n"
            "    verifika_api_token = \"<token>\"\n"
            "or\n"
            "    verifika_username = \"...\"\n"
            "    verifika_password = \"...\""
        )
        return None

    client = VerifikaQAClient(
        base_url=base_url,
        api_token=api_token or None,
        username=username or None,
        password=password or None,
    )
    if username and password and not api_token:
        try:
            client.login()
        except VerifikaError as e:
            st.error(f"Verifika login failed: {e}")
            return None
    return client


def _get_or_create_client() -> Optional[VerifikaQAClient]:
    if st.session_state.verifika_client is None:
        st.session_state.verifika_client = _build_client()
    return st.session_state.verifika_client


def _load_qa_profiles(client: VerifikaQAClient) -> List[Dict]:
    try:
        profiles = client.list_qa_settings()
        st.session_state.verifika_qa_profiles = profiles
        return profiles
    except VerifikaError as e:
        st.error(f"Failed to load QA profiles: {e}")
        return []


def _severity_icon(sev: str) -> str:
    s = (sev or "").lower()
    if s in ("error", "critical", "high"):
        return "🔴"
    if s in ("warning", "medium"):
        return "🟡"
    return "🔵"


# ─────────────────────────────────────────────────────────────────────────────
# Main entry
# ─────────────────────────────────────────────────────────────────────────────

def show_verifika_tab():
    _init_session_state()

    st.subheader("✅ Verifika Cloud QA")
    st.markdown(
        "Cloud-based quality checks against the translated XLIFF via the "
        "Verifika QA API."
    )

    if not st.session_state.get("translation_results"):
        st.info("Run a translation first (Workspace tab) to enable QA.")
        return

    client = _get_or_create_client()
    if client is None:
        st.markdown(
            "**Setup:** Add to `.streamlit/secrets.toml`:\n"
            "```toml\nverifika_api_token = \"<your-token>\"\n```"
        )
        return

    # ── 1. QA profile picker ──────────────────────────────────────────────
    st.markdown("##### 1. Select QA Profile")
    refresh_col, picker_col = st.columns([1, 4])
    with refresh_col:
        if st.button("🔄 Refresh profiles", use_container_width=True):
            _load_qa_profiles(client)

    if not st.session_state.verifika_qa_profiles:
        with st.spinner("Loading QA profiles…"):
            _load_qa_profiles(client)

    profiles = st.session_state.verifika_qa_profiles
    if not profiles:
        with picker_col:
            st.warning(
                "No QA profiles available. Create one in Verifika Web/Desktop, then refresh."
            )
        return

    labels, ids = [], []
    for p in profiles:
        pid = p.get("id") or p.get("Id") or ""
        labels.append(p.get("name") or p.get("Name") or pid[:8])
        ids.append(pid)

    default_idx = 0
    if st.session_state.verifika_qa_profile_id in ids:
        default_idx = ids.index(st.session_state.verifika_qa_profile_id)

    with picker_col:
        chosen_label = st.selectbox(
            "Profile", options=labels, index=default_idx,
            key="verifika_profile_select",
        )
    chosen_id = ids[labels.index(chosen_label)]
    st.session_state.verifika_qa_profile_id = chosen_id

    # ── 2. Run ────────────────────────────────────────────────────────────
    st.markdown("##### 2. Run QA")
    run_col, status_col = st.columns([1, 4])
    with run_col:
        run_clicked = st.button(
            "▶️ Run Verifika QA",
            type="primary",
            use_container_width=True,
            disabled=st.session_state.verifika_run_status == "running",
        )

    with status_col:
        s = st.session_state.verifika_run_status
        if s == "running":
            st.info("⏳ Running…")
        elif s == "done":
            n = len(st.session_state.verifika_issues)
            st.success(f"✅ Completed — {n} issue(s) found")
        elif s == "error":
            st.error(st.session_state.verifika_last_error or "Failed")

    if run_clicked:
        _run_qa_workflow(client, chosen_id)

    # ── 3. Report viewer ──────────────────────────────────────────────────
    if st.session_state.verifika_project_id and st.session_state.verifika_run_status == "done":
        _render_report_section(client)

    # ── 4. Issue table ────────────────────────────────────────────────────
    if st.session_state.verifika_issues:
        st.markdown("##### 4. Issues (editable)")
        _render_issue_table(st.session_state.verifika_issues)
        _render_apply_corrections(client)


# ─────────────────────────────────────────────────────────────────────────────
# Workflow runner
# ─────────────────────────────────────────────────────────────────────────────

def _run_qa_workflow(client: VerifikaQAClient, qa_settings_id: str):
    st.session_state.verifika_run_status = "running"
    st.session_state.verifika_progress_messages = []
    st.session_state.verifika_issues = []
    st.session_state.verifika_task_id = None
    st.session_state.verifika_last_error = ""
    st.session_state.verifika_corrected_xliff = None
    st.session_state.verifika_last_statuses = []

    xliff_bytes    = st.session_state.get("last_xliff_bytes")
    xliff_filename = st.session_state.get("last_xliff_filename") or "translated.xliff"
    seg_objs       = st.session_state.get("segment_objects", {})
    translations   = st.session_state.get("translation_results", {})
    match_scores   = st.session_state.get("segment_match_scores", {})

    if not xliff_bytes:
        st.session_state.verifika_run_status = "error"
        st.session_state.verifika_last_error = (
            "Original XLIFF bytes are not in session. "
            "Re-upload the XLIFF in Workspace tab."
        )
        st.error(st.session_state.verifika_last_error)
        return

    try:
        translated_xml = XMLParser.update_xliff(
            xliff_bytes, translations, seg_objs, match_scores=match_scores,
        )
    except Exception as e:
        st.session_state.verifika_run_status = "error"
        st.session_state.verifika_last_error = f"XLIFF rebuild failed: {e}"
        st.error(st.session_state.verifika_last_error)
        return

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    base = xliff_filename.rsplit(".", 1)[0]
    project_name = f"{base}_QA_{timestamp}"

    progress_box = st.empty()
    msgs: List[str] = []

    label_map = {
        "project_created":      "📁 Project created",
        "file_uploaded":        "⬆️ File uploaded",
        "project_started":      "🟢 Project started (task created)",
        "task_ready":           "📋 Task ready",
        "tasks_accepted":       "✅ Task accepted",
        "report_created":       "📊 Report created",
        "qa_check_triggered":   "⭐ QA check triggered",
        "report_started":       "🚀 QA analysis started (fallback)",
        "qa_completed":         "🎯 QA analysis completed",
        "issues_fetched":       "📥 Issues fetched",
    }

    def _ui_progress(stage: str, payload: Dict):
        if stage == "qa_progress":
            statuses = payload.get("statuses") or []
            st.session_state.verifika_last_statuses = statuses
            done = sum(1 for s in statuses if int(s.get("status", 0) or 0) == 1)
            total = len(statuses)
            issues_so_far = len(payload.get("qualityIssues") or [])
            # Build per-category breakdown
            cats = []
            for s in statuses:
                t = s.get("issueType")
                done_one = int(s.get("status", 0) or 0) == 1
                label = ISSUE_TYPE_LABELS.get(t, f"T{t}")
                cats.append(f"{'✓' if done_one else '⏳'} {label}")
            cat_str = " · ".join(cats)
            msg = (f"⏳ Polling… {done}/{total} categories ready, "
                   f"{issues_so_far} issue(s) found so far\n  {cat_str}")
        elif stage == "task_ready":
            # Capture the running task id — we need it for
            # client.update_translation_units (per-unit recheck endpoint).
            tid = payload.get("id") or payload.get("Id")
            if tid:
                st.session_state.verifika_task_id = tid
            msg = label_map.get(stage, stage)
        elif stage == "issues_fetched":
            msg = f"📥 {payload.get('count', 0)} issue(s) fetched"
        elif stage == "qa_completed":
            statuses = payload.get("statuses") or []
            issues = payload.get("qualityIssues") or []
            msg = (f"🎯 QA completed — "
                   f"{len(statuses)} categories all ready, "
                   f"{len(issues)} issue(s)")
        else:
            msg = label_map.get(stage, stage)
        msgs.append(msg)
        progress_box.markdown("\n".join(f"- {m}" for m in msgs[-12:]))

    try:
        project_id, report_id, issues = client.run_full_qa(
            project_name=project_name,
            xliff_bytes=translated_xml,
            xliff_filename=xliff_filename,
            qa_settings_id=qa_settings_id,
            progress_cb=_ui_progress,
        )
        st.session_state.verifika_project_id = project_id
        st.session_state.verifika_report_id  = report_id
        st.session_state.verifika_issues     = issues
        st.session_state.verifika_run_status = "done"
        if issues:
            st.success(f"Found {len(issues)} issue(s).")
        else:
            st.success("✅ No issues reported by Verifika.")

    except VerifikaError as e:
        st.session_state.verifika_run_status = "error"
        st.session_state.verifika_last_error = str(e)
        st.error(f"Verifika error: {e}")
        if e.response_body:
            with st.expander("Response details"):
                st.code(e.response_body)
    except Exception as e:
        st.session_state.verifika_run_status = "error"
        st.session_state.verifika_last_error = f"Unexpected error: {e}"
        st.error(st.session_state.verifika_last_error)


# ─────────────────────────────────────────────────────────────────────────────
# Report viewer (link + iframe attempt)
# ─────────────────────────────────────────────────────────────────────────────

def _render_report_section(client: VerifikaQAClient):
    st.markdown("##### 3. Verifika Report")
    project_id = st.session_state.verifika_project_id
    url = client.report_url(project_id)

    cols = st.columns([2, 2, 1])
    with cols[0]:
        st.markdown(
            f"🔗 [Open in Verifika (new tab)]({url})",
            help="Opens Verifika's review screen in a new tab. "
                 "Requires you to be logged into Verifika in the same browser."
        )
    with cols[1]:
        show_iframe = st.toggle(
            "Try inline iframe (often fails — see notes)",
            value=False,
            key="verifika_iframe_toggle",
            help=(
                "Verifika's auth provider blocks iframe embedding "
                "(X-Frame-Options on auth.e-verifika.com). The iframe "
                "will likely show an error. The 'Open in new tab' link "
                "is the reliable option."
            ),
        )

    if show_iframe:
        components.iframe(url, height=900, scrolling=True)


# ─────────────────────────────────────────────────────────────────────────────
# Issue table & corrections
# ─────────────────────────────────────────────────────────────────────────────

def _highlight_target(text: str, ranges: list) -> str:
    """
    Wrap each Verifika target range in a red <mark> so the user can
    see the exact problematic substring. ranges is a list of
    {start, length, end} dicts. Falls back to plain text on any error.
    """
    if not text or not ranges:
        return _escape_html(text or "")
    try:
        # Sort by start desc so we can splice without shifting indexes
        sorted_r = sorted(
            [r for r in ranges if isinstance(r, dict)],
            key=lambda x: int(x.get("start", 0) or 0),
            reverse=True,
        )
        out = text
        for r in sorted_r:
            start = int(r.get("start", 0) or 0)
            length = int(r.get("length", 0) or 0)
            if length <= 0 or start < 0 or start >= len(out):
                continue
            end = start + length
            chunk = out[start:end]
            out = (
                _escape_html(out[:start])
                + f"<mark style=\"background:#ffcdd2;padding:0 2px;"
                  f"border-radius:2px;\">"
                + _escape_html(chunk)
                + "</mark>"
                + _escape_html(out[end:])
            )
            return out  # only highlight the first range to avoid recursion
        return _escape_html(out)
    except Exception:
        return _escape_html(text)


def _escape_html(text: str) -> str:
    """Minimal HTML escape — Streamlit unsafe_allow_html bypasses
    its own escape, so we do it ourselves."""
    if text is None:
        return ""
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _render_issue_table(issues: List[Dict]):
    if not issues:
        return

    # Filters — use issueCategory (broad) for the multiselect so users
    # can pick "Spelling vs Common vs Terminology" etc., and the
    # specific issueKind text shows in each row.
    f1, f2 = st.columns(2)
    with f1:
        cat_options = sorted({i.get("issueCategory") or i["issueLabel"]
                              for i in issues})
        selected_cats = st.multiselect(
            "Filter by category", options=cat_options,
            default=cat_options,
        )
    with f2:
        show_ignored = st.checkbox("Show ignored issues", value=False)

    filtered = [
        i for i in issues
        if (i.get("issueCategory") or i["issueLabel"]) in selected_cats
        and (show_ignored or not i.get("isIgnored"))
    ]

    st.caption(
        f"Showing {len(filtered)} of {len(issues)} issue(s). "
        "Edit a target cell directly (press Enter to confirm), or tick "
        "**False positive** to mark the issue as ignored. "
        "Click **Apply Corrections** at the bottom to push everything to Verifika."
    )

    # Header row — 7 columns: severity, category, seg, source,
    # target (editable), detail/suggestions, false-positive checkbox
    header_cols = st.columns([1, 2, 1, 4, 4, 4, 2])
    for col, hdr in zip(header_cols,
                        ["", "Category", "Seg", "Source",
                         "Target (editable)", "Issue / Suggestions",
                         "False positive"]):
        col.markdown(f"**{hdr}**")
    st.markdown("---")

    for idx, iss in enumerate(filtered):
        cols = st.columns([1, 2, 1, 4, 4, 4, 2])

        # Severity icon
        cols[0].write(_severity_icon(iss["severity"]))

        # Category (broad) + small issueKind underneath
        cat = iss.get("issueCategory") or iss["issueLabel"]
        kind = iss.get("issueKind") or ""
        cat_html = f"**{_escape_html(cat)}**"
        if kind and kind != cat:
            cat_html += f"<br><span style='font-size:0.85em;color:#666;'>{_escape_html(kind)}</span>"
        cols[1].markdown(cat_html, unsafe_allow_html=True)

        # Segment id
        cols[2].code(str(iss["segmentId"]) or "—")

        # Source — highlight problematic ranges
        src = iss["sourceText"] or ""
        src_html = _highlight_target(src, iss.get("sourceRanges") or [])
        # Truncate very long
        if len(src) > 200:
            src_html = src_html[:600] + "…"
        cols[3].markdown(
            f"<div style='font-size:0.9em;line-height:1.35;'>{src_html}</div>",
            unsafe_allow_html=True,
        )

        # Target — editable, with a small preview ABOVE showing the
        # highlighted version of Verifika's reported target
        edit_key = f"verifika_edit_{iss['id'] or idx}_{iss['segmentId']}"
        # Separate override key — Streamlit forbids writing to a widget
        # key directly, so 'Apply fix' buttons stash their result here
        # and the text_input picks it up via its `value=` argument.
        override_key = edit_key + "__override"
        verifika_target = iss["targetText"] or ""
        tgt_ranges = iss.get("targetRanges") or []
        tgt_html = _highlight_target(verifika_target, tgt_ranges)
        if len(verifika_target) > 200:
            tgt_html = tgt_html[:600] + "…"
        cols[4].markdown(
            f"<div style='font-size:0.85em;line-height:1.35;color:#555;"
            f"margin-bottom:4px;'>{tgt_html}</div>",
            unsafe_allow_html=True,
        )

        # Resolve the value to display in the editable input.
        # Priority: override (just-applied fix) > current translation > Verifika target.
        current = (
            st.session_state.translation_results.get(iss["segmentId"])
            if iss["segmentId"]
            else iss["targetText"]
        )
        default_value = (
            st.session_state.get(override_key)
            or current
            or iss["targetText"]
            or ""
        )
        cols[4].text_input(
            "target",
            value=default_value,
            key=edit_key,
            label_visibility="collapsed",
        )

        # Detail column — issue specifics + actionable fix
        with cols[5]:
            # ── Terminology issues (issueType=1, "No target term") ──
            #   Show: source term → expected target term, optional wrong
            #   inflected form, and a one-click Apply button.
            expected_term = iss.get("expectedTerm") or ""
            source_term = iss.get("sourceTerm") or ""
            potential_form = iss.get("potentialForm") or ""
            potential_base = iss.get("potentialBase") or ""
            forbidden_list = iss.get("forbiddenTerms") or []
            extra_targets = [t for t in (iss.get("targetTerms") or [])
                             if t and t != expected_term]

            is_terminology = bool(expected_term)

            if is_terminology:
                # Source → expected target
                if source_term:
                    st.markdown(
                        f"<code>{_escape_html(source_term)}</code> "
                        f"<span style='color:#16a34a;'>→</span> "
                        f"<b><code>{_escape_html(expected_term)}</code></b>",
                        unsafe_allow_html=True,
                    )
                else:
                    st.markdown(
                        f"❗ Required term: "
                        f"<b><code>{_escape_html(expected_term)}</code></b>",
                        unsafe_allow_html=True,
                    )

                # Potential wrong form info (Verifika found a related
                # form in the target but inflected/spelled wrong)
                if potential_form:
                    st.caption(
                        f"⚠️ Found in target: `{potential_form}` "
                        f"(should be `{potential_base or expected_term}`)"
                    )

                # Forbidden alternatives (if any)
                if forbidden_list:
                    st.caption(
                        "🚫 Forbidden: "
                        + " · ".join(
                            f"`{_escape_html(t)}`" for t in forbidden_list[:3]
                        )
                    )

                # Alternative target translations (if Verifika supplied
                # more than one acceptable form)
                if extra_targets:
                    st.caption(
                        "Alternatives: "
                        + " · ".join(
                            f"`{_escape_html(t)}`" for t in extra_targets[:3]
                        )
                    )

            else:
                # ── Non-terminology issues (Spelling, Common, etc.) ──
                # Offending word
                word = iss.get("offendingWord") or ""
                if word:
                    st.markdown(
                        f"❗ <code>{_escape_html(word)}</code>",
                        unsafe_allow_html=True,
                    )

                # Suggested fix from Verifika — surfaced as info only.
                # The user types the correction directly in the target
                # field above and confirms with Enter.
                sfix = iss.get("suggestedFix") or ""
                if sfix:
                    st.markdown(
                        f"💡 Suggested: <code>{_escape_html(sfix)}</code>",
                        unsafe_allow_html=True,
                    )

                # Other suggestions
                sugs = iss.get("suggestions") or []
                other = [s for s in sugs if s != sfix][:3]
                if other:
                    st.caption(
                        "Alternatives: "
                        + " · ".join(f"`{_escape_html(s)}`" for s in other)
                    )

            # Comment — common to both branches.
            # Ignored state is shown via the False-positive checkbox in cols[6].
            if iss.get("comment"):
                st.caption(f"💬 {iss['comment']}")

        # ── False-positive toggle (cols[6]) ─────────────────────────
        # Pre-checked if Verifika already has it marked as ignored.
        # Apply Corrections diffs this against the original isIgnored
        # value and pushes the change to Verifika via /api/QualityIssues/Ignore.
        ignore_key = f"verifika_ignore_{iss['id'] or idx}_{iss['segmentId']}"
        cols[6].checkbox(
            "False positive",
            value=bool(iss.get("isIgnored", False)),
            key=ignore_key,
            help="Mark this issue as a false positive. "
                 "Pushed to Verifika when you click Apply Corrections "
                 "(if 'Also push corrections to Verifika' is on).",
            label_visibility="collapsed",
        )


def _apply_range_fix(target: str, ranges: list, fix: str) -> str:
    """
    Replace the FIRST highlighted range in `target` with `fix`.
    Falls back to a simple substring replace if range info is unusable.
    """
    if not target or not ranges or not fix:
        return target
    try:
        r = next((x for x in ranges if isinstance(x, dict)), None)
        if not r:
            return target
        start = int(r.get("start", 0) or 0)
        length = int(r.get("length", 0) or 0)
        if length <= 0 or start < 0 or start + length > len(target):
            return target
        return target[:start] + fix + target[start + length:]
    except Exception:
        return target


def _render_apply_corrections(client: VerifikaQAClient):
    st.markdown("---")
    apply_col, dl_col = st.columns([1, 3])

    sync_to_verifika = st.checkbox(
        "Also push corrections to Verifika "
        "(POST /api/QualityIssues/updateTranslationUnits)",
        value=True,
        help="When checked, the same corrections will be sent to Verifika "
             "so the report tracks the changes.",
    )

    with apply_col:
        if st.button("✅ Apply Corrections",
                     type="primary", use_container_width=True):
            _apply_corrections(client, sync_to_verifika)

    if st.session_state.verifika_corrected_xliff:
        with dl_col:
            _proj_guid = st.session_state.get('memoq_selected_project_guid')
            _doc_guid = st.session_state.get('memoq_selected_document_guid')
            _proj_service = st.session_state.get('memoq_project_service')
            _filename = st.session_state.get(
                "last_xliff_filename"
            ) or "translated.mqxliff"

            if st.button(
                "🔄 Update translated file in memoQ",
                type="primary",
                use_container_width=True,
                disabled=not (_proj_guid and _doc_guid and _proj_service),
                key="update_translated_btn_verifika",
            ):
                with st.spinner("Pushing QA-corrected XLIFF to memoQ Server..."):
                    try:
                        new_version = _proj_service.update_bilingual(
                            _proj_guid,
                            _doc_guid,
                            st.session_state.verifika_corrected_xliff,
                            filename=_filename,
                        )
                        if new_version is not None:
                            st.success(f"✓ Updated in memoQ — new document version: {new_version}")
                        else:
                            st.success("✓ Updated in memoQ")
                    except Exception as e:
                        st.error(f"Failed to update in memoQ: {e}")


def _apply_corrections(client: VerifikaQAClient, sync_to_verifika: bool):
    issues = st.session_state.verifika_issues
    translations = st.session_state.translation_results
    applied = 0
    verifika_updates: List[Dict] = []
    ignore_now: List[str] = []      # issues to flip to ignored=true on Verifika
    unignore_now: List[str] = []    # issues to flip to ignored=false on Verifika

    for idx, iss in enumerate(issues):
        edit_key   = f"verifika_edit_{iss['id'] or idx}_{iss['segmentId']}"
        ignore_key = f"verifika_ignore_{iss['id'] or idx}_{iss['segmentId']}"

        # ── 1. Target text edits ──────────────────────────────────────────
        new_val = st.session_state.get(edit_key, "")
        if iss["segmentId"]:
            old_val = (translations.get(iss["segmentId"]) or "").strip()
            if new_val and new_val.strip() != old_val:
                translations[iss["segmentId"]] = new_val
                applied += 1
                tu_id = iss.get("translationUnitId")
                if sync_to_verifika and tu_id:
                    verifika_updates.append({
                        "id": tu_id,
                        "text": new_val,
                        "originalText": iss.get("originalTarget") or iss.get("targetText"),
                    })

        # ── 2. False-positive (ignore) toggle diff ────────────────────────
        was_ignored = bool(iss.get("isIgnored", False))
        is_ignored_now = bool(st.session_state.get(ignore_key, was_ignored))
        if is_ignored_now != was_ignored and iss.get("id"):
            if is_ignored_now:
                ignore_now.append(str(iss["id"]))
            else:
                unignore_now.append(str(iss["id"]))

    if not applied and not ignore_now and not unignore_now:
        st.info(
            "No changes detected. Edit a target cell or tick a False-positive box, "
            "then click Apply Corrections."
        )
        return

    st.session_state.translation_results = translations

    # ── Push edits to Verifika (best-effort) ─────────────────────────────
    # The recheck endpoint is project- and task-scoped, so we need both
    # verifika_project_id and verifika_task_id (captured from task_ready).
    if (sync_to_verifika and verifika_updates
            and st.session_state.verifika_project_id
            and st.session_state.verifika_task_id):
        try:
            n_pushed = client.update_translation_units(
                st.session_state.verifika_project_id,
                st.session_state.verifika_task_id,
                verifika_updates,
            )
            st.success(f"📤 Pushed {n_pushed} correction(s) to Verifika.")
        except VerifikaError as e:
            st.warning(f"Local apply succeeded but Verifika sync failed: {e}")
    elif sync_to_verifika and verifika_updates:
        st.warning(
            "Local apply succeeded but Verifika sync skipped — "
            "missing project_id or task_id in session state."
        )

    # ── Push ignore-state changes to Verifika (best-effort) ──────────────
    # ignore_issues uses project-scoped path /api/projects/{pid}/qualityIssues/ignore,
    # so pass verifika_project_id (NOT verifika_report_id).
    if sync_to_verifika and st.session_state.verifika_project_id and (ignore_now or unignore_now):
        try:
            if ignore_now:
                client.ignore_issues(
                    st.session_state.verifika_project_id, ignore_now, ignored=True
                )
            if unignore_now:
                client.ignore_issues(
                    st.session_state.verifika_project_id, unignore_now, ignored=False
                )
            id_to_ignored = {i: True for i in ignore_now}
            id_to_ignored.update({i: False for i in unignore_now})
            for it in issues:
                if it.get("id") in id_to_ignored:
                    it["isIgnored"] = id_to_ignored[it["id"]]
            parts = []
            if ignore_now:
                parts.append(f"{len(ignore_now)} marked as false positive")
            if unignore_now:
                parts.append(f"{len(unignore_now)} unmarked")
            st.success(f"🚫 Verifika ignore-state updated — {', '.join(parts)}.")
        except VerifikaError as e:
            st.warning(f"Verifika ignore-state sync failed: {e}")
    elif (ignore_now or unignore_now):
        id_to_ignored = {i: True for i in ignore_now}
        id_to_ignored.update({i: False for i in unignore_now})
        for it in issues:
            if it.get("id") in id_to_ignored:
                it["isIgnored"] = id_to_ignored[it["id"]]
        st.info(
            f"Marked {len(ignore_now)} as false positive locally "
            f"(not pushed — 'Also push to Verifika' is unchecked)."
        )

    # If only ignore-state changed (no text edits), skip XLIFF rebuild.
    if not applied:
        return

    # Rebuild XLIFF for local download
    seg_objs       = st.session_state.get("segment_objects", {})
    match_scores   = st.session_state.get("segment_match_scores", {})
    xliff_bytes    = st.session_state.get("last_xliff_bytes")

    if not xliff_bytes:
        st.warning(
            f"{applied} correction(s) saved to translation_results, "
            "but original XLIFF is not in session — re-upload it in Workspace tab."
        )
        return

    try:
        corrected = XMLParser.update_xliff(
            xliff_bytes, translations, seg_objs, match_scores=match_scores,
        )
        st.session_state.verifika_corrected_xliff = corrected
        st.success(
            f"✅ {applied} correction(s) applied. "
            "Use the download button on the right to get the corrected XLIFF."
        )
    except Exception as e:
        st.error(f"Failed to rebuild XLIFF: {e}")
