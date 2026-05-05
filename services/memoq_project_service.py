"""
memoQ Server SOAP / WSAPI Project Service
=========================================
Wraps the WSAPI ServerProjectService and FileManagerService for:
  - Listing server projects
  - Listing translation documents in a project
  - Exporting bilingual (.mqxlz, unzipped to .mqxliff bytes)
  - Re-importing a corrected bilingual XLIFF via
    UpdateTranslationDocumentFromBilingual(projectGuid, fileGuid, "XLIFF")
  - Discovering the TM and TB GUIDs assigned to a project (REST helper)

Authentication
--------------
The memoQ WSAPI requires the API key inside the SOAP envelope header:
    <ApiKey xmlns="http://kilgray.com/memoqservices/2007">...</ApiKey>
NOT in an HTTP Authorization header.

Bilingual round-trip (proven live, v1.10 -> v1.12)
--------------------------------------------------
1. ExportBilingual(IncludeSkeleton=True, SaveCompressed=True) -> .mqxlz (ZIP)
2. unzip in-memory -> document.mqxliff
3. (caller modifies the XLIFF, e.g. translation results, Verifika fixes)
4. BeginChunkedFileUpload + AddNextFileChunk + EndChunkedFileUpload -> uploadFileGuid
5. UpdateTranslationDocumentFromBilingual(projectGuid, uploadFileGuid, "XLIFF")
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

    # Plain string GUID
    if isinstance(value, str) and _GUID_RE.match(value.strip()):
        return value.strip()

    # Check well-known field names first (most specific)
    for field in (
        "ExportedDocumentGuid", "FileGuid", "Guid",
        "DocumentGuid", "ExportGuid", "ResultFileGuid",
    ):
        v = getattr(value, field, None)
        if v is not None:
            s = str(v).strip()
            if _GUID_RE.match(s):
                return s

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
            if _GUID_RE.match(s):
                return s
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
        """
        if not target_lang_codes:
            try:
                project = self._project_client.service.GetProject(
                    serverProjectGuid=project_guid, _soapheaders=self._hdr()
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

        # Strategy 0 — plain ListProjectTranslationDocuments
        try:
            res = self._project_client.service.ListProjectTranslationDocuments(
                serverProjectGuid=project_guid, _soapheaders=self._hdr()
            )
            _collect(res)
        except Exception as e:
            last_error = e

        # Strategy 0b — "2" variant, no extra args
        if not results_by_guid:
            try:
                res = self._project_client.service.ListProjectTranslationDocuments2(
                    serverProjectGuid=project_guid, _soapheaders=self._hdr()
                )
                _collect(res)
            except Exception as e:
                last_error = e

        # Strategy A — per target language with various kwarg spellings
        if not results_by_guid:
            for tlc in (target_lang_codes or []):
                if not tlc:
                    continue
                for kwarg_name in ("targetLangCode", "targetLanguageCode", "languageCode"):
                    try:
                        kwargs = {"serverProjectGuid": project_guid, kwarg_name: tlc}
                        res = self._project_client.service.ListProjectTranslationDocuments2(
                            **kwargs, _soapheaders=self._hdr()
                        )
                        _collect(res)
                        break
                    except TypeError:
                        continue
                    except Exception as e:
                        last_error = e
                        break
                else:
                    for kwarg_name in ("targetLangCode", "targetLanguageCode", "languageCode"):
                        try:
                            kwargs = {"serverProjectGuid": project_guid, kwarg_name: tlc}
                            res = self._project_client.service.ListProjectTranslationDocuments(
                                **kwargs, _soapheaders=self._hdr()
                            )
                            _collect(res)
                            break
                        except TypeError:
                            continue
                        except Exception as e:
                            last_error = e
                            break

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
        include_skeleton: bool = True,
    ) -> Tuple[bytes, str]:
        """
        Export the document as a bilingual XLIFF (runs silently in background).

        Returns (xliff_bytes, suggested_filename).

        ExportTranslationDocument may return either a bare GUID string or a
        complex result object depending on the server version. _extract_guid()
        handles both cases by scanning for a UUID-formatted string.

        Four call-signature strategies for cross-version compatibility:
          1. docGuid, no options   (Mirage v12.2 confirmed)
          2. documentGuid, no options   (standard WSDL naming)
          3. docGuid + dict exportOptions
          4. documentGuid + dict exportOptions
        """
        file_guid: Optional[str] = None
        last_err: Optional[Exception] = None

        export_opts = {
            'BilingualDocFormat': 'XLIFF',
            'IncludeSkeleton': include_skeleton,
            'SaveCompressed': include_skeleton,
            'IncludeFullVersionHistory': False,
            'FillInUnconfirmedTranslations': False,
        }

        # Strategy 1: docGuid, no options
        try:
            raw = self._project_client.service.ExportTranslationDocument(
                serverProjectGuid=project_guid,
                docGuid=document_guid,
                _soapheaders=self._hdr(),
            )
            file_guid = _extract_guid(raw)
            if file_guid is None:
                logger.debug("export strategy 1: result has no GUID — raw=%r", raw)
        except Exception as e:
            last_err = e
            logger.debug("export strategy 1 (docGuid, no opts) failed: %s", e)

        # Strategy 2: documentGuid, no options
        if file_guid is None:
            try:
                raw = self._project_client.service.ExportTranslationDocument(
                    serverProjectGuid=project_guid,
                    documentGuid=document_guid,
                    _soapheaders=self._hdr(),
                )
                file_guid = _extract_guid(raw)
                if file_guid is None:
                    logger.debug("export strategy 2: result has no GUID — raw=%r", raw)
            except Exception as e:
                last_err = e
                logger.debug("export strategy 2 (documentGuid, no opts) failed: %s", e)

        # Strategy 3: docGuid + dict options
        if file_guid is None:
            try:
                raw = self._project_client.service.ExportTranslationDocument(
                    serverProjectGuid=project_guid,
                    docGuid=document_guid,
                    exportOptions=export_opts,
                    _soapheaders=self._hdr(),
                )
                file_guid = _extract_guid(raw)
                if file_guid is None:
                    logger.debug("export strategy 3: result has no GUID — raw=%r", raw)
            except Exception as e:
                last_err = e
                logger.debug("export strategy 3 (docGuid, dict opts) failed: %s", e)

        # Strategy 4: documentGuid + dict options
        if file_guid is None:
            try:
                raw = self._project_client.service.ExportTranslationDocument(
                    serverProjectGuid=project_guid,
                    documentGuid=document_guid,
                    exportOptions=export_opts,
                    _soapheaders=self._hdr(),
                )
                file_guid = _extract_guid(raw)
                if file_guid is None:
                    logger.debug("export strategy 4: result has no GUID — raw=%r", raw)
            except Exception as e:
                last_err = e
                logger.debug("export strategy 4 (documentGuid, dict opts) failed: %s", e)

        if not file_guid:
            raise RuntimeError(
                f"ExportTranslationDocument failed: {last_err}"
            )

        # Download the exported file (chunked)
        raw_bytes = self._download_file(file_guid)

        # If it's a .mqxlz (ZIP), extract the bilingual document
        suggested_name = "document.mqxliff"
        if raw_bytes[:2] == b"PK":
            try:
                with zipfile.ZipFile(io.BytesIO(raw_bytes)) as zf:
                    all_names = zf.namelist()
                    logger.info("mqxlz contents: %s", all_names)

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
        Push a corrected XLIFF back into the project document.
        Returns the new document version number (if available), else None.
        """
        upload_guid = self._upload_file(xliff_bytes, filename=filename)

        updated = False
        last_err: Optional[Exception] = None

        # Strategy 1: docGuid naming (Mirage v12.2)
        try:
            self._project_client.service.UpdateTranslationDocumentFromBilingual(
                serverProjectGuid=project_guid,
                docGuid=document_guid,
                fileGuid=upload_guid,
                bilingualDocFormat="XLIFF",
                _soapheaders=self._hdr(),
            )
            updated = True
        except Exception as e:
            last_err = e
            logger.debug("update strategy 1 (docGuid) failed: %s", e)

        # Strategy 2: documentGuid naming (standard WSDL)
        if not updated:
            try:
                self._project_client.service.UpdateTranslationDocumentFromBilingual(
                    serverProjectGuid=project_guid,
                    documentGuid=document_guid,
                    fileGuid=upload_guid,
                    bilingualDocFormat="XLIFF",
                    _soapheaders=self._hdr(),
                )
                updated = True
            except Exception as e:
                last_err = e
                logger.debug("update strategy 2 (documentGuid) failed: %s", e)

        # Strategy 3: positional args
        if not updated:
            try:
                self._project_client.service.UpdateTranslationDocumentFromBilingual(
                    project_guid, document_guid, upload_guid, "XLIFF",
                    _soapheaders=self._hdr(),
                )
                updated = True
            except Exception as e:
                raise RuntimeError(
                    f"UpdateTranslationDocumentFromBilingual failed: {e}"
                ) from e

        # Read back the new version number
        try:
            docs = self.list_documents(project_guid)
            for d in docs:
                if d.get("DocumentGuid") == document_guid:
                    return d.get("Version")
        except Exception:
            pass
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
        # BeginChunkedFileDownload returns a sessionId (separate from fileGuid).
        # GetNextFileChunk and EndChunkedFileDownload take sessionId, not fileGuid.
        try:
            raw = self._file_client.service.BeginChunkedFileDownload(
                fileGuid=file_guid, _soapheaders=self._hdr()
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
        return buf.getvalue()

    def _upload_file(self, data: bytes, filename: str) -> str:
        try:
            file_guid = self._file_client.service.BeginChunkedFileUpload(
                fileName=filename,
                isZipped=False,
                _soapheaders=self._hdr(),
            )
        except Exception as e:
            raise RuntimeError(f"BeginChunkedFileUpload failed: {e}") from e

        # Extract GUID if server returns a complex object
        file_guid = _extract_guid(file_guid) or str(file_guid)

        view = memoryview(data)
        offset = 0
        while offset < len(view):
            chunk = bytes(view[offset:offset + self.CHUNK_SIZE])
            try:
                self._file_client.service.AddNextFileChunk(
                    fileGuid=file_guid,
                    fileChunk=chunk,
                    _soapheaders=self._hdr(),
                )
            except Exception as e:
                raise RuntimeError(
                    f"Chunked upload failed at offset {offset}: {e}"
                ) from e
            offset += len(chunk)

        try:
            self._file_client.service.EndChunkedFileUpload(
                fileGuid=file_guid, _soapheaders=self._hdr()
            )
        except Exception as e:
            raise RuntimeError(f"EndChunkedFileUpload failed: {e}") from e

        return str(file_guid)
