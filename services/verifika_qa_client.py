"""
Verifika QA API Client (v3 — report-based workflow)

Built from a real HAR capture, the public Postman docs, and live tests
against the Verifika beta server. The actual chain the API exposes is:

    POST /api/Projects              {name, qaSettingsId}      → projectId
    POST /api/ProjectFiles/UploadChunkFile (multipart, repeat)
    POST /api/ProjectFiles/CommitFile  {projectId, fileId,
                                        fileName, indices}     → file metadata
    POST /api/Reports                {projectId, userId}        → reportId
    POST /api/Reports/{reportId}/Generate  {id: reportId}      → 202 Accepted
    GET  /api/QualityIssues?reportId={reportId}                 → polling
            response: {qualityIssues:[], statuses:[
                          {issueType, status}, ... ]}
            QA done ⇔ every statuses[].status == 1
    GET  /api/QualityIssues?reportId={reportId}                 → final fetch
    POST /api/QualityIssues/updateTranslationUnits
        body: {reportId, translationUnits:[{id, target}]}      → apply edits

User identity:
    Decode JWT 'sub' claim (no extra HTTP). Fallback /api/Users/current.

Issue normalisation:
    Verifika returns issues with `translationUnit.properties.id` which
    is the original XLIFF `<trans-unit id=...>` value. We surface that
    as `segmentId` so the rest of the app (which keys on XLIFF segment
    ids) can map back to the source XLIFF without a separate query.

This client is UI-agnostic (no streamlit imports).
"""

from __future__ import annotations

import base64
import io
import json
import logging
import time
import uuid
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, List, Optional, Tuple

import requests

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Defaults
# ─────────────────────────────────────────────────────────────────────────────

DEFAULT_BASE_URL = "https://beta.e-verifika.com"
DEFAULT_API_VERSION = "1.0"
DEFAULT_CHUNK_SIZE = 5 * 1024 * 1024     # 5 MB
DEFAULT_POLL_INTERVAL = 3                # seconds
DEFAULT_POLL_TIMEOUT = 600               # 10 minutes

# issueType code → human label.
# Verified against live test responses + Web UI tabs: 'Common' contains
# format/punctuation/untranslatable/etc. checks, while 'Spelling' is a
# distinct category. Where the per-issue `issueKind` field is
# available (which is most of the time), we prefer that — it's the
# server's own description (e.g. "Invalid format of number range",
# "Spelling error", "No untranslatable in target").
ISSUE_TYPE_LABELS = {
    0: "Common",            # number/format/apostrophe/spacing/untranslatables
    1: "Terminology",
    2: "Consistency",
    3: "Spelling",
    4: "User-defined",
    5: "Grammar",
}


class VerifikaError(Exception):
    """Raised for any Verifika API failure."""
    def __init__(self, message: str,
                 status_code: Optional[int] = None,
                 response_body: Optional[str] = None):
        super().__init__(message)
        self.status_code = status_code
        self.response_body = response_body


# ─────────────────────────────────────────────────────────────────────────────
# Client
# ─────────────────────────────────────────────────────────────────────────────

class VerifikaQAClient:
    """REST client for the Verifika QA API."""

    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        api_token: Optional[str] = None,
        username: Optional[str] = None,
        password: Optional[str] = None,
        api_version: str = DEFAULT_API_VERSION,
        verify_ssl: bool = True,
        timeout: int = 60,
    ):
        self.base_url = base_url.rstrip("/")
        self.api_version = api_version
        self.verify_ssl = verify_ssl
        self.timeout = timeout

        self._token: Optional[str] = api_token
        self._token_expiry: Optional[datetime] = None
        self._username = username
        self._password = password
        self._cached_user_id: Optional[str] = None

        self._session = requests.Session()

    # ── Auth ────────────────────────────────────────────────────────────────

    def login(self) -> None:
        if self._token and not self._username:
            return
        if not (self._username and self._password):
            raise VerifikaError(
                "Cannot login: no api_token and no username/password"
            )
        url = f"{self.base_url}/api/auth/login"
        resp = self._session.post(
            url,
            json={"username": self._username, "password": self._password},
            headers={"Accept": "application/json", "Content-Type": "application/json"},
            params={"api-version": self.api_version},
            timeout=self.timeout, verify=self.verify_ssl,
        )
        if resp.status_code >= 400:
            raise VerifikaError(
                f"Login failed: HTTP {resp.status_code}",
                status_code=resp.status_code, response_body=resp.text)
        data = resp.json()
        self._token = (data.get("token") or data.get("accessToken")
                       or data.get("access_token"))
        if not self._token:
            raise VerifikaError(f"Login response missing token: {data}")
        expires_in = int(data.get("expiresIn") or data.get("expires_in") or 3600)
        self._token_expiry = datetime.utcnow() + timedelta(seconds=expires_in - 60)

    def _ensure_auth(self) -> None:
        if not self._token:
            self.login(); return
        if self._token_expiry and datetime.utcnow() >= self._token_expiry:
            self._token = None
            self.login()

    # ── User identity ──────────────────────────────────────────────────────

    def get_current_user_id(self) -> str:
        """
        Return the current user's GUID. Decodes the JWT 'sub' claim
        from the Bearer token (no HTTP call). Falls back to
        /api/Users/current if decoding fails.
        """
        if self._cached_user_id:
            return self._cached_user_id

        # JWT decode
        if self._token and self._token.count(".") == 2:
            try:
                _, payload_b64, _ = self._token.split(".")
                pad = "=" * (-len(payload_b64) % 4)
                payload = json.loads(base64.urlsafe_b64decode(payload_b64 + pad))
                uid = (payload.get("sub") or payload.get("userId")
                       or payload.get("id"))
                if uid:
                    self._cached_user_id = uid
                    return uid
            except Exception as e:
                logger.debug("JWT decode failed: %s", e)

        # Fallback
        try:
            data = self._request("GET", "/api/Users/current")
            uid = data.get("id") or data.get("Id")
            if uid:
                self._cached_user_id = uid
                return uid
        except VerifikaError:
            pass

        raise VerifikaError(
            "Could not determine current user GUID — "
            "JWT 'sub' claim missing and /api/Users/current failed"
        )

    def get_current_user(self) -> Dict:
        return self._request("GET", "/api/Users/current")

    # ── HTTP plumbing ───────────────────────────────────────────────────────

    def _headers(self, extra: Optional[Dict] = None) -> Dict[str, str]:
        h = {
            "Accept": "application/json",
            "Authorization": f"Bearer {self._token}" if self._token else "",
        }
        if extra:
            h.update(extra)
        return h

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: Optional[Dict] = None,
        json_body: Any = None,
        data: Optional[bytes] = None,
        headers: Optional[Dict] = None,
        api_version_override: Optional[str] = None,
        accept_status: Optional[set] = None,
    ):
        """JSON request. json_body may be dict OR list."""
        self._ensure_auth()
        url = f"{self.base_url}{path}"
        merged_params = {
            "api-version": api_version_override or self.api_version,
            **(params or {}),
        }
        merged_headers = self._headers(headers)
        accept_status = accept_status or set()

        for attempt in (1, 2):
            resp = self._session.request(
                method, url,
                params=merged_params,
                json=json_body if data is None else None,
                data=data,
                headers=merged_headers,
                timeout=self.timeout,
                verify=self.verify_ssl,
            )

            if resp.status_code == 401 and attempt == 1 and self._username:
                self._token = None
                self._ensure_auth()
                merged_headers["Authorization"] = f"Bearer {self._token}"
                continue

            if resp.status_code >= 400 and resp.status_code not in accept_status:
                raise VerifikaError(
                    f"{method} {path} failed: HTTP {resp.status_code}",
                    status_code=resp.status_code,
                    response_body=resp.text[:1500],
                )

            ctype = resp.headers.get("Content-Type", "")
            if "json" in ctype:
                if not resp.text:
                    return None
                try:
                    return resp.json()
                except Exception:
                    return resp.text
            return resp.content if resp.content else None

        raise VerifikaError(f"{method} {path}: exhausted retries")

    def _request_multipart(
        self,
        method: str,
        path: str,
        *,
        files: Dict,
        data_fields: Optional[Dict] = None,
        params: Optional[Dict] = None,
    ):
        """Multipart/form-data variant for chunk uploads."""
        self._ensure_auth()
        url = f"{self.base_url}{path}"
        merged_params = {"api-version": self.api_version, **(params or {})}
        merged_headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {self._token}" if self._token else "",
        }

        for attempt in (1, 2):
            resp = self._session.request(
                method, url,
                params=merged_params,
                files=files,
                data=data_fields or {},
                headers=merged_headers,
                timeout=self.timeout,
                verify=self.verify_ssl,
            )
            if resp.status_code == 401 and attempt == 1 and self._username:
                self._token = None
                self._ensure_auth()
                merged_headers["Authorization"] = f"Bearer {self._token}"
                continue
            if resp.status_code >= 400:
                raise VerifikaError(
                    f"{method} {path} failed: HTTP {resp.status_code}",
                    status_code=resp.status_code,
                    response_body=resp.text[:1500],
                )
            ctype = resp.headers.get("Content-Type", "")
            if "json" in ctype:
                if not resp.text:
                    return None
                return resp.json()
            return resp.text or resp.content

        raise VerifikaError(f"{method} {path}: exhausted retries (multipart)")

    # ── QA settings (profiles) ─────────────────────────────────────────────

    def list_qa_settings(self) -> List[Dict]:
        result = self._request("GET", "/api/QASettings",
                               api_version_override="1.1")
        if isinstance(result, list):
            return result
        if isinstance(result, dict):
            for k in ("value", "items", "results", "data"):
                if isinstance(result.get(k), list):
                    return result[k]
        return []

    # ── Project lifecycle ───────────────────────────────────────────────────

    def create_project(
        self,
        name: str,
        qa_settings_id: Optional[str] = None,
        source_lang: Optional[str] = None,
        target_lang: Optional[str] = None,
    ) -> Dict:
        body: Dict = {"name": name}
        if qa_settings_id:
            body["qaSettingsId"] = qa_settings_id
        if source_lang:
            body["sourceLanguage"] = source_lang
        if target_lang:
            body["targetLanguage"] = target_lang
        return self._request("POST", "/api/Projects", json_body=body)

    def get_project(self, project_id: str) -> Dict:
        return self._request("GET", f"/api/Projects/{project_id}")

    def list_project_files(self, project_id: str) -> List[Dict]:
        result = self._request(
            "GET", f"/api/projects/{project_id}/projectFiles"
        )
        return result if isinstance(result, list) else []


    # ── Task chain (the real QA trigger) ───────────────────────────────────

    def start_project(
        self,
        project_id: str,
        assigned_to_id: Optional[str] = None,
        all_files: bool = True,
    ) -> Dict:
        """
        `POST /api/Projects/{pid}/start` — what the Web UI's "Start QA"
        button calls. Creates a task assigned to the given user.

        This is the entry point that wires the report's QA pipeline to
        an executable task. Without this call, /tasks/{id}/check has no
        task to act on, and the QualityIssues statuses stay at 0.
        """
        uid = assigned_to_id or self.get_current_user_id()
        body = {
            "assignments": [
                {"allFiles": all_files, "assignedToId": uid}
            ]
        }
        return self._request(
            "POST", f"/api/Projects/{project_id}/start", json_body=body
        )

    def list_tasks(self, project_id: str) -> List[Dict]:
        result = self._request("GET", f"/api/projects/{project_id}/tasks")
        return result if isinstance(result, list) else []

    def accept_tasks(self, project_id: str) -> None:
        """`POST /api/projects/{pid}/tasks/accept` (no body)."""
        self._request(
            "POST", f"/api/projects/{project_id}/tasks/accept",
            json_body={}, accept_status={200, 202, 204},
        )

    def check_task(self, project_id: str, task_id: str) -> None:
        """
        `POST /api/projects/{pid}/tasks/{tid}/check` (no body).

        ⭐ This is the call that actually starts the QA analysis on
        the server. /api/Reports/{id}/Generate alone returns 202 but
        does not move the statuses; only after /tasks/{tid}/check
        does the server start producing issues and flipping statuses
        to 1.
        """
        self._request(
            "POST", f"/api/projects/{project_id}/tasks/{task_id}/check",
            json_body={}, accept_status={200, 202, 204},
        )

    # ── File upload ─────────────────────────────────────────────────────────

    def upload_file(
        self,
        project_id: str,
        file_bytes: bytes,
        file_name: str,
        chunk_size: int = DEFAULT_CHUNK_SIZE,
        progress_cb: Optional[Callable[[int, int], None]] = None,
    ) -> Dict:
        """
        Chunked multipart upload + commit. Real schema (camelCase):

            UploadChunkFile (multipart):
                File:      blob
                Index:     int (per-chunk)
                FileId:    uuid (shared across chunks)
                FileName:  str
                ProjectId: uuid

            CommitFile (JSON):
                {projectId, fileId, fileName, indices: "0,1,2"}
            Response: full file metadata dict (id is the file's
            verifika-side uuid).
        """
        total = len(file_bytes)
        if total == 0:
            raise VerifikaError("Cannot upload empty file")

        file_id = str(uuid.uuid4())
        uploaded = 0
        chunk_idx = 0
        uploaded_indices: List[int] = []

        with io.BytesIO(file_bytes) as buf:
            while uploaded < total:
                chunk = buf.read(chunk_size)
                if not chunk:
                    break
                self._request_multipart(
                    "POST", "/api/ProjectFiles/UploadChunkFile",
                    files={"File": (file_name, chunk, "application/octet-stream")},
                    data_fields={
                        "Index":     str(chunk_idx),
                        "FileId":    file_id,
                        "FileName":  file_name,
                        "ProjectId": project_id,
                    },
                )
                uploaded_indices.append(chunk_idx)
                uploaded += len(chunk)
                chunk_idx += 1
                if progress_cb:
                    progress_cb(uploaded, total)

        commit = self._request(
            "POST", "/api/ProjectFiles/CommitFile",
            json_body={
                "projectId": project_id,
                "fileId":    file_id,
                "fileName":  file_name,
                "indices":   ",".join(str(i) for i in uploaded_indices),
            },
        ) or {}
        logger.info("Verifika: committed file %s (%d bytes, %d chunks, fileId=%s)",
                    file_name, total, chunk_idx, file_id)
        return {"fileId": file_id, "totalChunks": chunk_idx, "commit": commit}

    # ── Reports (the QA driver) ────────────────────────────────────────────

    def create_report(self, project_id: str,
                      user_id: Optional[str] = None) -> str:
        """
        `POST /api/Reports` — get-or-create a report for this project.
        Idempotent: returns the existing report's id if one already exists.

        Body: {projectId, userId}
        Response: {id, projectId, isOwner, createdOn}
        """
        uid = user_id or self.get_current_user_id()
        result = self._request(
            "POST", "/api/Reports",
            json_body={"projectId": project_id, "userId": uid},
        )
        if not isinstance(result, dict):
            raise VerifikaError(f"create_report response not a dict: {result}")
        rid = result.get("id") or result.get("Id")
        if not rid:
            raise VerifikaError(f"create_report response missing id: {result}")
        return rid

    def get_report_by_project(self, project_id: str) -> Optional[Dict]:
        """`GET /api/Reports?projectId=X`."""
        try:
            return self._request("GET", "/api/Reports",
                                 params={"projectId": project_id})
        except VerifikaError:
            return None

    def run_report(self, report_id: str) -> None:
        """
        `POST /api/Reports/{id}/Generate` — kick off the QA analysis.
        Returns 202 Accepted (no body). Server queues the work; poll
        /api/QualityIssues?reportId=... afterwards.
        """
        self._request(
            "POST", f"/api/Reports/{report_id}/Generate",
            json_body={"id": report_id},
            accept_status={202},
        )

    def generate_report_link(self, report_id: str) -> Optional[str]:
        """
        `POST /api/Reports/{id}/GenerateLink` — historically empty in
        our beta tests, kept for completeness / future use.
        """
        result = self._request(
            "POST", f"/api/Reports/{report_id}/GenerateLink",
            accept_status={200, 202, 204},
        )
        if isinstance(result, dict):
            for k in ("link", "url", "embedUrl", "shareUrl"):
                if result.get(k):
                    return result[k]
        return None

    # ── Quality issues ──────────────────────────────────────────────────────

    def get_quality_issues_payload(self, project_id: str) -> Dict:
        """
        Raw `GET /api/QualityIssues?projectId=X` response. Shape:
            {
              "qualityIssues": [...],
              "statuses": [{"issueType":N, "status":0|1}, ...]
            }

        IMPORTANT: We query with projectId, NOT reportId. The server
        side runs the actual QA against an automatically-created
        report whose id we cannot retrieve via POST /api/Reports
        (that endpoint creates a separate placeholder report that
        always stays empty). projectId is the only reliable key —
        verified live: the same response that comes back to the Web
        UI is what we get here.

        Each issue in the response carries its own `reportId` field
        (the *real* report id), which is what we use for downstream
        actions like update_translation_units and ignore_issues.
        """
        result = self._request(
            "GET", "/api/QualityIssues",
            params={"projectId": project_id},
        )
        if isinstance(result, dict):
            return result
        return {"qualityIssues": [], "statuses": []}

    def get_quality_issues(self, project_id: str) -> List[Dict]:
        """Normalised flat list of issue dicts."""
        payload = self.get_quality_issues_payload(project_id)
        raw = payload.get("qualityIssues") or []
        return [self._normalise_issue(it) for it in raw if isinstance(it, dict)]

    def wait_for_qa_completion(
        self,
        project_id: str,
        poll_interval: int = DEFAULT_POLL_INTERVAL,
        timeout: int = DEFAULT_POLL_TIMEOUT,
        progress_cb: Optional[Callable[[Dict], None]] = None,
    ) -> Dict:
        """
        Poll /api/QualityIssues?projectId=X until every category's
        status == 1. Returns the final payload.

        Note: we key on projectId, not reportId, because the report
        the server runs the QA against is internally managed and not
        the same object as the one our POST /api/Reports returns.
        See get_quality_issues_payload for the full story.
        """
        deadline = time.time() + timeout
        last: Dict = {}
        while time.time() < deadline:
            try:
                payload = self.get_quality_issues_payload(project_id)
            except VerifikaError as e:
                logger.warning("Polling fetch failed: %s", e)
                time.sleep(poll_interval)
                continue

            last = payload
            statuses = payload.get("statuses") or []
            if progress_cb:
                try: progress_cb(payload)
                except Exception: pass

            if statuses and all(int(s.get("status", 0) or 0) == 1
                                for s in statuses):
                logger.info("Verifika: all %d issue-type statuses ready",
                            len(statuses))
                return payload

            time.sleep(poll_interval)

        raise VerifikaError(
            f"QA for project {project_id} not ready after {timeout}s "
            f"(last statuses: {last.get('statuses')})"
        )

    @staticmethod
    def _normalise_issue(it: Dict) -> Dict:
        """
        Stable flat schema. Pulls XLIFF segment id out of
        translationUnit.properties.id so the rest of the app can map
        back to the source XLIFF.
        """
        def pick(d: Dict, *keys, default=""):
            for k in keys:
                if k in d and d[k] not in (None, ""):
                    return d[k]
            return default

        try:
            issue_type_int = int(pick(it, "issueType", "IssueType",
                                      "type", default=-1))
        except (TypeError, ValueError):
            issue_type_int = -1

        # XLIFF segment id is buried in translationUnit.properties.id
        tu = it.get("translationUnit") or {}
        props = tu.get("properties") or {}
        xliff_seg_id = props.get("id") or ""

        # Source / target text from translationUnit
        src_obj = tu.get("source") or {}
        tgt_obj = tu.get("target") or {}

        # Extra detail fields from the issue payload — these are
        # what makes a finding actionable (the user can see exactly
        # which word/range is flagged and what to change it to).
        target_ranges_raw = it.get("targetRanges") or []
        target_ranges = []
        suggested_fix = ""
        for r in target_ranges_raw:
            if not isinstance(r, dict):
                continue
            rng = r.get("range") or {}
            target_ranges.append({
                "start":  int(rng.get("start", 0) or 0),
                "length": int(rng.get("length", 0) or 0),
                "end":    int(rng.get("end", 0) or 0),
                "fix":    r.get("fix", "") or "",
                "isFixAvailable": bool(r.get("isFixAvailable", False)),
            })
            if not suggested_fix and r.get("fix") and r.get("isFixAvailable"):
                suggested_fix = r["fix"]

        source_ranges_raw = it.get("sourceRanges") or []
        source_ranges = []
        for r in source_ranges_raw:
            if not isinstance(r, dict):
                continue
            rng = r.get("range") or {}
            source_ranges.append({
                "start":  int(rng.get("start", 0) or 0),
                "length": int(rng.get("length", 0) or 0),
                "end":    int(rng.get("end", 0) or 0),
            })

        # additionalData carries the *most useful* per-issue info.
        # Two distinct shapes show up depending on the issue type:
        #
        # Spelling-style:
        #   {"word": "Modunu", "suggestions": ["Odunu", "Kodunu", ...]}
        #
        # Terminology (issueType 1, "No target term"):
        #   {"term": {"source": "wiring diagram",
        #             "targetTerms": ["kablo bağlantı şeması"],
        #             "forbiddenTerms": []},
        #    "potentialWordForm": {
        #         "form": "kablo bağlantı şemalarında",
        #         "range": {"start": 33, "length": 26, "end": 59},
        #         "baseTranslation": "kablo bağlantı şeması"}}
        ad = it.get("additionalData") or {}
        offending_word = ad.get("word", "") or ""
        suggestions = []
        if isinstance(ad.get("suggestions"), list):
            suggestions = [str(s) for s in ad["suggestions"] if s]

        # Terminology-specific fields
        term_obj = ad.get("term") if isinstance(ad.get("term"), dict) else {}
        source_term = (term_obj.get("source") or "") if term_obj else ""
        target_terms = []
        if term_obj and isinstance(term_obj.get("targetTerms"), list):
            target_terms = [str(t) for t in term_obj["targetTerms"] if t]
        forbidden_terms = []
        if term_obj and isinstance(term_obj.get("forbiddenTerms"), list):
            forbidden_terms = [str(t) for t in term_obj["forbiddenTerms"] if t]
        expected_term = target_terms[0] if target_terms else ""

        # potentialWordForm: target-side info — Verifika spotted the
        # term in the target but in a wrong inflected form
        pwf = ad.get("potentialWordForm")
        potential_form = ""
        potential_base = ""
        if isinstance(pwf, dict):
            potential_form = pwf.get("form", "") or ""
            potential_base = pwf.get("baseTranslation", "") or ""
            pwf_range = pwf.get("range") or {}
            # If we have a potentialWordForm range, surface it as a
            # target range so the UI can highlight the wrong-form
            # substring even though Verifika put the source range in
            # sourceRanges (terminology issues come with empty
            # targetRanges by default).
            try:
                pwf_start = int(pwf_range.get("start", 0) or 0)
                pwf_len = int(pwf_range.get("length", 0) or 0)
                if pwf_len > 0:
                    target_ranges.append({
                        "start": pwf_start,
                        "length": pwf_len,
                        "end": pwf_start + pwf_len,
                        "fix": potential_base or expected_term,
                        "isFixAvailable": bool(potential_base or expected_term),
                    })
                    if not suggested_fix and (potential_base or expected_term):
                        suggested_fix = potential_base or expected_term
            except (TypeError, ValueError):
                pass

        # Resolve the human label. Priority:
        #   1. server-supplied `issueKind` text (e.g. "Spelling error")
        #   2. our local ISSUE_TYPE_LABELS fallback
        kind_text = pick(it, "issueKind", "IssueKind")
        label = kind_text or ISSUE_TYPE_LABELS.get(
            issue_type_int, f"Type {issue_type_int}")
        # Top-level category for grouping/filter UI (separate from the
        # specific issueKind text)
        category = ISSUE_TYPE_LABELS.get(
            issue_type_int, f"Type {issue_type_int}")

        return {
            "id":                pick(it, "id", "Id"),
            "reportId":          pick(it, "reportId", "ReportId"),
            "issueType":         issue_type_int,
            "issueLabel":        label,                   # the SPECIFIC kind
            "issueCategory":     category,                # the BROAD category
            "issueKind":         kind_text,
            "issueKindId":       pick(it, "issueKindId", "IssueKindId",
                                      default=None),
            "groupId":           pick(it, "groupId", "GroupId", default=None),
            "translationUnitId": pick(it, "translationUnitId",
                                      "TranslationUnitId"),
            "segmentId":         xliff_seg_id,            # XLIFF id
            "sourceText":        src_obj.get("text", "") or "",
            "targetText":        tgt_obj.get("text", "") or "",
            "originalTarget":    tgt_obj.get("originalText", "") or "",
            "isIgnored":         bool(it.get("isIgnored", False)),
            "comment":           pick(it, "comment", "Comment"),
            "severity":          "warning",
            # ── enriched detail fields ────────────────────────────
            "offendingWord":     offending_word,
            "suggestions":       suggestions,
            "suggestedFix":      suggested_fix,
            "sourceRanges":      source_ranges,
            "targetRanges":      target_ranges,
            # ── terminology-specific (issueType=1, "No target term") ──
            "sourceTerm":        source_term,
            "expectedTerm":      expected_term,        # primary required Turkish term
            "targetTerms":       target_terms,         # full list (may have alternatives)
            "forbiddenTerms":    forbidden_terms,
            "potentialForm":     potential_form,       # the wrong inflected form found
            "potentialBase":     potential_base,       # base form to apply
            "raw":               it,
        }

    # ── Apply edits back to Verifika ────────────────────────────────────────

    def update_translation_units(
        self,
        project_id: str,
        task_id: str,
        updates: List[Dict],
    ) -> int:
        """
        `POST /api/projects/{projectId}/qualityIssues/recheck` — one
        request per translation unit (the server doesn't accept batch).

        Captured live from the Verifika Web UI. Required:
          • Content-Type: `application/*+json` (NOT plain application/json)
          • `taskid` HTTP header (lowercase) with the running task id
          • Body shape: {taskId, targetSegment{elements, text, originalText,
                                                 hasChanges}, translationUnitId}

        Returns the number of units the server accepted.
        """
        if not updates:
            return 0
        if not task_id:
            raise VerifikaError(
                "update_translation_units requires task_id (session "
                "state \"verifika_task_id\" was not populated)"
            )

        accepted = 0
        for u in updates:
            tu_id = u.get("id")
            text = u.get("text", "") or ""
            if not tu_id:
                continue
            length = len(text)
            body = {
                "taskId": task_id,
                "targetSegment": {
                    "elements": [
                        {
                            "elementType": 1,
                            "text": text,
                            "start": 0,
                            "length": length,
                            "end": length,
                        }
                    ],
                    "hasChanges": False,
                    "text": text,
                    "originalText": u.get("originalText", text) or text,
                },
                "translationUnitId": tu_id,
            }
            self._request(
                "POST", f"/api/projects/{project_id}/qualityIssues/recheck",
                json_body=body,
                headers={
                    "Content-Type": "application/*+json",
                    "taskid": task_id,
                },
                accept_status={200, 202, 204},
            )
            accepted += 1
        return accepted

    # ── Issue ignore (UI 'mark as ignored' button) ─────────────────────────

    def ignore_issues(self, project_id: str,
                      issue_ids: List[str], ignored: bool = True) -> None:
        """
        `POST /api/projects/{projectId}/qualityIssues/ignore` — flip the
        Verifika "ignored" (false-positive) state for one or more issues.

        Captured live from the Verifika Web UI (right-click → Mark as
        ignored). Two non-obvious requirements observed on the wire:

        1. Content-Type MUST be `application/*+json` (the Microsoft
           "any json subtype" wildcard). Plain `application/json` is
           rejected with HTTP 405.
        2. The body is just the ignoreQualityIssues array — no
           reportId, no project id (those are in the URL).
        """
        if not issue_ids:
            return
        self._request(
            "POST", f"/api/projects/{project_id}/qualityIssues/ignore",
            json_body={
                "ignoreQualityIssues": [
                    {"qualityIssueId": str(i), "isIgnored": ignored}
                    for i in issue_ids
                ],
            },
            headers={"Content-Type": "application/*+json"},
            accept_status={200, 202, 204},
        )

    # ── End-to-end orchestrator ────────────────────────────────────────────

    def run_full_qa(
        self,
        project_name: str,
        xliff_bytes: bytes,
        xliff_filename: str,
        qa_settings_id: str,
        progress_cb: Optional[Callable[[str, Dict], None]] = None,
    ) -> Tuple[str, str, List[Dict]]:
        """
        Full chain:
            create_project → upload_file → create_report → run_report
            → poll → fetch issues
        Returns (project_id, report_id, [normalised issues]).
        """
        def _emit(stage: str, payload: Dict):
            if progress_cb:
                try: progress_cb(stage, payload)
                except Exception: pass

        # 1. Project
        project = self.create_project(project_name, qa_settings_id=qa_settings_id)
        project_id = project.get("id") or project.get("Id") or ""
        if not project_id:
            raise VerifikaError(f"Project create response missing id: {project}")
        _emit("project_created", project)

        # 2. Upload (chunks + commit)
        upload = self.upload_file(project_id, xliff_bytes, xliff_filename)
        _emit("file_uploaded", upload)

        # 3. Start project (creates a task and assigns it to current user).
        # This is what the Web UI's "Start QA" button calls.
        try:
            self.start_project(project_id)
            _emit("project_started", {"projectId": project_id})
        except VerifikaError as e:
            logger.warning("start_project failed (continuing anyway): %s", e)

        # 4. Read the task id the server just created
        task_id: Optional[str] = None
        try:
            tasks = self.list_tasks(project_id)
            if tasks:
                task_id = tasks[0].get("id") or tasks[0].get("Id")
                _emit("task_ready", tasks[0])
        except VerifikaError as e:
            logger.warning("list_tasks failed: %s", e)

        # 5. Accept the task(s). No-op if already accepted.
        try:
            self.accept_tasks(project_id)
            _emit("tasks_accepted", {"projectId": project_id})
        except VerifikaError as e:
            logger.warning("accept_tasks failed: %s", e)

        # 6. Report (idempotent — created automatically by /start, but
        #    we still need its id for QualityIssues queries)
        report_id = self.create_report(project_id)
        _emit("report_created", {"reportId": report_id})

        # 7. ⭐ Trigger the actual QA analysis. The Reports/Generate
        #    endpoint alone is not enough; tasks/{id}/check is what
        #    makes the server start computing issues.
        triggered = False
        if task_id:
            try:
                self.check_task(project_id, task_id)
                _emit("qa_check_triggered", {"taskId": task_id})
                triggered = True
            except VerifikaError as e:
                logger.warning("tasks/{id}/check failed: %s", e)

        # Fall back to Reports/Generate if check_task wasn't possible
        if not triggered:
            try:
                self.run_report(report_id)
                _emit("report_started", {"reportId": report_id})
            except VerifikaError as e:
                logger.warning("Reports/Generate failed: %s", e)

        # 8. Poll — keyed on projectId (NOT reportId, because the
        #    server's internal report is not the one our POST /api/Reports
        #    returned; projectId is the only reliable key).
        final_payload = self.wait_for_qa_completion(
            project_id,
            progress_cb=lambda p: _emit("qa_progress", p),
        )
        _emit("qa_completed", final_payload)

        # 9. Normalise issues. Each issue carries its own `reportId`
        #    (the *real* internal report id) — we surface that so the
        #    caller can use it for update_translation_units / ignore.
        raw_issues = final_payload.get("qualityIssues") or []
        issues = [self._normalise_issue(it) for it in raw_issues
                  if isinstance(it, dict)]
        _emit("issues_fetched", {"count": len(issues)})

        # Pull the real report id from the first issue if available;
        # fall back to the placeholder we created earlier.
        real_report_id = report_id
        for it in raw_issues:
            if isinstance(it, dict) and it.get("reportId"):
                real_report_id = it["reportId"]
                break

        return project_id, real_report_id, issues

    # ── Convenience: review URL for the rich UI ────────────────────────────

    def report_url(self, project_id: str) -> str:
        """Web UI report screen — opens in a new tab (login session
        required)."""
        return f"{self.base_url}/report/{project_id}/formal"
