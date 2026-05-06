"""
memoQ Server SOAP / WSAPI Project Service
=========================================
Wraps the WSAPI ServerProjectService and FileManagerService for:
  - Listing server projects
  - Listing translation documents in a project
  - Exporting bilingual (.mqxlz, unzipped to .mqxliff bytes)
  - Re-importing a corrected bilingual XLIFF via
    UpdateTranslationDocumentFromBilingual(projectGuid, fileGuid, XLIFF)
  - Discovering the TM and TB GUIDs assigned to a project (REST helper)

Authentication
--------------
The memoQ WSAPI requires the API key inside the SOAP envelope header:
    <ApiKey xmlns="http://kilgray.com/memoqservices/2007">...</ApiKey>
NOT in an HTTP Authorization header.

Bilingual round-trip (V27+)
--------------------------
1. ExportBilingual(IncludeSkeleton=False, SaveCompressed=False) -> plain .mqxliff
   (no skeleton needed — server stores it internally; IncludeSkeleton=True would force
   SaveCompressed=True which produces a ZIP, making direct round-trip impossible)
2. (caller modifies the XLIFF, e.g. translation results, Verifika fixes)
3. BeginChunkedFileUpload + AddNextFileChunk + EndChunkedFileUpload -> uploadFileGuid
4. UpdateTranslationDocumentFromBilingual(projectGuid, uploadFileGuid, XLIFF)
"""

from __future__ import annotations

import io
import logging
import re
import zipfile
from typing import Any, Dict, List, Optional, Tuple

import requests
import urllib3
from lxml import etree

logger = logging.getLogger(__name__)

# Disable noisy SSL warnings when verify=False (self-signed memoQ certs)
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

try:
    from zeep import Client as ZeepClient
    from zeep import Transport
    from zeep.exceptions import Fault as ZeepFault
    _ZEEP_AVAILABLE = True
except Exception:  # pragma: no cover
    ZeepClient = None  # type: ignore
    Transport = None  # type: ignore
    ZeepFault = Exception  # type: ignore
    _ZEEP_AVAILABLE = False


# --- SOAP header builder ----------------------------------------------------

_APIKEY_NAMESPACE = "http://kilgray.com/memoqservices/2007"

_GUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)


def _build_apikey_header(api_key: str):
    """Build the <ApiKey xmlns=...>...</ApiKey> SOAP header element."""
    el = etree.Element("{%s}ApiKey" % _APIKEY_NAMESPACE)
    el.text = api_key
    return [el]


def _extract_guid(value: Any) -> Optional[str]:
    """
    Extract a plain GUID string from whatever ExportTranslationDocument returns.

    Some memoQ server versions return a bare GUID string; others return a
    complex object (e.g. containing ResultStatus + a GUID field). This
    function handles both cases:
      1. value is already a valid GUID string → return as-is
      2. value has a well-known GUID attribute → return that
      3. scan every string attribute for UUID format → return first match
    """
    if value is None:
        return None

    _NULL_UUID = "00000000-0000-0000-0000-000000000000"

    def _valid(s: str) -> Optional[str]:
        s = s.strip()
        if _GUID_RE.match(s) and s.lower() != _NULL_UUID:
            return s
        return None

    # Plain string GUID
    if isinstance(value, str):
        return _valid(value)

    # Check well-known field names first (most specific)
    for field in (
        "ExportedDocumentGuid", "FileGuid", "Guid",
        "DocumentGuid", "ExportGuid", "ResultFileGuid",
    ):
        v = getattr(value, field, None)
        if v is not None:
            result = _valid(str(v))
            if result:
                return result

    # Scan all attributes for anything matching UUID format
    try:
        for attr in dir(value):
            if attr.startswith("_"):
                continue
            try:
                v = getattr(value, attr)
            except Exception:
                continue
            if callable(v):
                continue
            s = str(v).strip() if v is not None else ""
            result = _valid(s)
            if result:
                return result
    except Exception:
        pass

    return None


# --- Helpers ----------------------------------------------------------------


def _flatten_string_list(value: Any) -> List[str]:
    """
    memoQ WSDL wraps repeated <string> elements in an ArrayOfstring container,
    so zeep returns the value either as a list[str], a wrapper object with a
    `.string` attribute that is itself a list, or just a single str. Normalise
    every shape into a clean list[str], skipping the literal placeholder
    'string' that the WSDL ships when the field is empty.
    """
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value and value.lower() != "string" else []
    if isinstance(value, (list, tuple)):
        out: List[str] = []
        for v in value:
            out.extend(_flatten_string_list(v))
        return out
    inner = getattr(value, "string", None)
    if inner is not None:
        return _flatten_string_list(inner)
    try:
        return [str(value)] if str(value).lower() != "string" else []
    except Exception:
        return []


# --- Service ----------------------------------------------------------------


class MemoQProjectService:
    """
    SOAP client for memoQ Server project + document operations.

    Args:
        server_url: base URL of memoQ services, e.g.
            "https://mirage.memoq.com:9091/adaturkey/memoqservices"
            (the WSDL endpoints are appended automatically)
        api_key:   memoQ Server API key
        verify_ssl: leave False for self-signed server certs
        timeout:   per-request timeout in seconds
    """

    PROJECT_WSDL = "ServerProject?wsdl"
    FILEMGR_WSDL = "FileManager?wsdl"

    # 500 KB chunks (memoQ recommends 100 KB - 1 MB)
    CHUNK_SIZE = 500 * 1024

    def __init__(
        self,
        server_url: str,
        api_key: str,
        verify_ssl: bool = False,
        timeout: int = 60,
    ):
        if not _ZEEP_AVAILABLE:
            raise RuntimeError(
                "The 'zeep' package is not installed. Add 'zeep>=4.2.0' to "
                "requirements.txt and reinstall."
            )

        self.server_url = server_url.rstrip("/")
        self.api_key = api_key
        self.verify_ssl = verify_ssl
        self.timeout = timeout

        self._session = requests.Session()
        self._session.verify = verify_ssl
        transport = Transport(session=self._session, timeout=timeout)

        proj_url = f"{self.server_url}/{self.PROJECT_WSDL}"
        file_url = f"{self.server_url}/{self.FILEMGR_WSDL}"

        try:
            self._project_client = ZeepClient(proj_url, transport=transport)
            self._file_client = ZeepClient(file_url, transport=transport)
        except Exception as e:
            raise RuntimeError(
                f"Failed to load memoQ WSDLs at {self.server_url}: {e}"
            ) from e

    # ------------------------------------------------------------------ #
    # Internal helpers                                                    #
    # ------------------------------------------------------------------ #

    def _hdr(self):
        return _build_apikey_header(self.api_key)

    @staticmethod
    def _zeep_to_dict(obj) -> Dict:
        """Best-effort zeep-object to dict (recursive) without losing data."""
        if obj is None:
            return {}
        if isinstance(obj, (str, int, float, bool)):
            return obj  # type: ignore
        if isinstance(obj, list):
            return [MemoQProjectService._zeep_to_dict(x) for x in obj]  # type: ignore
        out: Dict = {}
        for k in dir(obj):
            if k.startswith("_"):
                continue
            try:
                v = getattr(obj, k)
            except Exception:
                continue
            if callable(v):
                continue
            out[k] = v
        return out

    # ------------------------------------------------------------------ #
    # Public API                                                          #
    # ------------------------------------------------------------------ #

    def list_projects(self, only_active: bool = True) -> List[Dict]:
        """
        List server projects. Returns list of dicts with at least:
          ServerProjectGuid, Name, SourceLanguageCode, TargetLanguageCodes,
          DeadLine, CreationTime, ProjectStatus
        """
        try:
            ProjectFilter = self._project_client.get_type(
                "{http://kilgray.com/memoqservices/2007/projects}"
                "ServerProjectListFilter"
            )
            flt = ProjectFilter()
            res = self._project_client.service.ListProjects(
                filter=flt, _soapheaders=self._hdr()
            )
        except Exception:
            try:
                res = self._project_client.service.ListProjects(
                    _soapheaders=self._hdr()
                )
            except Exception as e:
                raise RuntimeError(f"ListProjects failed: {e}") from e

        items: List[Dict] = []
        for p in (res or []):
            d = self._zeep_to_dict(p)
            target_langs = _flatten_string_list(d.get("TargetLanguageCodes"))
            items.append({
                "ServerProjectGuid": d.get("ServerProjectGuid"),
                "Name": d.get("Name"),
                "SourceLanguageCode": d.get("SourceLanguageCode"),
                "TargetLanguageCodes": target_langs,
                "DeadLine": str(d.get("DeadLine")) if d.get("DeadLine") else "",
                "CreationTime": str(d.get("CreationTime")) if d.get("CreationTime") else "",
                "ProjectStatus": d.get("ProjectStatus"),
                "_raw": d,
            })

        if only_active:
            items = [
                p for p in items
                if str(p.get("ProjectStatus", "")).lower() not in ("wrappedup", "wrapped up", "archived")
            ]

        logger.info("Listed %d server projects", len(items))
        return items

    def list_documents(
        self,
        project_guid: str,
        target_lang_codes: Optional[List[str]] = None,
    ) -> List[Dict]:
        """
        List translation documents in a project. Returns dicts with:
          DocumentGuid, DocumentName, TargetLangCode, WorkflowStatus, Version

        Uses ListProjectTranslationDocuments (v1 only).
        ListProjectTranslationDocuments2 is NOT called — it requires an options
        object and raises NullReferenceException when called without one.
        """
        if not target_lang_codes:
            try:
                project = self._project_client.service.GetProject(
                    spGuid=project_guid, _soapheaders=self._hdr()
                )
                pdict = self._zeep_to_dict(project)
                target_lang_codes = _flatten_string_list(pdict.get("TargetLanguageCodes"))
            except Exception as e:
                logger.warning("GetProject failed while resolving target langs: %s", e)
                target_lang_codes = []

        last_error: Optional[Exception] = None
        results_by_guid: Dict[str, Dict] = {}

        def _collect(res):
            if res is None:
                return
            if not isinstance(res, (list, tuple)):
                inner = getattr(res, "TranslationDocumentInfo", None)
                if inner is None:
                    inner = getattr(res, "TranslationDocumentInfo2", None)
                if inner is None:
                    res = [res]
                else:
                    res = inner if isinstance(inner, (list, tuple)) else [inner]
            for d in (res or []):
                dd = self._zeep_to_dict(d)
                guid = dd.get("DocumentGuid")
                if not guid:
                    continue
                results_by_guid[str(guid)] = {
                    "DocumentGuid": guid,
                    "DocumentName": dd.get("DocumentName") or dd.get("Name"),
                    "TargetLangCode": dd.get("TargetLangCode"),
                    "WorkflowStatus": dd.get("WorkflowStatus"),
                    "Version": dd.get("Version"),
                    "TotalRowCount": dd.get("TotalRowCount"),
                    "ConfirmedRowCount": dd.get("ConfirmedRowCount"),
                    "_raw": dd,
                }

        # Strategy 0 — ListProjectTranslationDocuments (v1)
        try:
            res = self._project_client.service.ListProjectTranslationDocuments(
                serverProjectGuid=project_guid, _soapheaders=self._hdr()
            )
            _collect(res)
        except Exception as e:
            last_error = e
            logger.warning("list_documents (ListProjectTranslationDocuments) failed: %s", e)

        # Strategy C — grouped-by-source-file fallback
        if not results_by_guid:
            try:
                res = self._project_client.service.ListProjectTranslationDocumentsGroupedBySourceFile(
                    serverProjectGuid=project_guid, _soapheaders=self._hdr()
                )
                for grp in (res or []):
                    gdict = self._zeep_to_dict(grp)
                    docs = gdict.get("Documents") or gdict.get("TranslationDocuments")
                    inner = []
                    if hasattr(docs, "__iter__") and not isinstance(docs, (str, bytes)):
                        inner = list(docs)
                    elif docs is not None:
                        tdi = getattr(docs, "TranslationDocumentInfo", None)
                        if tdi is not None:
                            inner = list(tdi) if isinstance(tdi, (list, tuple)) else [tdi]
                    _collect(inner)
            except Exception as e:
                last_error = e

        items = list(results_by_guid.values())
        if not items and last_error is not None:
            raise RuntimeError(f"ListProjectTranslationDocuments failed: {last_error}") from last_error

        logger.info("Project %s: %d documents", project_guid, len(items))
        return items

    # ------------------------------------------------------------------ #
    # Bilingual export                                                    #
    # ------------------------------------------------------------------ #

    def export_bilingual(
        self,
        project_guid: str,
        document_guid: str,
        include_skeleton: bool = False,
    ) -> Tuple[bytes, str]:
        """
        Export the document as a bilingual XLIFF for round-trip editing.

        Returns (xliff_bytes, suggested_filename).

        memoQ server constraint: IncludeSkeleton=True REQUIRES SaveCompressed=True
        (the skeleton is always a separate file in the ZIP, never embedded in the XLIFF).
        For UpdateTranslationDocumentFromBilingual the skeleton is NOT needed —
        the server already has it internally.  So we use IncludeSkeleton=False,
        SaveCompressed=False → plain single .mqxliff, directly uploadable.

        Strategies:
          1. ExportTranslationDocumentAsXliffBilingual(IncludeSkeleton=False, SaveCompressed=False)
          2. ExportTranslationDocument (generic fallback)
        """
        file_guid: Optional[str] = None
        last_err: Optional[Exception] = None

        # IncludeSkeleton=False, SaveCompressed=False  →  plain .mqxliff (no ZIP)
        # memoQ enforces: IncludeSkeleton=True requires SaveCompressed=True
        # For server-side UpdateTranslationDocumentFromBilingual the skeleton is
        # NOT required — the server already stores it internally.
        try:
            opts_type = self._project_client.get_type(
                '{http://kilgray.com/memoqservices/2007}XliffBilingualExportOptions'
            )
            opts_obj = opts_type(
                IncludeSkeleton=False,
                SaveCompressed=False,
                FullVersionHistory=False,
            )
        except Exception as factory_err:
            logger.warning("XliffBilingualExportOptions factory failed: %s — using dict", factory_err)
            opts_obj = {
                'IncludeSkeleton': False,
                'SaveCompressed': False,
                'FullVersionHistory': False,
            }

        # Strategy 1: dedicated XLIFF bilingual export (exact WSDL signature)
        try:
            raw = self._project_client.service.ExportTranslationDocumentAsXliffBilingual(
                serverProjectGuid=project_guid,
                docGuid=document_guid,
                options=opts_obj,
                _soapheaders=self._hdr(),
            )
            file_guid = _extract_guid(raw)
            if file_guid is None:
                logger.warning("ExportTranslationDocumentAsXliffBilingual: no valid GUID — raw=%r", raw)
            else:
                logger.info("ExportTranslationDocumentAsXliffBilingual succeeded")
        except Exception as e:
            last_err = e
            logger.warning("ExportTranslationDocumentAsXliffBilingual failed: %s", e)

        # Strategy 2: generic primary-format export (last resort — may return original file format)
        if file_guid is None:
            try:
                raw = self._project_client.service.ExportTranslationDocument(
                    serverProjectGuid=project_guid,
                    docGuid=document_guid,
                    _soapheaders=self._hdr(),
                )
                file_guid = _extract_guid(raw)
                if file_guid is None:
                    logger.warning("ExportTranslationDocument: no valid GUID — raw=%r", raw)
                else:
                    logger.warning("ExportTranslationDocument (generic) succeeded — format may not be XLIFF")
            except Exception as e:
                last_err = e
                logger.warning("ExportTranslationDocument failed: %s", e)

        if not file_guid:
            raise RuntimeError(
                f"All export strategies failed. Last error: {last_err}"
            )

        # Download the exported file (chunked)
        raw_bytes = self._download_file(file_guid)

        # If it's a .mqxlz (ZIP), extract the bilingual document
        suggested_name = "document.mqxliff"
        if raw_bytes[:2] == b"PK":
            try:
                with zipfile.ZipFile(io.BytesIO(raw_bytes)) as zf:
                    all_names = zf.namelist()
                    logger.warning("mqxlz contents: %s", all_names)

                    # Priority: .mqxliff → .xliff/.xlf → .xml → XML content scan → any file
                    candidates = (
                        [n for n in all_names if n.lower().endswith(".mqxliff")]
                        or [n for n in all_names if n.lower().endswith((".xliff", ".xlf"))]
                        or [n for n in all_names if n.lower().endswith(".xml")]
                    )

                    # No extension match — scan file content for XML/XLIFF markers
                    if not candidates:
                        for name in all_names:
                            if name.endswith("/"):
                                continue
                            try:
                                head = zf.read(name)[:200]
                                if b"<xliff" in head or head.lstrip()[:5] == b"<?xml":
                                    candidates = [name]
                                    break
                            except Exception:
                                continue

                    # Last resort: take first non-directory entry
                    if not candidates:
                        candidates = [n for n in all_names if not n.endswith("/")]

                    if not candidates:
                        raise RuntimeError(
                            f"mqxlz package is empty. Contents: {all_names}"
                        )

                    inner = candidates[0]
                    suggested_name = inner.split("/")[-1] or inner
                    raw_bytes = zf.read(inner)
            except zipfile.BadZipFile as e:
                raise RuntimeError(
                    f"Bilingual export was ZIP-magic but unreadable: {e}"
                ) from e

        logger.info(
            "Bilingual export OK — %d bytes (%s)", len(raw_bytes), suggested_name
        )
        return raw_bytes, suggested_name

    # ------------------------------------------------------------------ #
    # Bilingual update (round-trip)                                       #
    # ------------------------------------------------------------------ #

    def update_bilingual(
        self,
        project_guid: str,
        document_guid: str,
        xliff_bytes: bytes,
        filename: str = "translated.mqxliff",
    ) -> Optional[int]:
        """
        Push a corrected XLIFF back into the existing project document via
        UpdateTranslationDocumentFromBilingual. The document is identified by
        the GUID embedded inside the bilingual file — no docGuid needed at the
        SOAP level.

        WSDL signature (verified from offline docs v12.2.40 Reference.cs):
          UpdateTranslationDocumentFromBilingual(
              Guid serverProjectGuid,
              Guid fileGuid,             ← GUID from BeginChunkedFileUpload
              BilingualDocFormat docFormat)
        Returns TranslationDocImportResultInfo[].
        """
        logger.info(
            "update_bilingual: uploading %d bytes as %s (project=%s)",
            len(xliff_bytes), filename, project_guid,
        )
        upload_guid = self._upload_file(xliff_bytes, filename=filename)
        logger.info("update_bilingual: upload complete, fileGuid=%s", upload_guid)

        try:
            result = self._project_client.service.UpdateTranslationDocumentFromBilingual(
                serverProjectGuid=project_guid,
                fileGuid=upload_guid,
                docFormat="XLIFF",
                _soapheaders=self._hdr(),
            )
            logger.info("UpdateTranslationDocumentFromBilingual result: %r", result)
        except Exception as e:
            logger.error("UpdateTranslationDocumentFromBilingual SOAP fault: %s", e)
            raise RuntimeError(
                f"UpdateTranslationDocumentFromBilingual failed: {e}"
            ) from e

        # Read back the new version number
        try:
            docs = self.list_documents(project_guid)
            for d in docs:
                if str(d.get("DocumentGuid")) == str(document_guid):
                    ver = d.get("MajorVersion") or d.get("MinorVersion") or d.get("Version")
                    logger.info("update_bilingual: new document version = %s", ver)
                    return ver
        except Exception as e:
            logger.warning("update_bilingual: version read-back failed: %s", e)
        return None

    # ------------------------------------------------------------------ #
    # Project resources (TM/TB GUIDs)                                     #
    # ------------------------------------------------------------------ #

    def get_project_resources(self, project_guid: str) -> Tuple[List[str], List[str]]:
        """
        Return (tm_guids, tb_guids) assigned to the project, via SOAP.
        Falls back to empty lists on error.
        """
        tm_guids: List[str] = []
        tb_guids: List[str] = []
        try:
            try:
                tms = self._project_client.service.ListProjectTMs2(
                    serverProjectGuid=project_guid, _soapheaders=self._hdr()
                )
            except Exception:
                tms = self._project_client.service.ListProjectTMs(
                    serverProjectGuid=project_guid, _soapheaders=self._hdr()
                )
            for tm in (tms or []):
                d = self._zeep_to_dict(tm)
                guid = d.get("TMGuid") or d.get("Guid") or d.get("ResourceGuid")
                if guid:
                    tm_guids.append(str(guid))
        except Exception as e:
            logger.warning("ListProjectTMs failed: %s", e)
        try:
            try:
                tbs = self._project_client.service.ListProjectTBs3(
                    serverProjectGuid=project_guid, _soapheaders=self._hdr()
                )
            except Exception:
                tbs = self._project_client.service.ListProjectTBs(
                    serverProjectGuid=project_guid, _soapheaders=self._hdr()
                )
            for tb in (tbs or []):
                d = self._zeep_to_dict(tb)
                guid = d.get("TBGuid") or d.get("Guid") or d.get("ResourceGuid")
                if guid:
                    tb_guids.append(str(guid))
        except Exception as e:
            logger.warning("ListProjectTBs failed: %s", e)
        return tm_guids, tb_guids

    # ------------------------------------------------------------------ #
    # Chunked file transfer                                               #
    # ------------------------------------------------------------------ #

    def _download_file(self, file_guid: str) -> bytes:
        """
        Download a server-side temp file by GUID.
        WSDL: BeginChunkedFileDownload(string fileGuid, bool zip) -> {Guid guid, ...}
        After download, calls DeleteFile to clean up the server-side temp file.
        """
        try:
            raw = self._file_client.service.BeginChunkedFileDownload(
                fileGuid=file_guid, zip=False, _soapheaders=self._hdr()
            )
            session_id = _extract_guid(raw) or str(raw)
        except Exception as e:
            raise RuntimeError(f"BeginChunkedFileDownload failed: {e}") from e

        buf = io.BytesIO()
        try:
            while True:
                chunk = self._file_client.service.GetNextFileChunk(
                    sessionId=session_id,
                    byteCount=self.CHUNK_SIZE,
                    _soapheaders=self._hdr(),
                )
                if not chunk:
                    break
                buf.write(chunk)
                if len(chunk) < self.CHUNK_SIZE:
                    break
        finally:
            try:
                self._file_client.service.EndChunkedFileDownload(
                    sessionId=session_id, _soapheaders=self._hdr()
                )
            except Exception:
                pass

        try:
            self._file_client.service.DeleteFile(
                fileGuid=file_guid, _soapheaders=self._hdr()
            )
        except Exception as e:
            logger.warning("DeleteFile failed for %s: %s", file_guid, e)

        return buf.getvalue()

    def _upload_file(self, data: bytes, filename: str) -> str:
        """
        Upload bytes to the server in chunks.
        WSDL:
          BeginChunkedFileUpload(string fileName, bool isZipped) -> Guid (fileIdAndSessionId)
          AddNextFileChunk(string fileIdAndSessionId, byte[] fileData)
          EndChunkedFileUpload(string fileIdAndSessionId)
        """
        try:
            upload_session_id = self._file_client.service.BeginChunkedFileUpload(
                fileName=filename,
                isZipped=False,
                _soapheaders=self._hdr(),
            )
        except Exception as e:
            raise RuntimeError(f"BeginChunkedFileUpload failed: {e}") from e

        upload_session_id = _extract_guid(upload_session_id) or str(upload_session_id)

        view = memoryview(data)
        offset = 0
        while offset < len(view):
            chunk = bytes(view[offset:offset + self.CHUNK_SIZE])
            try:
                self._file_client.service.AddNextFileChunk(
                    fileIdAndSessionId=upload_session_id,
                    fileData=chunk,
                    _soapheaders=self._hdr(),
                )
            except Exception as e:
                raise RuntimeError(
                    f"Chunked upload failed at offset {offset}: {e}"
                ) from e
            offset += len(chunk)

        try:
            self._file_client.service.EndChunkedFileUpload(
                fileIdAndSessionId=upload_session_id, _soapheaders=self._hdr()
            )
        except Exception as e:
            raise RuntimeError(f"EndChunkedFileUpload failed: {e}") from e

        return str(upload_session_id)

    # ------------------------------------------------------------------ #
    # TM Analysis                                                         #
    # ------------------------------------------------------------------ #

    def run_analysis(
        self,
        project_guid: str,
        doc_guid: str,
        tgt_lang_code: str,
    ) -> dict:
        """
        Run TM analysis on a single document using memoQ's RunAnalysis SOAP method.

        Returns a dict with segment counts per match level:
          hit_101, hit_100, hit_95_99, hit_85_94, hit_75_84,
          hit_50_74, no_match, repetition, total_segments

        Returns empty dict on failure (non-fatal — caller should continue).
        """
        try:
            opts_type = self._project_client.get_type(
                '{http://kilgray.com/memoqservices/2007}AnalysisOptions'
            )
            opts = opts_type(
                DocumentGuids=[doc_guid],
                # LanguageCodes omitted — memoQ analyses all target langs for the document;
                # specifying a code that doesn't match exactly raises a server error.
                RepetitionPreferenceOver100=False,
                StoreReportInProject=False,
                Note="",
            )

            result = self._project_client.service.RunAnalysis(
                serverProjectGuid=project_guid,
                options=opts,
                _soapheaders=self._hdr(),
            )

            if result is None:
                return {}

            r = self._zeep_to_dict(result)

            # ResultsForTargetLangs → list of AnalysisResultForLang
            langs = r.get('ResultsForTargetLangs') or []
            if isinstance(langs, dict):
                langs = langs.get('AnalysisResultForLang') or []
            if not isinstance(langs, list):
                langs = [langs]

            # Pick the entry matching tgt_lang_code; fall back to first
            lang_entry = None
            tgt_base = tgt_lang_code.lower().split('-')[0]
            for lr in langs:
                lrd = self._zeep_to_dict(lr) if not isinstance(lr, dict) else lr
                code = str(lrd.get('TargetLangCode') or '').lower().split('-')[0]
                if code == tgt_base:
                    lang_entry = lrd
                    break
            if lang_entry is None and langs:
                lang_entry = self._zeep_to_dict(langs[0]) if not isinstance(langs[0], dict) else langs[0]
            if lang_entry is None:
                return {}

            summary = lang_entry.get('Summary') or {}
            if not isinstance(summary, dict):
                summary = self._zeep_to_dict(summary)

            def _sc(key: str) -> int:
                node = summary.get(key) or {}
                if not isinstance(node, dict):
                    node = self._zeep_to_dict(node)
                try:
                    return int(node.get('SegmentCount') or 0)
                except (TypeError, ValueError):
                    return 0

            stats = {
                'hit_101':        _sc('Hit101'),
                'hit_100':        _sc('Hit100'),
                'hit_95_99':      _sc('Hit95_99'),
                'hit_85_94':      _sc('Hit85_94'),
                'hit_75_84':      _sc('Hit75_84'),
                'hit_50_74':      _sc('Hit50_74'),
                'no_match':       _sc('NoMatch'),
                'repetition':     _sc('Repetition'),
                'total_segments': _sc('All'),
            }
            logger.info("RunAnalysis: %s", stats)
            return stats

        except Exception as e:
            logger.warning("run_analysis failed: %s", e)
            return {}

    # ------------------------------------------------------------------ #
    # Pretranslate                                                        #
    # ------------------------------------------------------------------ #

    def pretranslate_document(
        self,
        project_guid: str,
        doc_guid: str,
        tm_guids: List[str],
        good_match_rate: int = 50,
    ) -> bool:
        """
        Pre-translate a document using memoQ's PretranslateDocuments SOAP method.

        Fills ALL TM matches >= good_match_rate into the document on the server.
        Segments are marked as Pretranslated (not confirmed/locked) so they can
        be overwritten when we import the corrected bilingual later.

        After this call, export_bilingual() will return an XLIFF with:
          - target content filled from TM for matched segments
          - mq:percent attribute per segment reflecting the TM match rate
          - mq:status="Pretranslated" for matched segments

        Args:
            project_guid:    server project GUID
            doc_guid:        translation document GUID
            tm_guids:        TM GUIDs to use (ResourceFilter); pass [] to use all project TMs
            good_match_rate: minimum match rate to pretranslate (default 50)

        Raises RuntimeError on SOAP failure.
        """
        try:
            opts_type = self._project_client.get_type(
                '{http://kilgray.com/memoqservices/2007}PretranslateOptions'
            )
            filter_type = self._project_client.get_type(
                '{http://kilgray.com/memoqservices/2007}PreTransFilter'
            )

            # Build ResourceFilter.TMs — try a few zeep array formats
            resource_filter = None
            if tm_guids:
                try:
                    resource_filter = filter_type(TMs=tm_guids)
                except Exception:
                    try:
                        resource_filter = filter_type(TMs={'guid': tm_guids})
                    except Exception:
                        resource_filter = None  # fall back: use all project TMs

            opts_kwargs = dict(
                GoodMatchRate=good_match_rate,
                PretranslateLookupBehavior='AnyMatch',
                UseMT=False,
                LockPretranslated=False,
                ConfirmLockPretranslated='None',
                ConfirmLockUnambiguousMatchesOnly=False,
                FinalTranslationState='Pretranslated',
            )
            if resource_filter is not None:
                opts_kwargs['ResourceFilter'] = resource_filter

            opts = opts_type(**opts_kwargs)

            # Try passing doc GUIDs as a plain list first; fall back to wrapped form
            try:
                result = self._project_client.service.PretranslateDocuments(
                    serverProjectGuid=project_guid,
                    translationDocGuids=[doc_guid],
                    options=opts,
                    _soapheaders=self._hdr(),
                )
            except Exception as _e1:
                logger.warning("PretranslateDocuments (list) failed: %s — retrying wrapped", _e1)
                result = self._project_client.service.PretranslateDocuments(
                    serverProjectGuid=project_guid,
                    translationDocGuids={'guid': [doc_guid]},
                    options=opts,
                    _soapheaders=self._hdr(),
                )

            logger.info("PretranslateDocuments succeeded: %r", result)
            return True

        except Exception as e:
            raise RuntimeError(f"pretranslate_document failed: {e}") from e
