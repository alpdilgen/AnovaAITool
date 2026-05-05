# services/memoq_ui.py
"""
Streamlit UI Components for memoQ Server Integration
-----------------------------------------------------
Architectural changes (Version 1):
  * No more TM/TB multi-select - TMs and TBs are auto-selected from the
    project the user picks.
  * Workspace is driven by a project + document picker that talks to the
    memoQ Server WSAPI (SOAP).
  * Picking a document fetches its bilingual XLIFF and stores the bytes in
    `st.session_state.last_xliff_bytes` so the existing translation pipeline
    keeps working unchanged.

Version 3 additions:
  * Source language is filled into st.session_state.detected_languages as
    soon as a project is picked (from project metadata).
  * Target language is filled when a document is picked (from doc metadata).
  * The bilingual XLIFF (when it arrives) still overrides these via
    XMLParser.detect_languages() in app.py — that path stays as the source
    of truth, this just removes the "Upload a file to detect" placeholder
    moment from the sidebar.
"""

from __future__ import annotations

import logging
from typing import List, Optional, Tuple

import streamlit as st

from services.memoq_server_client import MemoQServerClient

logger = logging.getLogger(__name__)


def _normalise_lang(code: Optional[str]) -> Optional[str]:
    """Lowercase the language code, keep dialect (e.g. 'en-US' -> 'en-us')."""
    if not code:
        return None
    return str(code).strip().lower() or None


def _set_detected_lang(field: str, value: Optional[str]) -> None:
    """
    Write a single language slot into st.session_state.detected_languages
    without clobbering the other slot.
    """
    if not value:
        return
    current = st.session_state.get('detected_languages') or {}
    if not isinstance(current, dict):
        current = {}
    current[field] = value
    st.session_state.detected_languages = current


class MemoQUI:
    """Streamlit UI for memoQ Server Integration."""

    # ================================================================== #
    # Sidebar: connection settings (REST + SOAP share creds)              #
    # ================================================================== #

    @staticmethod
    def show_connection_settings() -> Optional[MemoQServerClient]:
        """
        Render memoQ Server connection form in the sidebar.
        Returns the connected REST MemoQServerClient or None.
        """
        with st.sidebar:
            st.divider()
            st.subheader("Server Connection")

            if 'memoq_server_url' not in st.session_state:
                st.session_state.memoq_server_url = "https://mirage.memoq.com:9091/adaturkey"
            if 'memoq_username' not in st.session_state:
                st.session_state.memoq_username = "AnovaAI"
            if 'memoq_password' not in st.session_state:
                st.session_state.memoq_password = ""
            if 'memoq_api_key' not in st.session_state:
                st.session_state.memoq_api_key = ""
            if 'memoq_verify_ssl' not in st.session_state:
                st.session_state.memoq_verify_ssl = False

            with st.form("memoq_connection"):
                server_url = st.text_input(
                    "Server URL",
                    value=st.session_state.memoq_server_url,
                    help="memoQ Server base URL (e.g. https://host:9091/adaturkey)"
                )
                username = st.text_input(
                    "Username",
                    value=st.session_state.memoq_username,
                    help="memoQ username (REST)"
                )
                password = st.text_input(
                    "Password",
                    type="password",
                    value=st.session_state.memoq_password,
                    help="memoQ password (REST)"
                )
                api_key = st.text_input(
                    "WSAPI API key",
                    type="password",
                    value=st.session_state.memoq_api_key,
                    help="API key for SOAP/WSAPI (project list, bilingual export/import)"
                )
                verify_ssl = st.checkbox(
                    "Verify SSL",
                    value=st.session_state.memoq_verify_ssl,
                    help="Uncheck for self-signed certificates"
                )
                submitted = st.form_submit_button("Connect", width="stretch")

                if submitted:
                    st.session_state.memoq_server_url = server_url
                    st.session_state.memoq_username = username
                    st.session_state.memoq_password = password
                    st.session_state.memoq_api_key = api_key
                    st.session_state.memoq_verify_ssl = verify_ssl
                    st.session_state.memoq_connected = False
                    st.session_state.memoq_client = None
                    st.session_state.memoq_project_service = None

            if st.session_state.get('memoq_connected') and st.session_state.get('memoq_client'):
                st.success("Connected to memoQ Server")
                if st.button("Disconnect", width="stretch"):
                    st.session_state.memoq_connected = False
                    st.session_state.memoq_client = None
                    st.session_state.memoq_project_service = None
                    st.session_state.memoq_projects_list = []
                    st.session_state.memoq_documents_list = []
                    st.rerun()
                return st.session_state.memoq_client

            return None

    # ================================================================== #
    # Workspace: project + document picker                                #
    # ================================================================== #

    @staticmethod
    def show_project_picker() -> Tuple[Optional[str], Optional[dict]]:
        """
        Render the memoQ Server project + document picker.

        Returns (selected_project_guid, selected_document_dict_or_None).
        On document selection, fetches the bilingual XLIFF and stores it in
        st.session_state.last_xliff_bytes (and last_xliff_filename).

        Side effects on st.session_state.detected_languages:
          - 'source' is set the moment a project is chosen (project metadata)
          - 'target' is set when a document row is chosen (doc metadata)
        """
        # Ensure state keys exist
        for k, v in {
            'memoq_projects_list': [],
            'memoq_selected_project_guid': None,
            'memoq_selected_project_name': None,
            'memoq_documents_list': [],
            'memoq_selected_document_guid': None,
            'memoq_selected_document_name': None,
            'memoq_selected_target_lang': None,
            'project_search_filter': "",
            'document_search_filter': "",
            'available_tms': [],
            'available_tbs': [],
            'selected_tm_names': [],
            'selected_tb_names': [],
        }.items():
            if k not in st.session_state:
                st.session_state[k] = v

        proj_service = st.session_state.get('memoq_project_service')
        if proj_service is None:
            st.warning("Not connected to memoQ Server. Configure connection in sidebar.")
            return None, None

        # ---- Load projects button -------------------------------------
        col1, col2 = st.columns([1, 4])
        with col1:
            load_projects = st.button(
                "Load memoQ resources",
                width="stretch",
                help="Fetch the list of projects from memoQ Server"
            )
        with col2:
            if st.session_state.memoq_projects_list:
                st.success(
                    f"{len(st.session_state.memoq_projects_list)} project(s) loaded"
                )

        if load_projects:
            with st.spinner("Loading projects from memoQ Server..."):
                try:
                    projects = proj_service.list_projects(only_active=True)
                    st.session_state.memoq_projects_list = projects
                    # Reset downstream selections
                    st.session_state.memoq_selected_project_guid = None
                    st.session_state.memoq_documents_list = []
                    st.session_state.memoq_selected_document_guid = None
                    st.rerun()
                except Exception as e:
                    st.error(f"Failed to list projects: {e}")
                    logger.error("list_projects error: %s", e, exc_info=True)
                    return None, None

        if not st.session_state.memoq_projects_list:
            st.info("Click 'Load memoQ resources' to fetch your server projects.")
            return None, None

        # ---- Project picker -------------------------------------------
        st.markdown("**Project**")
        proj_search = st.text_input(
            "Filter projects",
            value=st.session_state.project_search_filter,
            placeholder="Type to filter projects by name...",
            key="proj_search_input",
            label_visibility="collapsed",
        )
        st.session_state.project_search_filter = proj_search

        proj_options = {}
        for p in st.session_state.memoq_projects_list:
            tgt_codes = p.get('TargetLanguageCodes') or []
            tgt_str = ', '.join(tgt_codes) if tgt_codes else '?'
            label = (
                f"{p.get('Name', '(unnamed)')}  "
                f"[{p.get('SourceLanguageCode', '?')} -> {tgt_str}]"
            )
            proj_options[label] = p

        filtered_proj_labels = [
            lbl for lbl in proj_options
            if proj_search.lower() in lbl.lower()
        ]

        # Pre-select previously chosen project (if still in filter)
        default_idx = 0
        if st.session_state.memoq_selected_project_name:
            for i, lbl in enumerate(filtered_proj_labels):
                if proj_options[lbl].get("Name") == st.session_state.memoq_selected_project_name:
                    default_idx = i
                    break

        selected_proj_label = st.selectbox(
            "Select project",
            options=filtered_proj_labels,
            index=default_idx if filtered_proj_labels else None,
            key="proj_selectbox",
            label_visibility="collapsed",
        )

        if not selected_proj_label:
            return None, None

        selected_project = proj_options[selected_proj_label]
        proj_guid = selected_project.get("ServerProjectGuid")

        # Project changed? reset doc list + push source language to sidebar state
        if proj_guid != st.session_state.memoq_selected_project_guid:
            st.session_state.memoq_selected_project_guid = proj_guid
            st.session_state.memoq_selected_project_name = selected_project.get("Name")
            st.session_state.memoq_documents_list = []
            st.session_state.memoq_selected_document_guid = None
            st.session_state.memoq_selected_document_name = None
            st.session_state.available_tms = []
            st.session_state.available_tbs = []
            st.session_state.selected_tm_names = []
            st.session_state.selected_tb_names = []
            # Clear any previously fetched XLIFF bytes
            st.session_state.last_xliff_bytes = None
            st.session_state.last_xliff_filename = None

            # ---- Push project's SOURCE language to sidebar state ----
            src_lang = _normalise_lang(selected_project.get("SourceLanguageCode"))
            if src_lang:
                _set_detected_lang('source', src_lang)
            # If project has exactly one target lang, push that too as a hint
            tgt_codes = selected_project.get("TargetLanguageCodes") or []
            if len(tgt_codes) == 1:
                tgt_lang = _normalise_lang(tgt_codes[0])
                if tgt_lang:
                    _set_detected_lang('target', tgt_lang)

            # Resolve target langs once so list_documents can iterate per-language
            target_lang_codes = [
                _normalise_lang(c) for c in (tgt_codes or []) if c
            ]
            target_lang_codes = [c for c in target_lang_codes if c]

            # Auto-load documents and project resources
            with st.spinner("Loading documents and TM/TB assignments..."):
                try:
                    try:
                        docs = proj_service.list_documents(
                            proj_guid, target_lang_codes=target_lang_codes or None
                        )
                    except TypeError:
                        docs = proj_service.list_documents(proj_guid)
                    st.session_state.memoq_documents_list = docs
                except Exception as e:
                    st.error(f"Failed to list documents: {e}")
                    logger.error("list_documents error: %s", e, exc_info=True)

                try:
                    rest_client = st.session_state.get('memoq_client')
                    tgt_lang_for_lookup = target_lang_codes[0] if target_lang_codes else None

                    if rest_client and src_lang:
                        # Fetch all TMs and filter client-side by base language code.
                        # Avoids REST API 400 errors from language code format mismatches
                        # (e.g. eng-us vs eng-US) that occur when passing as query params.
                        def _base(code):
                            return str(code or '').lower().split('-')[0]

                        src_base = _base(src_lang)
                        tgt_base = _base(tgt_lang_for_lookup) if tgt_lang_for_lookup else None

                        all_tms = rest_client.list_tms()
                        matching_tms = []
                        for tm in (all_tms or []):
                            tm_src = _base(tm.get('SourceLangCode') or '')
                            tm_tgt = _base(tm.get('TargetLangCode') or '')
                            if tm_src == src_base and (tgt_base is None or tm_tgt == tgt_base):
                                guid = tm.get('TMGuid') or tm.get('tmGuid')
                                if guid:
                                    friendly = (
                                        tm.get('FriendlyName') or tm.get('Name')
                                        or tm.get('name') or str(guid)[:8]
                                    )
                                    src_code = (tm.get('SourceLangCode') or '?').upper()
                                    tgt_code = (tm.get('TargetLangCode') or '?').upper()
                                    entries = tm.get('NumEntries', 0)
                                    matching_tms.append({
                                        'guid': str(guid),
                                        'label': f"{friendly} ({src_code}-{tgt_code}, {entries} entries)",
                                    })

                        all_tbs = rest_client.list_tbs()
                        matching_tbs = []
                        for tb in (all_tbs or []):
                            tb_lang_bases = [_base(l) for l in (tb.get('Languages') or [])]
                            if src_base in tb_lang_bases and (
                                tgt_base is None or tgt_base in tb_lang_bases
                            ):
                                guid = tb.get('TBGuid') or tb.get('tbGuid')
                                if guid:
                                    friendly = (
                                        tb.get('FriendlyName') or tb.get('Name')
                                        or tb.get('name') or str(guid)[:8]
                                    )
                                    langs = ', '.join(tb.get('Languages') or [])
                                    entries = tb.get('NumEntries', 0)
                                    matching_tbs.append({
                                        'guid': str(guid),
                                        'label': f"{friendly} ({langs}, {entries} terms)",
                                    })

                        st.session_state.available_tms = matching_tms
                        st.session_state.available_tbs = matching_tbs
                        st.session_state.selected_tm_names = []
                        st.session_state.selected_tb_names = []
                        st.session_state.selected_tm_guids = []
                        st.session_state.selected_tb_guids = []
                    else:
                        # Fallback: SOAP project-assigned resources (no names available)
                        tm_guids, tb_guids = proj_service.get_project_resources(proj_guid)
                        st.session_state.available_tms = [{'guid': g, 'label': g[:8]} for g in tm_guids]
                        st.session_state.available_tbs = [{'guid': g, 'label': g[:8]} for g in tb_guids]
                        st.session_state.selected_tm_guids = []
                        st.session_state.selected_tb_guids = []
                except Exception as e:
                    logger.warning("TM/TB lookup failed: %s", e)
                    st.session_state.selected_tm_guids = []
                    st.session_state.selected_tb_guids = []
            # Force a rerun so the document picker + language sidebar refresh
            # immediately on the same click instead of needing a second Enter.
            st.rerun()

        # ---- Document picker ------------------------------------------
        if not st.session_state.memoq_documents_list:
            st.info("This project has no translation documents.")
            return proj_guid, None

        st.markdown("**Document**")
        doc_search = st.text_input(
            "Filter documents",
            value=st.session_state.document_search_filter,
            placeholder="Type to filter documents...",
            key="doc_search_input",
            label_visibility="collapsed",
        )
        st.session_state.document_search_filter = doc_search

        doc_options = {}
        for d in st.session_state.memoq_documents_list:
            confirmed = d.get("ConfirmedRowCount") or 0
            total = d.get("TotalRowCount") or 0
            label = (
                f"{d.get('DocumentName', '(unnamed)')}  "
                f"[{d.get('TargetLangCode', '?')}]  "
                f"v{d.get('Version') or '?'}  "
                f"{confirmed}/{total}"
            )
            doc_options[label] = d

        filtered_doc_labels = [
            lbl for lbl in doc_options
            if doc_search.lower() in lbl.lower()
        ]

        default_doc_idx = 0
        if st.session_state.memoq_selected_document_name:
            for i, lbl in enumerate(filtered_doc_labels):
                if doc_options[lbl].get("DocumentName") == st.session_state.memoq_selected_document_name:
                    default_doc_idx = i
                    break

        selected_doc_label = st.selectbox(
            "Select document",
            options=filtered_doc_labels,
            index=default_doc_idx if filtered_doc_labels else None,
            key="doc_selectbox",
            label_visibility="collapsed",
        )

        if not selected_doc_label:
            return proj_guid, None

        selected_doc = doc_options[selected_doc_label]
        doc_guid = selected_doc.get("DocumentGuid")

        # Document changed? fetch its bilingual XLIFF + push target lang
        if doc_guid != st.session_state.memoq_selected_document_guid:
            st.session_state.memoq_selected_document_guid = doc_guid
            st.session_state.memoq_selected_document_name = selected_doc.get("DocumentName")
            doc_target = _normalise_lang(selected_doc.get("TargetLangCode"))
            st.session_state.memoq_selected_target_lang = doc_target

            # Push the document's target language to sidebar state
            if doc_target:
                _set_detected_lang('target', doc_target)

            # Re-affirm project source as well (in case state was cleared)
            src_lang = _normalise_lang(selected_project.get("SourceLanguageCode"))
            if src_lang:
                _set_detected_lang('source', src_lang)

            with st.spinner(f"Exporting bilingual XLIFF for {selected_doc.get('DocumentName')}..."):
                try:
                    xliff_bytes, suggested_name = proj_service.export_bilingual(
                        proj_guid, doc_guid, include_skeleton=True
                    )
                    st.session_state.last_xliff_bytes = xliff_bytes
                    st.session_state.last_xliff_filename = suggested_name or selected_doc.get("DocumentName")
                    st.success(
                        f"Loaded {selected_doc.get('DocumentName')} "
                        f"({len(xliff_bytes):,} bytes)"
                    )
                except Exception as e:
                    st.error(f"Failed to export bilingual: {e}")
                    logger.error("export_bilingual error: %s", e, exc_info=True)
                    st.session_state.last_xliff_bytes = None

        # ---- Show summary ---------------------------------------------
        cols = st.columns(2)
        with cols[0]:
            st.metric("Project", selected_project.get("Name", "?"))
        with cols[1]:
            st.metric("Document", selected_doc.get("DocumentName", "?"))

        # ---- TM/TB selection -----------------------------------------
        available_tms = st.session_state.get('available_tms') or []
        available_tbs = st.session_state.get('available_tbs') or []

        if available_tms:
            tm_options = {t['label']: t['guid'] for t in available_tms}
            selected_tm_labels = st.multiselect(
                f"Translation Memories ({len(available_tms)} matching)",
                options=list(tm_options.keys()),
                default=st.session_state.get('selected_tm_names') or [],
                key="tm_multiselect",
            )
            st.session_state.selected_tm_names = selected_tm_labels
            st.session_state.selected_tm_guids = [tm_options[n] for n in selected_tm_labels]
        else:
            st.caption("No TMs found for this language pair.")

        if available_tbs:
            tb_options = {t['label']: t['guid'] for t in available_tbs}
            selected_tb_labels = st.multiselect(
                f"Termbases ({len(available_tbs)} matching)",
                options=list(tb_options.keys()),
                default=st.session_state.get('selected_tb_names') or [],
                key="tb_multiselect",
            )
            st.session_state.selected_tb_names = selected_tb_labels
            st.session_state.selected_tb_guids = [tb_options[n] for n in selected_tb_labels]
        else:
            st.caption("No TBs found for this language pair.")

        return proj_guid, selected_doc

    # ================================================================== #
    # Compatibility shim - old data loader (kept so callers don't break)  #
    # ================================================================== #

    @staticmethod
    def show_memoq_data_loader(
        client: MemoQServerClient,
        src_lang: str,
        tgt_lang: str,
    ) -> Tuple[List[str], List[str]]:
        """
        Deprecated. Returns the auto-selected TM/TB GUIDs from the current
        project (set by show_project_picker). Kept so any leftover callers
        keep working.
        """
        return (
            list(st.session_state.get('selected_tm_guids') or []),
            list(st.session_state.get('selected_tb_guids') or []),
        )

    # ------------------------------------------------------------------ #
    # Helper                                                              #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _get_memoq_lang_code(lang_code: str) -> str:
        """Convert any language code format to memoQ 3-letter code."""
        from config import ISO_TO_MEMOQ_LANG
        if not lang_code:
            return lang_code
        code = lang_code.lower().strip()
        base = code.split('-')[0]
        if len(base) == 3:
            return lang_code
        three = ISO_TO_MEMOQ_LANG.get(base, base)
        if '-' in code:
            locale = code.split('-', 1)[1].upper()
            return f"{three}-{locale}"
        return three
