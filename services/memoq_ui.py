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
"""

from __future__ import annotations

import logging
from typing import List, Optional, Tuple

import streamlit as st

from services.memoq_server_client import MemoQServerClient

logger = logging.getLogger(__name__)


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
            label = (
                f"{p.get('Name', '(unnamed)')}  "
                f"[{p.get('SourceLanguageCode', '?')} -> "
                f"{', '.join(p.get('TargetLanguageCodes') or []) or '?'}]"
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

        # Project changed? reset doc list
        if proj_guid != st.session_state.memoq_selected_project_guid:
            st.session_state.memoq_selected_project_guid = proj_guid
            st.session_state.memoq_selected_project_name = selected_project.get("Name")
            st.session_state.memoq_documents_list = []
            st.session_state.memoq_selected_document_guid = None
            st.session_state.memoq_selected_document_name = None
            # Clear any previously fetched XLIFF bytes
            st.session_state.last_xliff_bytes = None
            st.session_state.last_xliff_filename = None

            # Auto-load documents and project resources
            with st.spinner("Loading documents and TM/TB assignments..."):
                try:
                    docs = proj_service.list_documents(proj_guid)
                    st.session_state.memoq_documents_list = docs
                except Exception as e:
                    st.error(f"Failed to list documents: {e}")
                    logger.error("list_documents error: %s", e, exc_info=True)

                try:
                    tm_guids, tb_guids = proj_service.get_project_resources(proj_guid)
                    st.session_state.selected_tm_guids = tm_guids
                    st.session_state.selected_tb_guids = tb_guids
                    st.caption(
                        f"Auto-selected {len(tm_guids)} TM(s) and {len(tb_guids)} TB(s) "
                        f"from project assignment"
                    )
                except Exception as e:
                    logger.warning("get_project_resources failed: %s", e)
                    st.session_state.selected_tm_guids = []
                    st.session_state.selected_tb_guids = []

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

        # Document changed? fetch its bilingual XLIFF
        if doc_guid != st.session_state.memoq_selected_document_guid:
            st.session_state.memoq_selected_document_guid = doc_guid
            st.session_state.memoq_selected_document_name = selected_doc.get("DocumentName")
            st.session_state.memoq_selected_target_lang = selected_doc.get("TargetLangCode")

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
        cols = st.columns(3)
        with cols[0]:
            st.metric("Project", selected_project.get("Name", "?"))
        with cols[1]:
            st.metric("Document", selected_doc.get("DocumentName", "?"))
        with cols[2]:
            st.metric(
                "TM/TB auto-selected",
                f"{len(st.session_state.get('selected_tm_guids') or [])}/"
                f"{len(st.session_state.get('selected_tb_guids') or [])}",
            )

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
