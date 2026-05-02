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
import zipfile
from typing import Dict, List, Optional, Tuple

import requests
import urllib3
from lxml import etree

logger = logging.getLogger(__name__)

# Disable noisy SSL warnings when verify=False (self-signed memoQ certs)
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Lazy import of zeep so the module loads even if zeep is missing,
# but obviously the SOAP calls will fail until it's installed.
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


def _build_apikey_header(api_key: str):
    """Build the <ApiKey xmlns=...>...</ApiKey> SOAP header element."""
    el = etree.Element("{%s}ApiKey" % _APIKEY_NAMESPACE)
    el.text = api_key
    return [el]


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

    PROJECT_WSDL = "ServerProjectService.svc?wsdl"
    FILEMGR_WSDL = "FileManagerService.svc?wsdl"

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

        # Build a shared requests session for zeep (handles SSL + timeouts)
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
            # ListProjects(filter) — pass an empty filter struct to get all
            ProjectFilter = self._project_client.get_type(
                "{http://kilgray.com/memoqservices/2007/projects}"
                "ServerProjectListFilter"
            )
            flt = ProjectFilter()
            res = self._project_client.service.ListProjects(
                filter=flt, _soapheaders=self._hdr()
            )
        except Exception:
            # Older WSDLs use no-arg ListProjects
            try:
                res = self._project_client.service.ListProjects(
                    _soapheaders=self._hdr()
                )
            except Exception as e:
                raise RuntimeError(f"ListProjects failed: {e}") from e

        items: List[Dict] = []
        for p in (res or []):
            d = self._zeep_to_dict(p)
            # Try to surface common fields up to top level for UI convenience
            items.append({
                "ServerProjectGuid": d.get("ServerProjectGuid"),
                "Name": d.get("Name"),
                "SourceLanguageCode": d.get("SourceLanguageCode"),
                "TargetLanguageCodes": d.get("TargetLanguageCodes"),
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

    def list_documents(self, project_guid: str) -> List[Dict]:
        """
        List translation documents in a project. Returns dicts with:
          DocumentGuid, DocumentName, TargetLangCode, WorkflowStatus, Version
        """
        try:
            res = self._project_client.service.ListProjectTranslationDocuments2(
                serverProjectGuid=project_guid, _soapheaders=self._hdr()
            )
        except AttributeError:
            res = self._project_client.service.ListProjectTranslationDocuments(
                serverProjectGuid=project_guid, _soapheaders=self._hdr()
            )
        except Exception as e:
            raise RuntimeError(
                f"ListProjectTranslationDocuments failed: {e}"
            ) from e

        items: List[Dict] = []
        for d in (res or []):
            dd = self._zeep_to_dict(d)
            items.append({
                "DocumentGuid": dd.get("DocumentGuid"),
                "DocumentName": dd.get("DocumentName") or dd.get("Name"),
                "TargetLangCode": dd.get("TargetLangCode"),
                "WorkflowStatus": dd.get("WorkflowStatus"),
                "Version": dd.get("Version"),
                "TotalRowCount": dd.get("TotalRowCount"),
                "ConfirmedRowCount": dd.get("ConfirmedRowCount"),
                "_raw": dd,
            })
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
        Export the document as a bilingual XLIFF.

        Returns:
            (xliff_bytes, suggested_filename) — xliff_bytes is the inner
            document.mqxliff if the server returned an .mqxlz ZIP, otherwise
            the raw XLIFF.
        """
        # Build BilingualExportOptions (XLIFF, compressed if skeleton)
        try:
            ExportOptions = self._project_client.get_type(
                "{http://schemas.datacontract.org/2004/07/MemoQServices}"
                "BilingualExportOptions"
            )
            FormatEnum = self._project_client.get_type(
                "{http://schemas.datacontract.org/2004/07/MemoQServices}"
                "BilingualDocFormat"
            )
            opts = ExportOptions(
                BilingualDocFormat=FormatEnum("XLIFF"),
                IncludeSkeleton=include_skeleton,
                SaveCompressed=include_skeleton,  # required if skeleton=True
                IncludeFullVersionHistory=False,
                FillInUnconfirmedTranslations=False,
            )
            file_guid = self._project_client.service.ExportTranslationDocument(
                serverProjectGuid=project_guid,
                documentGuid=document_guid,
                exportOptions=opts,
                _soapheaders=self._hdr(),
            )
        except Exception as e:
            raise RuntimeError(f"ExportTranslationDocument failed: {e}") from e

        # Download the file by GUID (chunked)
        raw = self._download_file(file_guid)

        # If it's a .mqxlz (ZIP), extract document.mqxliff
        suggested_name = "document.mqxliff"
        if raw[:2] == b"PK":
            try:
                with zipfile.ZipFile(io.BytesIO(raw)) as zf:
                    name_priority = [
                        n for n in zf.namelist()
                        if n.lower().endswith(".mqxliff")
                    ] or [
                        n for n in zf.namelist()
                        if n.lower().endswith((".xliff", ".xlf"))
                    ]
                    if not name_priority:
                        raise RuntimeError(
                            "mqxlz package contains no .mqxliff/.xliff entry"
                        )
                    inner = name_priority[0]
                    suggested_name = inner.split("/")[-1]
                    raw = zf.read(inner)
            except zipfile.BadZipFile as e:
                raise RuntimeError(
                    f"Bilingual export was ZIP-magic but unreadable: {e}"
                ) from e

        logger.info(
            "Bilingual export OK — %d bytes (%s)", len(raw), suggested_name
        )
        return raw, suggested_name

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
        Calls UpdateTranslationDocumentFromBilingual(... "XLIFF").

        Returns the new document version number (if available), else None.
        """
        upload_guid = self._upload_file(xliff_bytes, filename=filename)

        try:
            self._project_client.service.UpdateTranslationDocumentFromBilingual(
                serverProjectGuid=project_guid,
                documentGuid=document_guid,
                fileGuid=upload_guid,
                bilingualDocFormat="XLIFF",
                _soapheaders=self._hdr(),
            )
        except TypeError:
            # Some older WSDLs use positional params
            self._project_client.service.UpdateTranslationDocumentFromBilingual(
                project_guid, document_guid, upload_guid, "XLIFF",
                _soapheaders=self._hdr(),
            )
        except Exception as e:
            raise RuntimeError(
                f"UpdateTranslationDocumentFromBilingual failed: {e}"
            ) from e

        # Try to read back the new version number
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
        try:
            self._file_client.service.BeginChunkedFileDownload(
                fileGuid=file_guid, _soapheaders=self._hdr()
            )
        except Exception as e:
            raise RuntimeError(f"BeginChunkedFileDownload failed: {e}") from e

        buf = io.BytesIO()
        try:
            while True:
                chunk = self._file_client.service.GetNextFileChunk(
                    fileGuid=file_guid,
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
                    fileGuid=file_guid, _soapheaders=self._hdr()
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

        view = memoryview(data)
        offset = 0
        try:
            while offset < len(view):
                chunk = bytes(view[offset:offset + self.CHUNK_SIZE])
                self._file_client.service.AddNextFileChunk(
                    fileGuid=file_guid,
                    fileChunk=chunk,
                    _soapheaders=self._hdr(),
                )
                offset += len(chunk)
            self._file_client.service.EndChunkedFileUpload(
                fileGuid=file_guid, _soapheaders=self._hdr()
            )
        except Exception as e:
            raise RuntimeError(
                f"Chunked upload failed at offset {offset}: {e}"
            ) from e

        return str(file_guid)
