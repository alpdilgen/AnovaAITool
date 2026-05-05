import streamlit as st
import pandas as pd
import time
import re
from datetime import datetime
from openai import AuthenticationError
from services.prompt_builder import PromptBuilder
from services.ai_translator import AITranslator
from services.caching import CacheManager
from services.doc_analyzer import DocumentAnalyzer, PromptGenerator
from services.embedding_matcher import EmbeddingMatcher, get_embedding_cost_estimate
from utils.xml_parser import XMLParser
from utils.logger import TransactionLogger
import config
from services.memoq_server_client import MemoQServerClient
from services.memoq_ui import MemoQUI
from analysis_screen import show_analysis_screen
from verifika_screen import show_verifika_tab
# --- Setup ---
st.set_page_config(page_title=config.APP_NAME, layout="wide", page_icon="🌍")

# Session state initialization
if 'translation_results' not in st.session_state:
    st.session_state.translation_results = {}
if 'segment_objects' not in st.session_state:
    st.session_state.segment_objects = {}
if 'translation_log' not in st.session_state:
    st.session_state.translation_log = ""
if 'tm_info' not in st.session_state:
    st.session_state.tm_info = None
if 'bypass_stats' not in st.session_state:
    st.session_state.bypass_stats = {'bypassed': 0, 'llm_sent': 0}
if 'detected_languages' not in st.session_state:
    st.session_state.detected_languages = {'source': None, 'target': None}
if 'chat_history' not in st.session_state:
    st.session_state.chat_history = []
# Prompt Builder state
if 'generated_prompt' not in st.session_state:
    st.session_state.generated_prompt = None
if 'prompt_metadata' not in st.session_state:
    st.session_state.prompt_metadata = {}
if 'use_generated_prompt' not in st.session_state:
    st.session_state.use_generated_prompt = False
if 'uploaded_prompt_template' not in st.session_state:
    st.session_state.uploaded_prompt_template = None
# Reference file state
if 'reference_chunks' not in st.session_state:
    st.session_state.reference_chunks = []
if 'embedding_matcher' not in st.session_state:
    st.session_state.embedding_matcher = None
if 'reference_embeddings_ready' not in st.session_state:
    st.session_state.reference_embeddings_ready = False
# DNT (Do Not Translate) list
if 'dnt_terms' not in st.session_state:
    st.session_state.dnt_terms = []

# memoQ Server state
if 'memoq_server_url' not in st.session_state:
    st.session_state.memoq_server_url = "https://mirage.memoq.com:9091/adaturkey"
def _get_secret(key: str) -> str:
    try:
        return (st.secrets.get(key) or "") if hasattr(st, "secrets") else ""
    except Exception:
        return ""

if 'memoq_username' not in st.session_state:
    st.session_state.memoq_username = _get_secret("memoq_username")
if 'memoq_password' not in st.session_state:
    st.session_state.memoq_password = _get_secret("memoq_password")
if 'memoq_api_key' not in st.session_state:
    st.session_state.memoq_api_key = _get_secret("memoq_api_key")
if 'memoq_verify_ssl' not in st.session_state:
    st.session_state.memoq_verify_ssl = False
if 'memoq_connected' not in st.session_state:
    st.session_state.memoq_connected = False
if 'memoq_client' not in st.session_state:
    st.session_state.memoq_client = None
if 'memoq_project_service' not in st.session_state:
    st.session_state.memoq_project_service = None
if 'memoq_projects_list' not in st.session_state:
    st.session_state.memoq_projects_list = []
if 'memoq_selected_project_guid' not in st.session_state:
    st.session_state.memoq_selected_project_guid = None
if 'memoq_selected_project_name' not in st.session_state:
    st.session_state.memoq_selected_project_name = None
if 'memoq_documents_list' not in st.session_state:
    st.session_state.memoq_documents_list = []
if 'memoq_selected_document_guid' not in st.session_state:
    st.session_state.memoq_selected_document_guid = None
if 'memoq_selected_document_name' not in st.session_state:
    st.session_state.memoq_selected_document_name = None
if 'memoq_selected_target_lang' not in st.session_state:
    st.session_state.memoq_selected_target_lang = None
if 'last_xliff_bytes' not in st.session_state:
    st.session_state.last_xliff_bytes = None
if 'last_xliff_filename' not in st.session_state:
    st.session_state.last_xliff_filename = None
if 'selected_tm_guids' not in st.session_state:
    st.session_state.selected_tm_guids = []
if 'selected_tb_guids' not in st.session_state:
    st.session_state.selected_tb_guids = []
if 'memoq_tms_list' not in st.session_state:
    st.session_state.memoq_tms_list = []
if 'memoq_tbs_list' not in st.session_state:
    st.session_state.memoq_tbs_list = []

if 'batch_size' not in st.session_state:
    st.session_state.batch_size = 20
if 'segment_match_scores' not in st.session_state:
    st.session_state.segment_match_scores = {}

# --- Sidebar ---
with st.sidebar:
    st.title("⚙️ Configuration")

    # Language Settings
    st.subheader("🌐 Languages")

    # Raw detected codes from MQXLIFF (e.g., "en-us", "tr")
    raw_detected_src = st.session_state.detected_languages.get('source')
    raw_detected_tgt = st.session_state.detected_languages.get('target')

    if raw_detected_src and raw_detected_tgt:
        # Languages detected: show read-only with display names
        src_display = config.get_language_display_name(raw_detected_src)
        tgt_display = config.get_language_display_name(raw_detected_tgt)

        st.text_input("Source Language", value=f"{src_display}", disabled=True)
        st.text_input("Target Language", value=f"{tgt_display}", disabled=True)
        st.caption(f"🔍 Auto-detected: {raw_detected_src} → {raw_detected_tgt}")

        # Store raw codes — these will be used for TM/TB API calls
        src_code = raw_detected_src.lower()
        tgt_code = raw_detected_tgt.lower()

        # Also store 3-letter equivalents for backward compatibility
        src_code_3letter = config.convert_detected_lang(raw_detected_src) if raw_detected_src else 'eng'
        tgt_code_3letter = config.convert_detected_lang(raw_detected_tgt) if raw_detected_tgt else 'tur'
        # Keep base 3-letter for SUPPORTED_LANGUAGES lookup
        src_code_3letter_base = src_code_3letter.split('-')[0] if src_code_3letter else 'eng'
        tgt_code_3letter_base = tgt_code_3letter.split('-')[0] if tgt_code_3letter else 'tur'
    else:
        # No file uploaded yet: show empty disabled fields
        st.text_input("Source Language", value="", disabled=True, placeholder="Upload a file to detect")
        st.text_input("Target Language", value="", disabled=True, placeholder="Upload a file to detect")
        st.caption("📄 Upload a file to auto-detect languages")

        # Default codes until file is uploaded
        src_code = 'eng'
        tgt_code = 'tur'
        src_code_3letter = 'eng'
        tgt_code_3letter = 'tur'
        src_code_3letter_base = 'eng'
        tgt_code_3letter_base = 'tur'

    st.divider()

    # AI Settings
    st.subheader("🤖 AI Settings")
    # Pre-fill from Streamlit secrets if configured.
    # Secrets format (in .streamlit/secrets.toml or Streamlit Cloud secrets):
    #     openai_api_key = "sk-..."
    _default_api_key = ""
    try:
        if hasattr(st, "secrets") and "openai_api_key" in st.secrets:
            _default_api_key = st.secrets["openai_api_key"]
    except Exception:
        pass
    api_key = st.text_input(
        "API Key",
        type="password",
        value=_default_api_key,
        help="Pre-filled from Streamlit secrets if `openai_api_key` is set; otherwise paste here.",
    )
    model = st.selectbox("Model", config.OPENAI_MODELS)

    st.divider()

    # TM Settings
    st.subheader("📚 TM Settings")

    acceptance_threshold = st.slider(
        "TM Acceptance Threshold",
        min_value=70,
        max_value=100,
        value=config.DEFAULT_ACCEPTANCE_THRESHOLD,
        help="Matches ≥ this value bypass LLM (direct TM usage)"
    )

    match_threshold = st.slider(
        "TM Match Threshold",
        min_value=50,
        max_value=100,
        value=config.DEFAULT_MATCH_THRESHOLD,
        help="Matches ≥ this value are sent as context to LLM"
    )

    if acceptance_threshold <= match_threshold:
        st.warning("Acceptance should be higher than Match threshold")

    st.divider()

    # Chat History Settings
    st.subheader("💬 Chat History")
    chat_history_length = st.slider(
        "Previous batches to include",
        min_value=0,
        max_value=10,
        value=config.DEFAULT_CHAT_HISTORY,
        help="Number of previous translation batches to include for consistency"
    )

    st.divider()

    # Batch Size Settings
    st.subheader("📦 Batch Processing")
    batch_size = st.slider(
        "Batch Size",
        min_value=5,
        max_value=50,
        value=st.session_state.batch_size,
        step=5,
        help="Number of segments per batch sent to LLM"
    )
    st.session_state.batch_size = batch_size

    st.divider()

# ==================== memoQ SERVER CONNECTION ====================
    st.divider()
    st.subheader("🔗 memoQ Server")

    with st.form("memoq_connection_form"):
        memoq_url = st.text_input(
            "Server URL",
            value=st.session_state.memoq_server_url,
            help="memoQ Server base URL",
            key="memoq_url_input"
        )

        memoq_user = st.text_input(
            "Username",
            value=st.session_state.memoq_username,
            key="memoq_user_input"
        )

        memoq_pass = st.text_input(
            "Password",
            type="password",
            value=st.session_state.memoq_password,
            key="memoq_pass_input"
        )

        memoq_api_key = st.text_input(
            "WSAPI API key",
            type="password",
            value=st.session_state.get('memoq_api_key', ''),
            help="API key for SOAP/WSAPI (project list, bilingual export/import)",
            key="memoq_api_key_input"
        )

        memoq_ssl = st.checkbox(
            "Verify SSL",
            value=st.session_state.memoq_verify_ssl,
            help="Disable for self-signed certificates"
        )

        memoq_connect = st.form_submit_button("🔐 Connect", width="stretch")

    if memoq_connect:
        st.session_state.memoq_server_url = memoq_url
        st.session_state.memoq_username = memoq_user
        st.session_state.memoq_password = memoq_pass
        st.session_state.memoq_api_key = memoq_api_key
        st.session_state.memoq_verify_ssl = memoq_ssl

        try:
            client = MemoQServerClient(
                server_url=memoq_url,
                username=memoq_user,
                password=memoq_pass,
                verify_ssl=memoq_ssl
            )
            client.login()
            st.session_state.memoq_client = client
            st.session_state.memoq_connected = True

            # Initialize SOAP project service for bilingual round-trip
            try:
                from services.memoq_project_service import MemoQProjectService
                soap_base = memoq_url.rstrip('/')
                if not soap_base.endswith('/memoqservices'):
                    soap_base = soap_base + '/memoqservices'
                if memoq_api_key:
                    st.session_state.memoq_project_service = MemoQProjectService(
                        server_url=soap_base,
                        api_key=memoq_api_key,
                        verify_ssl=memoq_ssl,
                    )
                else:
                    st.session_state.memoq_project_service = None
                    st.warning("WSAPI API key not provided — project picker disabled.")
            except Exception as soap_err:
                st.session_state.memoq_project_service = None
                st.warning(f"SOAP project service unavailable: {soap_err}")

            st.success("✓ Connected to memoQ Server")
        except Exception as e:
            st.error(f"Connection failed: {str(e)}")
            st.session_state.memoq_connected = False
            st.session_state.memoq_client = None
            st.session_state.memoq_project_service = None

    if st.session_state.memoq_connected and st.session_state.memoq_client:
        st.success("✓ Connected to memoQ Server")
        if st.button("🔌 Disconnect", width="stretch"):
            st.session_state.memoq_connected = False
            st.session_state.memoq_client = None
            st.session_state.memoq_project_service = None
            st.rerun()

    # Show if using generated prompt
    if st.session_state.use_generated_prompt and st.session_state.generated_prompt:
        st.divider()
        st.success("✨ Using generated prompt")
        if st.button("❌ Clear Generated Prompt"):
            st.session_state.use_generated_prompt = False
            st.session_state.generated_prompt = None
            st.rerun()


# --- Helper Functions ---

def parse_reference_file(content: bytes, filename: str) -> list:
    """
    Parse reference file (target-only text) into chunks for style reference.
    Supports TXT, DOCX, PDF, HTML, RTF, and Excel formats.

    Returns list of text chunks (sentences/paragraphs).
    """
    chunks = []
    filename_lower = filename.lower()

    try:
        # === TXT ===
        if filename_lower.endswith('.txt'):
            text = None
            for encoding in ['utf-8', 'utf-8-sig', 'utf-16', 'latin-1', 'cp1252', 'iso-8859-9']:
                try:
                    text = content.decode(encoding)
                    break
                except Exception:
                    continue

            if text:
                text = text.replace('\r\n', '\n').replace('\r', '\n')
                lines = [line.strip() for line in text.split('\n') if line.strip()]

                import re
                for line in lines:
                    clean_line = re.sub(r'\d+$', '', line).strip()
                    if clean_line and len(clean_line) > 15:
                        chunks.append(clean_line)

        # === DOCX ===
        elif filename_lower.endswith('.docx'):
            from docx import Document
            import io
            doc = Document(io.BytesIO(content))
            for para in doc.paragraphs:
                text = para.text.strip()
                if text and len(text) > 15:
                    chunks.append(text)

        # === PDF ===
        elif filename_lower.endswith('.pdf'):
            import io
            try:
                import pdfplumber
                with pdfplumber.open(io.BytesIO(content)) as pdf:
                    for page in pdf.pages:
                        text = page.extract_text()
                        if text:
                            lines = [line.strip() for line in text.split('\n') if line.strip()]
                            for line in lines:
                                if len(line) > 15:
                                    chunks.append(line)
            except ImportError:
                st.warning("PDF support requires pdfplumber: pip install pdfplumber")

        # === HTML ===
        elif filename_lower.endswith(('.html', '.htm')):
            try:
                from bs4 import BeautifulSoup
                text = None
                for encoding in ['utf-8', 'utf-8-sig', 'latin-1', 'cp1252']:
                    try:
                        text = content.decode(encoding)
                        break
                    except Exception:
                        continue

                if text:
                    soup = BeautifulSoup(text, 'html.parser')
                    # Remove script and style elements
                    for element in soup(['script', 'style', 'head', 'meta', 'link']):
                        element.decompose()

                    # Get text from paragraphs, divs, list items, etc.
                    for tag in soup.find_all(['p', 'div', 'li', 'td', 'th', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6']):
                        text = tag.get_text(strip=True)
                        if text and len(text) > 15:
                            chunks.append(text)
            except ImportError:
                st.warning("HTML support requires beautifulsoup4: pip install beautifulsoup4")

        # === RTF ===
        elif filename_lower.endswith('.rtf'):
            try:
                from striprtf.striprtf import rtf_to_text
                text = rtf_to_text(content.decode('latin-1', errors='ignore'))
                if text:
                    text = text.replace('\r\n', '\n').replace('\r', '\n')
                    lines = [line.strip() for line in text.split('\n') if line.strip()]
                    for line in lines:
                        if len(line) > 15:
                            chunks.append(line)
            except ImportError:
                st.warning("RTF support requires striprtf: pip install striprtf")

        # === Excel (XLSX, XLS) ===
        elif filename_lower.endswith(('.xlsx', '.xls')):
            import io
            try:
                df = pd.read_excel(io.BytesIO(content), header=None)
                # Iterate through all cells
                for col in df.columns:
                    for value in df[col]:
                        if pd.notna(value):
                            text = str(value).strip()
                            if text and len(text) > 15:
                                # Skip if it's just a number
                                try:
                                    float(text.replace(',', '.'))
                                    continue
                                except Exception:
                                    chunks.append(text)
            except Exception as e:
                st.warning(f"Excel parsing error: {e}")

    except Exception as e:
        st.warning(f"Error parsing reference file: {e}")

    # Remove duplicates while preserving order
    seen = set()
    unique_chunks = []
    for chunk in chunks:
        if chunk not in seen:
            seen.add(chunk)
            unique_chunks.append(chunk)

    return unique_chunks


def get_reference_samples(chunks: list, batch_num: int, samples_per_batch: int = 5, max_chars: int = 1500) -> str:
    """
    Get reference samples for a batch using rotating selection.

    Args:
        chunks: List of reference text chunks
        batch_num: Current batch number (for rotation)
        samples_per_batch: How many samples to include
        max_chars: Maximum total characters for all samples

    Returns:
        Formatted string of reference samples
    """
    if not chunks:
        return ""

    # Rotating selection - different chunks for each batch
    total_chunks = len(chunks)
    start_idx = (batch_num * samples_per_batch) % total_chunks

    selected = []
    total_len = 0

    for i in range(samples_per_batch):
        idx = (start_idx + i) % total_chunks
        chunk = chunks[idx]

        # Truncate long chunks
        if len(chunk) > 300:
            chunk = chunk[:300] + "..."

        if total_len + len(chunk) > max_chars:
            break

        selected.append(chunk)
        total_len += len(chunk)

    if not selected:
        return ""

    return "\n".join(f"• {s}" for s in selected)


def parse_dnt_file(content: bytes, filename: str) -> list:
    """
    Parse Do Not Translate / Forbidden Terms file.
    Supports TXT and CSV formats.

    Returns list of terms that should not be translated.
    """
    terms = []
    filename_lower = filename.lower()

    try:
        if filename_lower.endswith('.txt'):
            text = None
            for encoding in ['utf-8', 'utf-8-sig', 'latin-1', 'cp1252']:
                try:
                    text = content.decode(encoding)
                    break
                except Exception:
                    continue

            if text:
                for line in text.split('\n'):
                    line = line.strip()
                    # Skip empty lines and comments
                    if line and not line.startswith('#'):
                        terms.append(line)

        elif filename_lower.endswith('.csv'):
            text = None
            for encoding in ['utf-8', 'utf-8-sig', 'latin-1', 'cp1252']:
                try:
                    text = content.decode(encoding)
                    break
                except Exception:
                    continue

            if text:
                for line in text.split('\n'):
                    line = line.strip()
                    if line and not line.startswith('#'):
                        # Take first column
                        parts = line.split(',')
                        term = parts[0].strip().strip('"').strip("'")
                        # Skip header-like entries
                        if term.lower() not in ['term', 'forbidden', 'dnt', 'do not translate', 'source']:
                            if term:
                                terms.append(term)

    except Exception as e:
        st.warning(f"Error parsing DNT file: {e}")

    # Remove duplicates while preserving order
    seen = set()
    unique_terms = []
    for term in terms:
        if term not in seen:
            seen.add(term)
            unique_terms.append(term)

    return unique_terms


def apply_tm_to_segment(source_with_tags: str, tm_translation: str) -> str:
    """Apply TM translation while preserving source tags ({{N}}, <ph>, <bpt>, <ept>, etc.)."""
    import re

    # Detect ALL tag types: {{N}}, <ph>, <bpt>, <ept>, etc.
    all_tags = re.findall(r'(\{\{\d+\}\}|<[a-z]+[^>]*/?[^>]*/?>)', source_with_tags)

    if not all_tags:
        return tm_translation

    # Check if tags already in translation
    if '{{' in tm_translation or '<' in tm_translation:
        return tm_translation

    # Extract leading tags (beginning of source)
    leading_match = re.match(r'^((?:\{\{\d+\}\}|<[a-z]+[^>]*/?[^>]*/?>))+', source_with_tags)
    if leading_match:
        leading_tags = leading_match.group()
        if not tm_translation.startswith(leading_tags):
            tm_translation = leading_tags + tm_translation

    # Extract trailing tags (end of source)
    trailing_match = re.search(r'((?:\{\{\d+\}\}|<[a-z]+[^>]*/?[^>]*/?>))+$', source_with_tags)
    if trailing_match:
        trailing_tags = trailing_match.group()
        if not tm_translation.endswith(trailing_tags):
            tm_translation = tm_translation + trailing_tags

    return tm_translation


def get_chat_history_context(history: list, max_items: int) -> list:
    """Get recent translation history for context."""
    if not history or max_items <= 0:
        return []
    return history[-max_items:]


def normalize_segment_for_matching(source_text: str) -> str:
    """Remove XML/inline tags for TM matching"""
    import re

    # Remove all inline tags: <ph>, <bpt>, <ept>, {{N}}, etc.
    normalized = re.sub(r'<[^>]+>', '', source_text)  # XML tags
    normalized = re.sub(r'\{\{[^}]+\}\}', '', normalized)  # {{1}} style tags

    # Clean up extra spaces
    normalized = re.sub(r'\s+', ' ', normalized).strip()

    return normalized


# --- Main Translation Logic ---

def process_translation(xliff_bytes, tmx_bytes, csv_bytes, custom_prompt_content=None, memoq_tm_guids=None, memoq_tb_guids=None):
    start_time = time.time()  # Track total processing duration
    batch_size = st.session_state.batch_size

    # Initialize match scores tracking for memoQ metadata
    match_scores = {}

    if not api_key:
        st.error("Please provide an API Key.")
        return

    with st.status("Processing...", expanded=True) as status:

        # 1. Parse XLIFF
        st.write("📄 Parsing XLIFF...")
        segments = XMLParser.parse_xliff(xliff_bytes)
        total_segments = len(segments)
        st.write(f"✅ Loaded {total_segments} segments")

        if total_segments == 0:
            st.error(
                "⚠️ No translatable segments found in the exported document. "
                "The bilingual export may have failed or the document has no active segments."
            )
            return

        st.session_state.segment_objects = {seg.id: seg for seg in segments}
        st.session_state.chat_history = []

        # Initialize Logger
        logger = TransactionLogger()
        logger.log(f"Started translation job for {total_segments} segments.")
        logger.log(f"Source: {src_code} | Target: {tgt_code} | Model: {model}")
        logger.log(f"TM Acceptance: ≥{acceptance_threshold}% | TM Match: ≥{match_threshold}%")
        logger.log(f"Chat History Length: {chat_history_length}")

        if st.session_state.reference_chunks:
            logger.log(f"Reference file: {len(st.session_state.reference_chunks)} style samples loaded")

        if st.session_state.use_generated_prompt:
            logger.log("Using generated prompt from Prompt Builder")

        # 2. Initialize memoQ Server client if TMs/TBs selected
        memoq_client = None
        if memoq_tm_guids or memoq_tb_guids:
            try:
                if st.session_state.get('memoq_client'):
                    memoq_client = st.session_state.memoq_client
                    st.write(f"🔗 Using memoQ Server TM/TB resources")
                    if memoq_tm_guids:
                        st.write(f"   • {len(memoq_tm_guids)} Translation Memory(ies)")
                    if memoq_tb_guids:
                        st.write(f"   • {len(memoq_tb_guids)} Termbase(s)")
                    logger.log(f"memoQ Server: {len(memoq_tm_guids)} TMs, {len(memoq_tb_guids)} TBs")
            except Exception as e:
                st.warning(f"Could not connect to memoQ Server: {str(e)}")
                logger.log(f"memoQ connection error: {e}")

        # 4. Initialize Prompt Builder
        # Priority: Generated prompt > Custom file > Default
        if st.session_state.use_generated_prompt and st.session_state.generated_prompt:
            prompt_builder = PromptBuilder(custom_template=st.session_state.generated_prompt)
            logger.log("Using generated prompt template from Prompt Builder.")
        elif custom_prompt_content:
            prompt_builder = PromptBuilder(custom_template=custom_prompt_content)
            logger.log("Using custom prompt template from file.")
        else:
            prompt_builder = PromptBuilder(template_path=config.PROMPT_TEMPLATE_PATH)
            logger.log("Using default prompt template.")

        translator = AITranslator("OpenAI", api_key, model)

        status.update(label="Analyzing segments...", state="running")

        # 5. Analyze segments
        bypass_segments = []
        llm_segments = []
        final_translations = {}
        tm_context = {}
        tb_context = {}

        st.write("🔍 Analyzing TM matches...")
        analysis_progress = st.progress(0)

        # STEP A: Handle tag-only segments first
        segments_needing_tm = []
        for seg in segments:
            text_only = re.sub(r'<[^>]+>|\{\{\d+\}\}', '', seg.source).strip()
            if not text_only:
                final_translations[seg.id] = seg.source
                match_scores[seg.id] = 100
                bypass_segments.append(seg)
            else:
                segments_needing_tm.append(seg)

        # STEP B: memoQ Server TM batch lookup
        segments_for_memoq = [s for s in segments_needing_tm if s.id not in {s2.id for s2 in bypass_segments}]

        BATCH_SIZE = batch_size

        # Build segment index for context lookup (preceding/following segments)
        _seg_index_map = {seg.id: i for i, seg in enumerate(segments)}

        def _build_context_for_batch(batch_segs):
            """Build context_info list for a batch, using original segment order."""
            ctx_list = []
            for seg in batch_segs:
                ctx = {}
                orig_idx = _seg_index_map.get(seg.id)
                if orig_idx is not None:
                    if orig_idx > 0:
                        prev_src = normalize_segment_for_matching(segments[orig_idx - 1].source)
                        if prev_src:
                            ctx["preceding"] = prev_src
                    if orig_idx < len(segments) - 1:
                        next_src = normalize_segment_for_matching(segments[orig_idx + 1].source)
                        if next_src:
                            ctx["following"] = next_src
                ctx_list.append(ctx)
            return ctx_list

        if memoq_client and memoq_tm_guids and segments_for_memoq:
            for tm_guid in memoq_tm_guids:
                remaining = [s for s in segments_for_memoq if s.id not in {s2.id for s2 in bypass_segments} or s.id in tm_context]

                for batch_start in range(0, len(remaining), BATCH_SIZE):
                    batch = remaining[batch_start:batch_start + BATCH_SIZE]
                    normalized_sources = [normalize_segment_for_matching(s.source) for s in batch]
                    batch_context = _build_context_for_batch(batch)

                    try:
                        results = memoq_client.lookup_segments(
                            tm_guid, normalized_sources,
                            match_threshold=match_threshold,
                            src_lang=src_code, tgt_lang=tgt_code,
                            context_info=batch_context
                        )

                        if results:
                            for idx, seg in enumerate(batch):
                                if idx in results:
                                    tm_hits = results[idx]
                                    if tm_hits:
                                        best_hit = tm_hits[0]
                                        score = best_hit.similarity

                                        if score >= acceptance_threshold:
                                            bypass_segments.append(seg)
                                            final_translations[seg.id] = best_hit.target_text
                                            match_scores[seg.id] = score
                                        elif score >= match_threshold:
                                            # Only update if memoQ has better match than local TM
                                            existing_score = match_scores.get(seg.id, 0)
                                            if score > existing_score:
                                                tm_context[seg.id] = tm_hits
                                                match_scores[seg.id] = score
                    except Exception as e:
                        logger.log(f"memoQ TM batch lookup error: {e}")

                    progress = 0.3 + (batch_start + len(batch)) / max(len(remaining), 1) * 0.3
                    analysis_progress.progress(min(progress, 0.6))

        # STEP D: Determine which segments go to LLM
        bypassed_ids = {s.id for s in bypass_segments}
        for seg in segments_needing_tm:
            if seg.id not in bypassed_ids:
                llm_segments.append(seg)
                if seg.id not in match_scores:
                    match_scores[seg.id] = 0

        # STEP E: memoQ TB lookup
        if memoq_client and memoq_tb_guids:
            import json as _json
            import re as _re
            from models.entities import TermMatch

            all_segment_sources = [s.source for s in segments_needing_tm]
            logger.log(f"TB Lookup: {len(memoq_tb_guids)} TB(s), {len(all_segment_sources)} segments, src={src_code}, tgt={tgt_code}")

            for tb_idx, tb_guid in enumerate(memoq_tb_guids):
                logger.log(f"  TB [{tb_idx+1}/{len(memoq_tb_guids)}]: {tb_guid}")

                # --- Step 0: Get TB metadata to discover actual language codes ---
                tb_src_lang = src_code  # fallback
                tb_tgt_lang = tgt_code  # fallback
                try:
                    tb_info = memoq_client._make_request("GET", f"/tbs/{tb_guid}")
                    tb_languages = []
                    if isinstance(tb_info, dict):
                        tb_languages = tb_info.get('Languages', [])
                    if tb_languages and isinstance(tb_languages, list):
                        _2to3 = config.ISO_TO_MEMOQ_LANG
                        _3to2 = {v.lower(): k for k, v in _2to3.items()}

                        def _get_base_codes(code):
                            """Get all equivalent base codes: en→{en,eng}, eng→{eng,en}"""
                            base = code.lower().split('-')[0]
                            codes = {base}
                            if base in _2to3:
                                codes.add(_2to3[base].lower())
                            if base in _3to2:
                                codes.add(_3to2[base])
                            return codes

                        src_bases = _get_base_codes(src_code)
                        tgt_bases = _get_base_codes(tgt_code)

                        for tb_lang in tb_languages:
                            tl_base = tb_lang.lower().split('-')[0]
                            if tl_base in src_bases:
                                tb_src_lang = tb_lang
                            elif tl_base in tgt_bases:
                                tb_tgt_lang = tb_lang
                    logger.log(f"  TB lang mapping: src={src_code} → {tb_src_lang}, tgt={tgt_code} → {tb_tgt_lang}")
                except Exception as e:
                    logger.log(f"  TB lang detection error: {type(e).__name__}: {str(e)[:200]}")

                # --- Step 1: Extract unique n-grams from all segments ---
                all_ngrams = set()
                segment_words = {}
                for seg_i, src in enumerate(all_segment_sources):
                    clean = _re.sub(r'<[^>]+>', '', src)
                    clean = _re.sub(r'\{\{[^}]+\}\}', '', clean)
                    clean = _re.sub(r'\s+', ' ', clean).strip()
                    segment_words[seg_i] = clean.lower()
                    words = clean.split()
                    for n in range(1, 6):
                        for i in range(len(words) - n + 1):
                            ngram = ' '.join(words[i:i+n])
                            ngram = ngram.strip('.,;:!?()[]{}"\'-—–')
                            if len(ngram) > 3 and not ngram.isdigit():
                                all_ngrams.add(ngram.lower())

                short_ngrams = sorted([ng for ng in all_ngrams if len(ng.split()) <= 3], key=len)
                long_ngrams = sorted([ng for ng in all_ngrams if len(ng.split()) > 3], key=len, reverse=True)
                search_ngrams = short_ngrams + long_ngrams
                search_ngrams = search_ngrams[:500]
                for seg_i, src in enumerate(all_segment_sources):
                    clean = _re.sub(r'<[^>]+>', '', src)
                    clean = _re.sub(r'\{\{[^}]+\}\}', '', clean)
                    clean = _re.sub(r'\s+', ' ', clean).strip()
                    if clean and clean.lower() not in all_ngrams:
                        search_ngrams.append(clean)
                logger.log(f"  Extracted {len(all_ngrams)} unique n-grams, searching {len(search_ngrams)} items (incl. full segments)")

                # --- Step 2: Use lookupterms with TB's actual language codes ---
                found_terms = []
                seen_pairs = set()

                ngram_batch_size = 50
                for batch_start in range(0, len(search_ngrams), ngram_batch_size):
                    batch = search_ngrams[batch_start:batch_start + ngram_batch_size]
                    seg_list = [f"<seg>{ng}</seg>" for ng in batch]
                    payload = {
                        "SourceLanguage": tb_src_lang,
                        "TargetLanguage": tb_tgt_lang,
                        "Segments": seg_list
                    }
                    try:
                        result = memoq_client._make_request(
                            "POST", f"/tbs/{tb_guid}/lookupterms",
                            data=payload
                        )

                        result_list = []
                        if isinstance(result, dict):
                            result_list = result.get('Result', [])
                        elif isinstance(result, list):
                            result_list = result

                        for seg_idx, seg_result in enumerate(result_list):
                            if not isinstance(seg_result, dict):
                                continue
                            tb_hits = seg_result.get('TBHits', [])
                            if not isinstance(tb_hits, list):
                                continue
                            for hit_group in tb_hits:
                                if not isinstance(hit_group, list):
                                    continue
                                for hit in hit_group:
                                    if not isinstance(hit, dict):
                                        continue
                                    entry = hit.get('Entry', hit)
                                    languages = entry.get('Languages', [])
                                    if not isinstance(languages, list):
                                        continue
                                    src_terms = []
                                    tgt_terms = []
                                    for lang_entry in languages:
                                        if not isinstance(lang_entry, dict):
                                            continue
                                        lang_code = lang_entry.get('Language', '').lower()
                                        term_items = lang_entry.get('TermItems', [])
                                        if not isinstance(term_items, list):
                                            term_items = [term_items] if isinstance(term_items, dict) else []
                                        for ti in term_items:
                                            if not isinstance(ti, dict):
                                                continue
                                            txt = ti.get('Text', '').strip()
                                            if not txt or ti.get('IsForbidden', False):
                                                continue
                                            lc_base = lang_code.split('-')[0]
                                            if lang_code == tb_src_lang.lower() or lc_base in src_bases:
                                                src_terms.append(txt)
                                            elif lang_code == tb_tgt_lang.lower() or lc_base in tgt_bases:
                                                tgt_terms.append(txt)

                                    if not src_terms:
                                        st_val = hit.get('SourceTerm', '') or entry.get('SourceTerm', '')
                                        if st_val:
                                            src_terms = [st_val]
                                    if not tgt_terms:
                                        tt_val = hit.get('TargetTerm', '') or entry.get('TargetTerm', '')
                                        if tt_val:
                                            tgt_terms = [tt_val]

                                    for src_t in src_terms:
                                        for tt in tgt_terms:
                                            pair_key = (src_t.lower().strip(), tt.lower().strip())
                                            if pair_key not in seen_pairs and pair_key[0] and pair_key[1]:
                                                seen_pairs.add(pair_key)
                                                found_terms.append(TermMatch(
                                                    source=src_t.strip(), target=tt.strip(),
                                                    source_language=src_code,
                                                    target_language=tgt_code
                                                ))
                    except Exception as e:
                        logger.log(f"  TB batch error: {type(e).__name__}: {str(e)[:200]}")
                        continue

                logger.log(f"  TB search found {len(found_terms)} unique term pairs")
                for ft in found_terms[:20]:
                    logger.log(f"    '{ft.source}' → '{ft.target}'")
                if len(found_terms) > 20:
                    logger.log(f"    ... and {len(found_terms) - 20} more")

                # --- Step 3: Match found terms against each segment ---
                if found_terms:
                    for seg_i, seg in enumerate(segments_needing_tm):
                        seg_lower = segment_words.get(seg_i, seg.source.lower())
                        matching_terms = []
                        for ft in found_terms:
                            if ft.source.lower() in seg_lower:
                                matching_terms.append(ft)
                        if matching_terms:
                            existing = tb_context.get(seg.id, [])
                            tb_context[seg.id] = existing + matching_terms

                tb_matched_segs = sum(1 for sid in tb_context if tb_context[sid])
                logger.log(f"  TB total: {len(found_terms)} terms matched across {tb_matched_segs} segments")

            analysis_progress.progress(0.8)

        # STEP F: TB validation of bypassed segments
        if tb_context:
            from models.entities import TMMatch as _TMMatch, TermMatch as _TermMatch
            still_bypassed = []
            tb_demoted = 0

            for seg in bypass_segments:
                seg_tb_terms = tb_context.get(seg.id, [])
                if not seg_tb_terms:
                    still_bypassed.append(seg)
                    continue

                tm_translation = final_translations.get(seg.id, '')
                tm_translation_lower = tm_translation.lower()

                missing = []
                for term in seg_tb_terms:
                    if isinstance(term, _TermMatch):
                        target_base = term.target.lower().strip()
                    elif isinstance(term, dict):
                        target_base = term.get('target', '').lower().strip()
                    else:
                        continue
                    if target_base and target_base not in tm_translation_lower:
                        missing.append(target_base)

                if missing:
                    tm_hit = _TMMatch(
                        source_text=seg.source,
                        target_text=tm_translation,
                        similarity=match_scores.get(seg.id, acceptance_threshold),
                        match_type='FUZZY'
                    )
                    tm_context[seg.id] = [tm_hit]
                    del final_translations[seg.id]
                    llm_segments.append(seg)
                    tb_demoted += 1
                    logger.log(
                        f"TB demotion: [{seg.id}] TM match accepted but missing "
                        f"TB terms {missing} — sent to LLM for correction"
                    )
                else:
                    still_bypassed.append(seg)

            bypass_segments = still_bypassed
            if tb_demoted > 0:
                st.write(f"⚠️ **{tb_demoted}** TM-accepted segments demoted for TB term correction")
                logger.log(f"TB validation: {tb_demoted} segments demoted from TM bypass to LLM")

        analysis_progress.progress(1.0)

        st.session_state.bypass_stats = {
            'bypassed': len(bypass_segments),
            'llm_sent': len(llm_segments),
            'total_segments': total_segments
        }

        st.write(f"✅ **{len(bypass_segments)}** segments from TM (≥{acceptance_threshold}% match)")
        st.write(f"🔄 **{len(llm_segments)}** segments need LLM translation")

        logger.log(f"Analysis complete: {len(bypass_segments)} bypass, {len(llm_segments)} LLM")
        logger.log_tm_matches(tm_context)
        logger.log_tb_matches(tb_context)

        segments_with_context = [s for s in llm_segments if s.id in tm_context]
        segments_no_context = [s for s in llm_segments if s.id not in tm_context]

        logger.log(f"Segments: {len(segments_with_context)} with TM context, {len(segments_no_context)} without")
        logger.log(f"Processing in original document order")

        # 6. Process LLM segments
        if llm_segments:
            status.update(label=f"Translating {len(llm_segments)} segments...", state="running")

            llm_progress = st.progress(0)
            batch_translations_history = []

            for i in range(0, len(llm_segments), batch_size):
                batch = llm_segments[i:i + batch_size]
                batch_num = (i // batch_size) + 1
                total_batches = (len(llm_segments) + batch_size - 1) // batch_size

                st.write(f"📤 Batch {batch_num}/{total_batches} ({len(batch)} segments)")

                logger.log_batch_start(batch_num, batch)

                batch_tm = {seg.id: tm_context.get(seg.id, []) for seg in batch}
                batch_tb = {seg.id: tb_context.get(seg.id, []) for seg in batch}

                history_context = get_chat_history_context(
                    batch_translations_history,
                    chat_history_length * batch_size
                )

                if history_context:
                    logger.log(f"Chat history: {len(history_context)} previous translations included")

                # Get reference samples for this batch
                reference_samples = ""

                # Use semantic matching if embedding matcher is ready
                if st.session_state.reference_embeddings_ready and st.session_state.embedding_matcher:
                    try:
                        source_texts = [seg.source for seg in batch]

                        matcher = st.session_state.embedding_matcher
                        matches_dict = matcher.find_similar_batch(
                            source_texts,
                            top_k=3,
                            min_similarity=0.35
                        )

                        all_matches = []
                        seen_indices = set()
                        for seg_matches in matches_dict.values():
                            for m in seg_matches:
                                if m.index not in seen_indices:
                                    all_matches.append(m)
                                    seen_indices.add(m.index)

                        all_matches.sort(key=lambda x: x.similarity, reverse=True)
                        reference_samples = matcher.format_reference_context(all_matches[:8], max_chars=2000)

                        if reference_samples:
                            logger.log(f"Semantic reference: {len(all_matches)} matches, {len(reference_samples)} chars")

                    except Exception as e:
                        logger.log(f"Semantic reference error: {e}")
                        if st.session_state.reference_chunks:
                            reference_samples = get_reference_samples(
                                st.session_state.reference_chunks,
                                batch_num,
                                samples_per_batch=5,
                                max_chars=1500
                            )

                elif st.session_state.reference_chunks:
                    reference_samples = get_reference_samples(
                        st.session_state.reference_chunks,
                        batch_num,
                        samples_per_batch=5,
                        max_chars=1500
                    )
                    if reference_samples:
                        logger.log(f"Reference (rotating): {len(reference_samples)} chars of style samples")

                # Get DNT terms
                dnt_terms = st.session_state.dnt_terms if st.session_state.dnt_terms else None
                if dnt_terms:
                    logger.log(f"DNT list: {len(dnt_terms)} forbidden terms")

                prompt = prompt_builder.build_prompt(
                    config.get_language_display_name(src_code),
                    config.get_language_display_name(tgt_code),
                    batch,
                    batch_tm,
                    batch_tb,
                    chat_history=history_context,
                    reference_context=reference_samples,
                    dnt_terms=dnt_terms
                )

                # Defensive guard: prompt must be a non-empty string
                if not isinstance(prompt, str) or not prompt.strip():
                    st.error(f"❌ Prompt build failed for batch {batch_num}: prompt is {type(prompt).__name__} = {repr(prompt[:200] if prompt else prompt)}")
                    logger.log(f"ERROR: Batch {batch_num} - prompt is None or empty, skipping")
                    for seg in batch:
                        match_scores[seg.id] = 0
                    continue

                try:
                    st.write("🔄 Calling LLM API...")
                    response_text, tokens = translator.translate_batch(prompt)

                    if response_text:
                        st.write("✅ LLM Response received")
                        logger.log_llm_interaction(prompt, response_text)

                        lines = response_text.strip().split('\n')
                        batch_results = []
                        parsed_translations = {}

                        for line in lines:
                            if line.startswith('[') and ']' in line:
                                try:
                                    seg_id = line[line.find('[')+1:line.find(']')]
                                    trans_text = line[line.find(']')+1:].strip()
                                    final_translations[seg_id] = trans_text
                                    parsed_translations[seg_id] = trans_text

                                    seg_obj = st.session_state.segment_objects.get(seg_id)
                                    if seg_obj:
                                        batch_results.append({
                                            'source': seg_obj.source,
                                            'target': trans_text
                                        })
                                except Exception:
                                    pass

                        if parsed_translations:
                            logger.log(f"Parsed Response (Batch {batch_num}):")
                            for seg_id in sorted(parsed_translations.keys(), key=lambda x: int(x) if x.isdigit() else 0):
                                trans_text = parsed_translations[seg_id]
                                display_text = trans_text[:80] + "..." if len(trans_text) > 80 else trans_text
                                logger.log(f"  [{seg_id}] {display_text}")

                        batch_translations_history.extend(batch_results)
                    else:
                        st.error("❌ LLM returned empty response")
                        logger.log(f"ERROR: Batch {batch_num} - LLM returned empty response")
                        for seg in batch:
                            match_scores[seg.id] = 0
                        continue

                except AuthenticationError as e:
                    st.error(f"❌ Authentication Error: {str(e)}")
                    st.info("Possible causes: Invalid API key, expired token, rate limit")
                    logger.log(f"ERROR: Authentication failed - {str(e)}")
                    for seg in batch:
                        match_scores[seg.id] = 0
                    break

                except Exception as e:
                    cause = getattr(e, '__cause__', None) or getattr(e, 'last_attempt', None)
                    if cause is not None:
                        real_exc = getattr(cause, 'exception', lambda: None)()
                        real_msg = str(real_exc) if real_exc else str(cause)
                    else:
                        real_msg = str(e)
                    err_msg = f"Batch {batch_num} failed: {real_msg}"
                    st.error(f"❌ {err_msg}")
                    logger.log(f"ERROR: {err_msg}")
                    for seg in batch:
                        match_scores[seg.id] = 0
                    continue

                llm_progress.progress((i + len(batch)) / len(llm_segments))

        # 7. Save results
        duration = time.time() - start_time
        st.session_state.translation_results = final_translations
        st.session_state.translation_log = logger.get_content()
        st.session_state.chat_history = batch_translations_history if llm_segments else []
        st.session_state.segment_match_scores = match_scores

        logger.log("\n" + "="*80)
        logger.log("TRANSLATION JOB SUMMARY")
        logger.log("="*80)
        logger.log(f"Total Segments: {total_segments}")
        _pct = lambda n: f"{n/total_segments*100:.1f}%" if total_segments else "N/A"
        logger.log(f"✓ Bypass (≥95%): {len(bypass_segments)} ({_pct(len(bypass_segments))})")
        logger.log(f"✓ With TM Context (60-94%): {len(tm_context)} ({_pct(len(tm_context))})")
        llm_only_count = len(llm_segments) - len(tm_context)
        logger.log(f"✓ LLM Only (<60%): {llm_only_count} ({_pct(llm_only_count)})")
        logger.log(f"Processing Time: {duration:.1f} seconds")
        logger.log(f"Batch Size: {batch_size} segments")
        num_batches = (len(llm_segments) + batch_size - 1) // batch_size if llm_segments else 0
        logger.log(f"Total Batches: {num_batches}")
        logger.log("="*80 + "\n")

        st.session_state.translation_log = logger.get_content()

        status.update(label="✅ Translation Complete!", state="complete")

        st.success(f"""
        **Translation Complete!**
        - {len(bypass_segments)} segments from TM (no API cost)
        - {len(llm_segments)} segments via LLM
        - {len(final_translations)} total translations
        """)


def _compute_analysis():
    """Compute TM match analysis from session state. Returns dict or None."""
    segment_match_scores = st.session_state.get('segment_match_scores', {})
    segment_objects = st.session_state.get('segment_objects', {})
    if not segment_match_scores or not segment_objects:
        return None
    analysis_by_level = {
        '101% (Context)': {'segments': 0, 'words': 0},
        '100%':           {'segments': 0, 'words': 0},
        '95%-99%':        {'segments': 0, 'words': 0},
        '85%-94%':        {'segments': 0, 'words': 0},
        '75%-84%':        {'segments': 0, 'words': 0},
        '50%-74%':        {'segments': 0, 'words': 0},
        'No match':       {'segments': 0, 'words': 0},
    }
    total_words = 0
    for seg_id, seg_obj in segment_objects.items():
        clean_text = re.sub(r'<[^>]+>|\{\{\d+\}\}', '', seg_obj.source).strip()
        wc = len(clean_text.split()) if clean_text else 0
        total_words += wc
        score = segment_match_scores.get(seg_id, 0)
        if score > 100:
            analysis_by_level['101% (Context)']['segments'] += 1
            analysis_by_level['101% (Context)']['words'] += wc
        elif score == 100:
            analysis_by_level['100%']['segments'] += 1
            analysis_by_level['100%']['words'] += wc
        elif score >= 95:
            analysis_by_level['95%-99%']['segments'] += 1
            analysis_by_level['95%-99%']['words'] += wc
        elif score >= 85:
            analysis_by_level['85%-94%']['segments'] += 1
            analysis_by_level['85%-94%']['words'] += wc
        elif score >= 75:
            analysis_by_level['75%-84%']['segments'] += 1
            analysis_by_level['75%-84%']['words'] += wc
        elif score >= 50:
            analysis_by_level['50%-74%']['segments'] += 1
            analysis_by_level['50%-74%']['words'] += wc
        else:
            analysis_by_level['No match']['segments'] += 1
            analysis_by_level['No match']['words'] += wc
    return {
        'total_segments': len(segment_objects),
        'total_words': total_words,
        'by_level': analysis_by_level,
    }


# --- UI Layout ---

st.title("🚀 Enhanced Translation Assistant")
st.markdown("AI-powered translation with TM, Termbase & Smart Prompt Builder")

tab1, tab2, tab3, tab4 = st.tabs(["📂 Workspace", "📊 Results", "✨ Prompt Builder", "✅ Verifika QA"])

# === TAB 1: WORKSPACE ===
with tab1:
    col1, col2 = st.columns([2, 1])

    with col1:
        # ==================== memoQ SERVER RESOURCES ====================
        st.markdown("##### 🔗 memoQ Server Resources")

        if st.session_state.memoq_connected and st.session_state.memoq_client:
            # Project + document picker (auto-fetches bilingual XLIFF and
            # auto-selects TM/TB GUIDs for the project)
            selected_project_guid, selected_doc = MemoQUI.show_project_picker()

            # Detect languages from the fetched bilingual XLIFF
            if st.session_state.last_xliff_bytes:
                try:
                    detected_src, detected_tgt = XMLParser.detect_languages(
                        st.session_state.last_xliff_bytes
                    )
                    if detected_src and detected_tgt:
                        st.session_state.detected_languages = {
                            'source': detected_src,
                            'target': detected_tgt
                        }
                        st.caption(f"🔍 Detected: {detected_src} → {detected_tgt}")
                except Exception:
                    pass
        else:
            st.warning("🔗 Not connected to memoQ Server. Configure connection in sidebar.")

        st.markdown("---")

        # Reference file for style/tone with semantic matching
        st.markdown("---")
        st.markdown("##### 📑 Semantic Reference (Optional)")

        reference_file = st.file_uploader(
            "Reference File (Target Language Only)",
            type=['txt', 'docx', 'pdf', 'html', 'htm', 'rtf', 'xlsx', 'xls'],
            help="Previously translated text for style/terminology reference. Supports TXT, DOCX, PDF, HTML, RTF, Excel."
        )

        if reference_file:
            reference_file.seek(0)
            chunks = parse_reference_file(reference_file.getvalue(), reference_file.name)
            st.session_state.reference_chunks = chunks

            if chunks:
                # Show cost estimate
                cost_info = get_embedding_cost_estimate(len(chunks), 100)

                col_ref1, col_ref2 = st.columns(2)
                with col_ref1:
                    st.metric("Reference Samples", len(chunks))
                with col_ref2:
                    st.metric("Est. Embedding Cost", cost_info['total_cost_formatted'])

                # Button to create embeddings
                if not st.session_state.reference_embeddings_ready:
                    if api_key:
                        if st.button("🧠 Create Semantic Index", type="secondary", width="stretch"):
                            with st.spinner("Creating embeddings... This may take a minute."):
                                try:
                                    matcher = EmbeddingMatcher(api_key)

                                    progress_bar = st.progress(0)
                                    def update_progress(current, total):
                                        progress_bar.progress(current / total if total > 0 else 0)

                                    count, was_cached = matcher.load_reference(chunks, update_progress)

                                    st.session_state.embedding_matcher = matcher
                                    st.session_state.reference_embeddings_ready = True

                                    if was_cached:
                                        st.success(f"✅ Loaded {count} cached embeddings")
                                    else:
                                        st.success(f"✅ Created {count} embeddings")
                                    st.rerun()

                                except Exception as e:
                                    st.error(f"Embedding error: {e}")
                    else:
                        st.warning("⚠️ API Key required for semantic matching")
                else:
                    st.success("✅ Semantic index ready")
                    if st.button("🔄 Reset Index"):
                        st.session_state.reference_embeddings_ready = False
                        st.session_state.embedding_matcher = None
                        st.rerun()

                with st.expander("Preview reference samples"):
                    for i, chunk in enumerate(chunks[:5]):
                        st.caption(f"{i+1}. {chunk[:100]}..." if len(chunk) > 100 else f"{i+1}. {chunk}")
                    if len(chunks) > 5:
                        st.caption(f"... and {len(chunks) - 5} more")

        st.markdown("---")

        # DNT (Do Not Translate) List
        dnt_file = st.file_uploader(
            "🚫 Do Not Translate List (TXT/CSV)",
            type=['txt', 'csv'],
            help="Terms that should remain in source language (brand names, product codes, etc.)"
        )

        if dnt_file:
            dnt_file.seek(0)
            terms = parse_dnt_file(dnt_file.getvalue(), dnt_file.name)
            st.session_state.dnt_terms = terms
            if terms:
                st.success(f"🚫 **{len(terms)}** forbidden terms loaded")
                with st.expander("Preview DNT terms"):
                    for term in terms[:20]:
                        st.caption(f"• {term}")
                    if len(terms) > 20:
                        st.caption(f"... and {len(terms) - 20} more")

        if st.session_state.use_generated_prompt:
            st.info("✨ Using prompt from Prompt Builder tab")
        elif st.session_state.get('uploaded_prompt_template'):
            st.info("📝 Custom prompt template loaded from Prompt Builder tab")

    with col2:
        st.info("""
        **How it works:**
        1. Segments ≥ Acceptance threshold → Direct TM
        2. Segments ≥ Match threshold → LLM with TM context
        3. Chat history provides consistency across batches
        4. Reference file provides style/tone guidance

        **Prompt Priority:**
        1. Generated prompt (from Prompt Builder)
        2. Custom file upload (Prompt Builder tab)
        3. Default template
        """)

        _xliff_ready = bool(st.session_state.get('last_xliff_bytes'))

        if st.button("🚀 Start Translation", type="primary", width="stretch", disabled=not _xliff_ready):
            if _xliff_ready:
                custom_prompt = None
                uploaded_tpl = st.session_state.get('uploaded_prompt_template')
                if uploaded_tpl and not st.session_state.use_generated_prompt:
                    custom_prompt = uploaded_tpl
                    required_placeholders = ['%SEGMENTS%', '%TERMS%', '%FORBIDDENTERMS%', '%EXAMPLES%']
                    missing_ph = [p for p in required_placeholders if p not in custom_prompt]
                    if missing_ph:
                        st.warning(
                            f"⚠️ Custom prompt is missing placeholders: {', '.join(missing_ph)}. "
                            f"TB terms, DNT list, or reference context may not be injected."
                        )

                process_translation(
                    st.session_state.last_xliff_bytes,
                    tmx_bytes=None,
                    csv_bytes=None,
                    custom_prompt_content=custom_prompt,
                    memoq_tm_guids=st.session_state.selected_tm_guids,
                    memoq_tb_guids=st.session_state.selected_tb_guids
                )
            else:
                st.error("Pick a memoQ project and document first.")

    # --- Analysis results shown inline after translation completes ---
    if st.session_state.translation_results:
        _analysis = _compute_analysis()
        if _analysis:
            st.divider()
            show_analysis_screen(_analysis)

# === TAB 2: RESULTS ===
with tab2:
    if st.session_state.translation_results:
        st.subheader("Translation Output")

        col_stat1, col_stat2, col_stat3 = st.columns(3)
        with col_stat1:
            st.metric("Total Segments", st.session_state.bypass_stats.get('total_segments', len(st.session_state.translation_results)))
        with col_stat2:
            bypassed = st.session_state.bypass_stats.get('bypassed', 0)
            st.metric(f"From TM (≥{acceptance_threshold}%)", bypassed)
        with col_stat3:
            st.metric("Via LLM", st.session_state.bypass_stats.get('llm_sent', 0))

        st.divider()

        col_res1, col_res2 = st.columns(2)

        with col_res1:
            _src_xliff = st.session_state.get('last_xliff_bytes')
            if _src_xliff:
                final_xml = XMLParser.update_xliff(
                    _src_xliff,
                    st.session_state.translation_results,
                    st.session_state.get('segment_objects', {}),
                    match_scores=st.session_state.get('segment_match_scores', {})
                )

                # Cache the rebuilt XLIFF so other tabs (Verifika) can re-use it
                st.session_state.translated_xliff_bytes = final_xml

                _proj_guid = st.session_state.get('memoq_selected_project_guid')
                _doc_guid = st.session_state.get('memoq_selected_document_guid')
                _proj_service = st.session_state.get('memoq_project_service')

                if st.button(
                    "🔄 Update translated file in memoQ",
                    type="primary",
                    width="stretch",
                    disabled=not (_proj_guid and _doc_guid and _proj_service),
                    key="update_translated_btn_results",
                ):
                    with st.spinner("Pushing translated XLIFF to memoQ Server..."):
                        try:
                            new_version = _proj_service.update_bilingual(
                                _proj_guid, _doc_guid, final_xml,
                                filename=st.session_state.get(
                                    'last_xliff_filename', 'translated.mqxliff'
                                ),
                            )
                            if new_version is not None:
                                st.success(f"✓ Updated in memoQ — new document version: {new_version}")
                            else:
                                st.success("✓ Updated in memoQ")
                        except Exception as e:
                            st.error(f"Failed to update in memoQ: {e}")
            else:
                st.info("No source XLIFF in session — re-pick a document in the Workspace tab.")

        with col_res2:
            if st.session_state.translation_log:
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                st.download_button(
                    "📜 Download Detailed Log",
                    st.session_state.translation_log,
                    file_name=f"translation_log_{timestamp}.txt",
                    mime="text/plain"
                )

        st.divider()
        st.subheader("Preview")

        preview_data = []
        for seg_id, trans in st.session_state.translation_results.items():
            seg_obj = st.session_state.segment_objects.get(seg_id)
            source = seg_obj.source if seg_obj else "N/A"
            preview_data.append({
                'ID': seg_id,
                'Source': source[:50] + '...' if len(source) > 50 else source,
                'Translation': trans[:50] + '...' if len(trans) > 50 else trans
            })

        df = pd.DataFrame(preview_data)
        st.dataframe(df, width="stretch")

        # --- TM Analysis ---
        _tab2_analysis = _compute_analysis()
        if _tab2_analysis:
            st.divider()
            st.subheader("📊 TM Match Analysis")
            show_analysis_screen(_tab2_analysis)


    else:
        st.info("No results yet. Run translation in Workspace tab.")

# === TAB 3: PROMPT BUILDER ===
with tab3:
    st.subheader("✨ Smart Prompt Builder")
    st.markdown("Generate optimized prompts from Analysis Reports and Style Guides")

    col_pb1, col_pb2 = st.columns([1, 1])

    with col_pb1:
        st.markdown("#### 📄 Upload Documents")

        # ----- Custom prompt template (moved from Workspace) -----
        custom_prompt_upload = st.file_uploader(
            "📝 Custom Prompt Template (TXT)",
            type=['txt'],
            help="Optional: Upload your own prompt template. Used by Start Translation when no generated prompt is active.",
            key="custom_prompt_template_upload",
            disabled=st.session_state.use_generated_prompt,
        )
        if custom_prompt_upload is not None:
            try:
                custom_prompt_upload.seek(0)
                st.session_state.uploaded_prompt_template = (
                    custom_prompt_upload.read().decode('utf-8', errors='replace')
                )
                st.success(f"✓ Loaded custom prompt template ({len(st.session_state.uploaded_prompt_template)} chars)")
            except Exception as e:
                st.error(f"Could not read prompt template: {e}")
        if st.session_state.get('uploaded_prompt_template') and not st.session_state.use_generated_prompt:
            if st.button("🗑️ Clear custom template", key="clear_custom_tpl"):
                st.session_state.uploaded_prompt_template = None
                st.rerun()

        analysis_file = st.file_uploader(
            "📊 Analysis Report (DOCX)",
            type=['docx'],
            help="AICONTEXT analysis report",
            key="analysis_docx"
        )

        style_file = st.file_uploader(
            "📋 Style Guide (DOCX)",
            type=['docx'],
            help="Translation style guide",
            key="style_docx"
        )

        dnt_file = st.file_uploader(
            "🚫 Do Not Translate / Forbidden Terms (TXT/CSV)",
            type=['txt', 'csv'],
            help="List of terms to avoid in translation. One term per line or CSV format.",
            key="dnt_file"
        )

        # Parse DNT file
        forbidden_terms = []
        if dnt_file:
            dnt_file.seek(0)
            terms = parse_dnt_file(dnt_file.getvalue(), dnt_file.name)
            forbidden_terms = terms
            st.session_state.dnt_terms = terms

            st.success(f"🚫 **{len(forbidden_terms)}** forbidden terms loaded")
            with st.expander("Preview forbidden terms"):
                st.write(", ".join(forbidden_terms[:20]) + ("..." if len(forbidden_terms) > 20 else ""))

        # Analyze uploaded files
        analysis_result = None
        style_result = None

        if analysis_file:
            analysis_file.seek(0)
            analysis_result = DocumentAnalyzer.analyze_file(
                analysis_file.getvalue(),
                analysis_file.name
            )

            with st.expander("📊 Analysis Report Extracted Data", expanded=True):
                if analysis_result.domain:
                    st.success(f"**Domain:** {analysis_result.domain}")
                if analysis_result.domain_composition:
                    st.write("**Domain Composition:**")
                    for comp in analysis_result.domain_composition:
                        st.write(f"  • {comp}")
                if analysis_result.terminology_categories:
                    st.write(f"**Terminology:** {len(analysis_result.terminology_categories)} categories")
                if analysis_result.critical_numbers:
                    st.write(f"**Critical Numbers:** {len(analysis_result.critical_numbers)} items")

        if style_file:
            style_file.seek(0)
            style_result = DocumentAnalyzer.analyze_file(
                style_file.getvalue(),
                style_file.name
            )

            with st.expander("📋 Style Guide Extracted Data", expanded=True):
                if style_result.style_rules:
                    st.write(f"**Style Rules:** {len(style_result.style_rules)} rules")
                if style_result.formatting_rules:
                    st.write(f"**Formatting Rules:** {len(style_result.formatting_rules)} rules")
                    st.write(f"**Gender/Inclusivity:** {len(style_result.gender_inclusivity)} rules")
                if style_result.do_not_translate:
                    st.write(f"**DNT Items:** {len(style_result.do_not_translate)} items")

        st.divider()

        # Generate button
        if st.button("🔮 Generate Prompt", type="primary", width="stretch",
                     disabled=(not analysis_file and not style_file and not dnt_file)):

            prompt, metadata = PromptGenerator.generate(
                analysis=analysis_result,
                style_guide=style_result,
                source_lang=config.get_language_display_name(src_code),
                target_lang=config.get_language_display_name(tgt_code),
                forbidden_terms=forbidden_terms
            )

            st.session_state.generated_prompt = prompt
            st.session_state.prompt_metadata = metadata
            st.success("✅ Prompt generated!")

    with col_pb2:
        st.markdown("#### 📝 Generated Prompt")

        if st.session_state.generated_prompt:
            # Show metadata
            meta = st.session_state.prompt_metadata
            col_m1, col_m2, col_m3, col_m4 = st.columns(4)
            with col_m1:
                st.metric("Style Rules", meta.get('style_rules_count', 0))
            with col_m2:
                st.metric("Term Categories", meta.get('terminology_categories', 0))
            with col_m3:
                st.metric("Format Rules", meta.get('formatting_rules_count', 0))
            with col_m4:
                st.metric("🚫 Forbidden", meta.get('forbidden_terms_count', 0))

            # Editable prompt
            edited_prompt = st.text_area(
                "Edit prompt (optional):",
                value=st.session_state.generated_prompt,
                height=400,
                key="prompt_editor"
            )

            # Update if edited
            if edited_prompt != st.session_state.generated_prompt:
                st.session_state.generated_prompt = edited_prompt

            st.divider()

            # Action buttons
            col_act1, col_act2, col_act3 = st.columns(3)

            with col_act1:
                if st.button("✅ Use This Prompt", type="primary", width="stretch"):
                    st.session_state.use_generated_prompt = True
                    st.success("Prompt activated! Go to Workspace tab to start translation.")

            with col_act2:
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                st.download_button(
                    "⬇️ Download",
                    st.session_state.generated_prompt,
                    file_name=f"cat_tool_prompt_{timestamp}.txt",
                    mime="text/plain",
                    width="stretch"
                )

            with col_act3:
                if st.button("🗑️ Clear", width="stretch"):
                    st.session_state.generated_prompt = None
                    st.session_state.prompt_metadata = {}
                    st.session_state.use_generated_prompt = False
                    st.rerun()
        else:
            st.info("""
            **How to use:**
            1. Upload Analysis Report (AICONTEXT output) and/or Style Guide
            2. Click "Generate Prompt"
            3. Review and edit if needed
            4. Click "Use This Prompt" to activate
            5. Go to Workspace tab and start translation

            **Extracted elements:**
            - Domain & context from Analysis Report
            - Technical protocols (decimal, units, etc.)
            - Style rules from Style Guide
            - Formatting rules
            - Terminology categories
            """)

# === TAB 4: VERIFIKA QA ===
with tab4:
    show_verifika_tab()
