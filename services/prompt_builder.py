"""
Advanced Prompt Builder for translation with full context injection
Handles: TM, TB, DNT lists, chat history, reference text
Supports multiple template formats and dynamic context assembly
"""

from typing import List, Dict, Optional, Union, Tuple
from models.entities import TranslationSegment, TMMatch, TermMatch
import os
import logging
import re

logger = logging.getLogger(__name__)


class PromptBuilder:
    """
    Builds optimized translation prompts with comprehensive context
    
    Context types supported:
    - Translation Memory (TM): Fuzzy matches with similarity scores
    - Termbase (TB): Approved terminology pairs
    - Do Not Translate (DNT): Brand names, codes, protected terms
    - Chat History: Previous translations for consistency
    - Reference Text: Style guide and examples
    
    Features:
    - Multiple template support (default, custom, dynamic)
    - Context deduplication and optimization
    - Token-aware context trimming
    - Comprehensive logging
    """
    
    def __init__(self, 
                 template_path: Optional[str] = None, 
                 custom_template: Optional[str] = None):
        """
        Initialize PromptBuilder
        
        Args:
            template_path: Path to custom template file
            custom_template: Direct template string (takes priority)
        """
        self.template = ""
        self.template_path = template_path
        
        if custom_template:
            self.template = custom_template
            logger.info("PromptBuilder: Using custom template from parameter")
        
        elif template_path and os.path.exists(template_path):
            try:
                with open(template_path, 'r', encoding='utf-8') as f:
                    self.template = f.read()
                logger.info(f"PromptBuilder: Loaded template from {template_path}")
            except Exception as e:
                logger.warning(f"Failed to load template from {template_path}: {e}")
                self.template = self._get_default_template()
        
        else:
            self.template = self._get_default_template()
            logger.info("PromptBuilder: Using default template")
    
    def set_custom_template(self, template_content: str):
        """Set custom template from string"""
        if not template_content or not template_content.strip():
            logger.warning("Empty template provided, keeping current template")
            return
        
        self.template = template_content
        logger.info("PromptBuilder: Custom template set")
    
    def _get_default_template(self) -> str:
        """Default template with all placeholder sections"""
        return """You are a senior %SOURCELANG%-to-%TARGETLANG% translator with deep domain knowledge and fluency in product-related terminology. You will be provided with a text in %SOURCELANG% which you need to translate into %TARGETLANG%. Your translation must strictly preserve the original layout, formatting, and tags (e.g., {{1}}, {{2}}, etc.), while ensuring contextual and stylistic consistency across all lines.
Translate with accuracy, fluency, and brand consistency.
Maintain the exact structure of the original document, including all specified tags such as {{1}}, {{2}} and any others exactly as they appear.
Do not add or remove any spaces, line breaks, paragraphs, or formatting elements.
Use the examples provided below as reference for style and structure:
%EXAMPLES%
TERMINOLOGY RULES (MANDATORY — HIGHEST PRIORITY):
Every term listed below comes from the client's approved termbase. You MUST use ALL of them in your translation — there are no optional terms. Every approved term that appears in a source segment MUST appear in the corresponding translation. Omitting any approved term is not permitted.
The approved term is the required BASE FORM. You must preserve this base form exactly — do not rephrase, restructure, shorten, or substitute it with alternative forms.
You are permitted to make ONLY the following grammatical adaptations, and nothing else:
- Add inflectional suffixes to the END of the term (plural markers, case endings, possessive markers, verb conjugation endings, agglutinative suffixes, etc.) as required by %TARGETLANG% grammar.
- Prepend grammatical articles (definite or indefinite) BEFORE the term where %TARGETLANG% grammar requires them (e.g., "der/die/das" in German, "le/la/les" in French, "el/la/los/las" in Spanish, "il/la/i/le" in Italian, etc.).
- Apply gender agreement to articles and any adjectives that directly modify the term, following the grammatical gender of the term in %TARGETLANG%.
- Apply case-driven changes to articles or preceding prepositions required by the syntactic role of the term in the sentence (e.g., German preposition-article contractions such as "im", "zum", "vom").
The base form of the approved term itself must remain exactly as specified. Only the surrounding grammatical elements and word endings may change.
%TERMS%
Keep the following terms in their original source language form — do not translate them:
%FORBIDDENTERMS%
PER-SEGMENT CONTEXT:
Some segments below include ">>> TERMS:" lines listing the approved termbase terms that appear in that segment. Every one of these terms MUST be used in your translation of that segment — all of them, without exception, with only the grammatical adaptations described above.
Some segments also include ">>> TM MATCH" lines showing a previous translation from Translation Memory:
- Use the TM match as your STARTING POINT — adapt it, do not translate from scratch.
- For 90%+ matches, make only minimal changes. For 70-89%, adapt more freely but preserve reusable parts.
- Always apply all mandatory terminology rules, even when adapting a TM match.
Return only the translated text. Do not include explanations or comments in your response.
OUTPUT FORMAT:
[ID] Translated text
SEGMENTS TO TRANSLATE:
%SEGMENTS%
"""
    
    def _get_match_info(self, match: Union[TMMatch, Dict, object]) -> Optional[Dict]:
        """
        Extract match information from any format
        Handles TMMatch objects, dicts, and other formats
        
        Args:
            match: TMMatch object, dict, or other match format
        
        Returns:
            Dict with source, target, similarity, match_type
            None if match is invalid
        """
        try:
            # Format 1: Standard TMMatch object
            if isinstance(match, TMMatch):
                return {
                    'source': match.source_text,
                    'target': match.target_text,
                    'similarity': match.similarity,
                    'match_type': match.match_type
                }
            
            # Format 2: Dict with TMMatch attributes
            elif isinstance(match, dict):
                if 'source_text' in match and 'target_text' in match:
                    return {
                        'source': match['source_text'],
                        'target': match['target_text'],
                        'similarity': match.get('similarity', 0),
                        'match_type': match.get('match_type', 'FUZZY')
                    }
                
                # Format 3: Dict with alternative field names
                # 'score' is the key used by local TMX extract_matches()
                elif 'source' in match or 'target' in match:
                    return {
                        'source': match.get('source', match.get('source_text', '')),
                        'target': match.get('target', match.get('target_text', '')),
                        'similarity': match.get('similarity', match.get('score', match.get('match_rate', 0))),
                        'match_type': match.get('match_type', 'FUZZY')
                    }
            
            logger.warning(f"Unknown match format: {type(match)}")
            return None
        
        except Exception as e:
            logger.error(f"Error extracting match info: {e}")
            return None
    
    def _deduplicate_tm(self, matches_by_segment: Dict[str, List]) -> Dict[str, List]:
        """
        Remove duplicate TM matches across segments
        Keep highest similarity version of each source→target pair
        
        Args:
            matches_by_segment: {segment_id: [TMMatch objects]}
        
        Returns:
            Deduplicated dict with only unique source→target pairs
        """
        unique_tm = {}
        
        for seg_id, matches in matches_by_segment.items():
            if not matches:
                continue
            
            for match in matches:
                match_info = self._get_match_info(match)
                if not match_info:
                    continue
                
                key = f"{match_info['source']}->{match_info['target']}"
                
                # Keep highest similarity
                if key not in unique_tm or match_info['similarity'] > unique_tm[key]['similarity']:
                    unique_tm[key] = match_info
        
        return unique_tm
    
    def _deduplicate_tb(self, terms_by_segment: Dict[str, List]) -> Dict[str, str]:
        """
        Remove duplicate TB terms across segments
        
        Args:
            terms_by_segment: {segment_id: [TermMatch objects]}
        
        Returns:
            Dict: {source_term: target_term}
        """
        unique_tb = {}
        
        for seg_id, terms in terms_by_segment.items():
            if not terms:
                continue
            
            for term in terms:
                if isinstance(term, TermMatch):
                    unique_tb[term.source] = term.target
                elif isinstance(term, dict):
                    source = term.get('source', '')
                    target = term.get('target', '')
                    if source and target:
                        unique_tb[source] = target
        
        return unique_tb
    
    def _format_tm_context(self, unique_tm: Dict[str, Dict], max_matches: int = 15) -> str:
        """
        Format TM matches for inclusion in prompt
        
        Args:
            unique_tm: {key: match_info_dict}
            max_matches: Maximum matches to include
        
        Returns:
            Formatted text section for prompt
        """
        if not unique_tm:
            return "No Translation Memory matches available.\n\n"
        
        # Sort by similarity descending
        sorted_tm = sorted(
            unique_tm.values(),
            key=lambda x: x['similarity'],
            reverse=True
        )[:max_matches]
        
        text = "TRANSLATION MEMORY CONTEXT:\n"
        for match in sorted_tm:
            source = match['source']
            target = match['target']
            similarity = match['similarity']
            match_type = match['match_type']
            
            # Truncate long entries
            source_display = source[:60] + "..." if len(source) > 60 else source
            target_display = target[:60] + "..." if len(target) > 60 else target
            
            text += f"{source_display} → {target_display} [{match_type} {similarity}%]\n"
        
        text += "\n"
        return text
    
    def _format_tb_context(self, unique_tb: Dict[str, str], max_terms: int = 30) -> str:
        """
        Format termbase entries for inclusion in prompt
        
        Args:
            unique_tb: {source_term: target_term}
            max_terms: Maximum terms to include
        
        Returns:
            Formatted text section for prompt
        """
        if not unique_tb:
            return "No specific terminology constraints.\n\n"
        
        # Sort alphabetically for readability
        sorted_tb = sorted(unique_tb.items())[:max_terms]
        
        text = "REQUIRED TERMINOLOGY (use these exact translations):\n"
        for source, target in sorted_tb:
            text += f"- \"{source}\" → \"{target}\"\n"

        text += "\n"
        return text
    
    def _format_dnt_context(self, dnt_terms: Optional[List[str]], max_terms: int = 50) -> str:
        """
        Format Do Not Translate list for inclusion in prompt
        
        Args:
            dnt_terms: List of terms to keep in original form
            max_terms: Maximum terms to include
        
        Returns:
            Formatted text section for prompt
        """
        if not dnt_terms:
            return ""
        
        dnt_set = set(dnt_terms)  # Remove duplicates
        sorted_dnt = sorted(dnt_set)[:max_terms]
        
        if not sorted_dnt:
            return ""
        
        text = "Keep the following terms in their original source language form — do not translate them:\n"
        for term in sorted_dnt:
            text += f"- {term}\n"
        
        text += "\n"
        return text
    
    def _format_chat_history(self, 
                             chat_history: Optional[List[Dict[str, str]]], 
                             max_items: int = 10) -> str:
        """
        Format previous translations for consistency context
        
        Args:
            chat_history: List of {source: str, target: str} dicts
            max_items: Maximum items to include
        
        Returns:
            Formatted text section for prompt
        """
        if not chat_history or len(chat_history) == 0:
            return ""
        
        limited_history = chat_history[-max_items:]  # Last N items
        
        text = "PREVIOUS TRANSLATIONS (for consistency):\n"
        for item in limited_history:
            source = item.get('source', '').strip()
            target = item.get('target', '').strip()
            
            if source and target:
                # Truncate long entries
                source_display = source[:50] + "..." if len(source) > 50 else source
                target_display = target[:50] + "..." if len(target) > 50 else target
                
                text += f"{source_display} → {target_display}\n"
        
        text += "\n"
        return text
    
    def _format_reference_text(self, reference_context: Optional[str]) -> str:
        """
        Format style reference text
        
        Args:
            reference_context: Style guide or example text
        
        Returns:
            Formatted text section for prompt
        """
        if not reference_context or not reference_context.strip():
            return ""
        
        return f"STYLE REFERENCE:\n{reference_context}\n\n"
    
    def _format_segments(self, segments: List[TranslationSegment],
                         tm_context: Optional[Dict[str, List]] = None,
                         tb_context: Optional[Dict[str, List]] = None) -> str:
        """
        Format segments for translation, embedding per-segment TM matches
        and per-segment TB terms directly next to each segment.

        Args:
            segments: List of TranslationSegment objects
            tm_context: Optional dict {segment_id: [TMMatch objects]}
            tb_context: Optional dict {segment_id: [TermMatch objects]}

        Returns:
            Formatted segment list for prompt
        """
        seg_text = ""
        for seg in segments:
            seg_text += f"[{seg.id}] {seg.source}\n"

            # Embed per-segment TB terms right next to the segment
            if tb_context and seg.id in tb_context and tb_context[seg.id]:
                term_pairs = []
                for term in tb_context[seg.id]:
                    if isinstance(term, TermMatch):
                        term_pairs.append(f"\"{term.source}\" = \"{term.target}\"")
                    elif isinstance(term, dict):
                        src = term.get('source', '')
                        tgt = term.get('target', '')
                        if src and tgt:
                            term_pairs.append(f"\"{src}\" = \"{tgt}\"")
                if term_pairs:
                    seg_text += f">>> TERMS: {', '.join(term_pairs)}\n"

            # Embed best TM match for this segment if available
            if tm_context and seg.id in tm_context and tm_context[seg.id]:
                best_match = None
                best_similarity = 0

                for match in tm_context[seg.id]:
                    match_info = self._get_match_info(match)
                    if match_info and match_info['similarity'] > best_similarity:
                        best_match = match_info
                        best_similarity = match_info['similarity']

                if best_match and best_similarity > 0:
                    seg_text += (
                        f">>> TM MATCH ({best_match['similarity']}%): "
                        f"{best_match['target']}\n"
                        f">>> TM SOURCE: {best_match['source']}\n"
                    )

        return seg_text
    
    def build_prompt(self,
                     source_lang: str,
                     target_lang: str,
                     segments: List[TranslationSegment],
                     tm_context: Optional[Dict[str, List]] = None,
                     tb_context: Optional[Dict[str, List]] = None,
                     chat_history: Optional[List[Dict[str, str]]] = None,
                     reference_context: Optional[str] = None,
                     dnt_terms: Optional[List[str]] = None) -> str:
        """
        Build complete translation prompt with all available context
        
        Args:
            source_lang: Source language name (e.g., "English")
            target_lang: Target language name (e.g., "Turkish")
            segments: List of segments to translate
            tm_context: Dict of {segment_id: [TMMatch objects]}
            tb_context: Dict of {segment_id: [TermMatch objects]}
            chat_history: List of previous translations for consistency
            reference_context: Target language style reference
            dnt_terms: List of terms to not translate
        
        Returns:
            Complete prompt string ready for LLM
        
        Prompt structure:
        1. Role statement
        2. Task instructions
        3. Style reference (if available)
        4. Chat history (if available)
        5. TM context (fuzzy matches with %)
        6. TB context (terminology)
        7. DNT list
        8. Output format
        9. Segments to translate
        """
        
        logger.info(f"Building prompt: {source_lang} → {target_lang}, {len(segments)} segments")
        
        # ===== 1. Format Reference Text =====
        reference_text = self._format_reference_text(reference_context)
        
        # ===== 2. Format Chat History =====
        history_text = self._format_chat_history(chat_history, max_items=10)
        
        # ===== 3. TM Context =====
        # Per-segment TM matches are embedded inline in %SEGMENTS% (>>> TM MATCH).
        # We do NOT duplicate them as global examples in %EXAMPLES% — that would
        # show the same information twice and waste tokens.
        examples_text = ""
        
        # ===== 4. Format TB Context =====
        terms_text = ""
        if tb_context:
            unique_tb = self._deduplicate_tb(tb_context)
            terms_text = self._format_tb_context(unique_tb, max_terms=30)
        else:
            terms_text = "No specific terminology constraints.\n\n"
        
        # ===== 5. Format DNT =====
        dnt_text = self._format_dnt_context(dnt_terms, max_terms=50)
        
        # ===== 6. Format Segments (with per-segment TM + TB embedded) =====
        seg_text = self._format_segments(segments, tm_context=tm_context, tb_context=tb_context)
        
        # ===== 7. Build Prompt from Template =====
        prompt = self.template
        
        # Replace language placeholders
        prompt = prompt.replace("%SOURCELANG%", source_lang)
        prompt = prompt.replace("%TARGETLANG%", target_lang)
        
        # Replace context sections
        # Combine reference + history + examples
        combined_examples = reference_text + history_text + examples_text
        
        prompt = prompt.replace("%EXAMPLES%", combined_examples)
        prompt = prompt.replace("%TERMS%", terms_text)
        
        # Handle DNT section — only replace the placeholder, not literal text
        prompt = prompt.replace("%FORBIDDENTERMS%", dnt_text if dnt_text else "")
        
        # Replace segments
        prompt = prompt.replace("%SEGMENTS%", seg_text)
        
        # ===== 8. Log Prompt Statistics =====
        prompt_stats = {
            'total_chars': len(prompt),
            'segments': len(segments),
            'tm_matches': sum(len(m) for m in tm_context.values() if m) if tm_context else 0,
            'tb_terms': sum(len(t) for t in tb_context.values() if t) if tb_context else 0,
            'dnt_terms': len(dnt_terms) if dnt_terms else 0,
            'history_items': len(chat_history) if chat_history else 0
        }
        
        logger.info(
            f"Prompt built: {prompt_stats['total_chars']} chars, "
            f"TM={prompt_stats['tm_matches']}, "
            f"TB={prompt_stats['tb_terms']}, "
            f"DNT={prompt_stats['dnt_terms']}"
        )
        
        return prompt
