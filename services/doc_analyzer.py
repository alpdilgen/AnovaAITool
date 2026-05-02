"""
Document Analyzer Service
Extracts domain, style rules, and terminology from:
- Analysis Reports (AICONTEXT output)
- Style Guides
"""

from docx import Document
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field
import re
import io


@dataclass
class AnalysisResult:
    """Container for extracted document analysis data"""
    # From Analysis Report
    domain: Optional[str] = None
    domain_composition: List[str] = field(default_factory=list)
    executive_context: Optional[str] = None
    
    # Technical protocols
    decimal_format: Optional[str] = None
    unit_conversion: Optional[str] = None
    cultural_adaptation: Optional[str] = None
    geographic_handling: Optional[str] = None
    tone_guide: Optional[str] = None
    
    # Terminology categories
    terminology_categories: Dict[str, str] = field(default_factory=dict)
    critical_numbers: List[str] = field(default_factory=list)
    
    # From Style Guide
    style_rules: List[str] = field(default_factory=list)
    formatting_rules: List[str] = field(default_factory=list)
    gender_inclusivity: List[str] = field(default_factory=list)
    do_not_translate: List[str] = field(default_factory=list)
    
    # Source info
    source_file: Optional[str] = None
    doc_type: Optional[str] = None  # 'analysis' or 'style_guide'


class DocumentAnalyzer:
    """
    Analyzes DOCX documents to extract translation-relevant information.
    Supports:
    - AICONTEXT Analysis Reports
    - Translation Style Guides
    """
    
    # Section markers for Analysis Reports
    ANALYSIS_SECTIONS = {
        'executive_context': ['executive context', '1. executive context'],
        'domain_composition': ['domain composition', '3. domain composition'],
        'technical_protocols': ['technical protocols', '4. technical protocols'],
        'localization_strategy': ['localization strategy', '5. localization strategy'],
        'terminology': ['terminology', '6. terminology'],
    }
    
    # Section markers for Style Guides
    STYLE_SECTIONS = {
        'language_specs': ['language specifications', '4. language specifications', 'style'],
        'gender_inclusivity': ['gender', 'inclusivity', '5. gender'],
        'terminology_dnt': ['terminology', 'do not translate', '6. terminology'],
        'formatting': ['formatting', 'locale', '7. formatting'],
    }
    
    @classmethod
    def analyze_file(cls, file_content: bytes, filename: str = "") -> AnalysisResult:
        """
        Main entry point - analyzes a DOCX file and returns structured data.
        
        Args:
            file_content: Raw bytes of the DOCX file
            filename: Original filename (used for type detection)
            
        Returns:
            AnalysisResult with extracted data
        """
        result = AnalysisResult(source_file=filename)
        
        try:
            doc = Document(io.BytesIO(file_content))
            paragraphs = [p.text.strip() for p in doc.paragraphs if p.text.strip()]
            
            # Detect document type
            full_text = ' '.join(paragraphs[:10]).lower()
            
            if 'enterprise document analysis' in full_text or 'executive context' in full_text:
                result.doc_type = 'analysis'
                cls._parse_analysis_report(paragraphs, result)
            elif 'style guide' in full_text or 'translation/localization' in full_text:
                result.doc_type = 'style_guide'
                cls._parse_style_guide(paragraphs, result)
            else:
                # Try to extract what we can
                result.doc_type = 'unknown'
                cls._parse_generic(paragraphs, result)
                
        except Exception as e:
            result.domain = f"Error parsing document: {str(e)}"
            
        return result
    
    @classmethod
    def _parse_analysis_report(cls, paragraphs: List[str], result: AnalysisResult):
        """Parse AICONTEXT-style analysis report"""
        current_section = None
        
        for para in paragraphs:
            lower = para.lower().strip()
            
            # Detect section changes - must be section headers
            # Headers are typically short and may start with numbers
            is_likely_header = len(para) < 50 or para.startswith(('1.', '2.', '3.', '4.', '5.', '6.', '7.', '8.', '9.'))
            
            if is_likely_header:
                if 'executive context' in lower:
                    current_section = 'executive_context'
                    continue
                elif 'domain composition' in lower:
                    current_section = 'domain_composition'
                    continue
                elif 'technical protocols' in lower:
                    current_section = 'technical_protocols'
                    continue
                elif 'localization strategy' in lower:
                    current_section = 'localization_strategy'
                    continue
                elif lower.endswith('terminology') or lower == '6. terminology':
                    current_section = 'terminology'
                    continue
                elif any(x in lower for x in ['resource qualifications', 'risk matrix', 'workflow', 'commercial estimation', 'document components']):
                    current_section = 'ignore'
                    continue
            
            # Extract content based on section
            if current_section == 'executive_context':
                if not result.executive_context and len(para) > 30:
                    # Skip metadata lines
                    if not any(skip in para.lower() for skip in ['file:', 'date:', '---']):
                        result.executive_context = para
                        # Extract domain from first substantial paragraph
                        result.domain = cls._extract_domain_summary(para)
                    
            elif current_section == 'domain_composition':
                # Parse percentage lines like "60% Agriculture and Crop production - ..."
                match = re.match(r'^(\d+%)\s+(.+?)(?:\s*[-–]\s*(.+))?$', para)
                if match:
                    percentage = match.group(1)
                    domain_name = match.group(2).strip()
                    result.domain_composition.append(f"{percentage} {domain_name}")
                    
            elif current_section == 'technical_protocols':
                if para.lower().startswith('decimal format'):
                    result.decimal_format = para
                elif para.lower().startswith('unit conversion'):
                    result.unit_conversion = para
                elif para.startswith('•') or para.startswith('-'):
                    result.critical_numbers.append(para.lstrip('•- '))
                    
            elif current_section == 'localization_strategy':
                if 'cultural adaptation' in lower:
                    result.cultural_adaptation = para
                elif 'geographic handling' in lower:
                    result.geographic_handling = para
                elif 'tone guide' in lower:
                    result.tone_guide = para
                    
            elif current_section == 'terminology':
                # Parse "Category: term1, term2, term3" format
                if ':' in para:
                    parts = para.split(':', 1)
                    if len(parts) == 2:
                        category = parts[0].strip()
                        terms = parts[1].strip()
                        category_lower = category.lower()
                        # Only skip metadata/workflow categories, not real terminology
                        # Be specific to avoid false matches (e.g., "cultural" in "agricultural")
                        skip_categories = ['text volume', 'visuals', 'tables', 'ocr needs', 
                                          'formatting impact', 'risk of', 'processing', 'evidence',
                                          'workflow', 'profile', 'seniority', 'min experience',
                                          'education', 'tools', 'critical numbers']
                        # Also skip if category starts with [ (risk matrix items)
                        if not any(skip == category_lower or category_lower.startswith(skip) for skip in skip_categories):
                            if not category.startswith('['):
                                if len(terms) > 5:  # Has actual content
                                    result.terminology_categories[category] = terms
    
    @classmethod
    def _parse_style_guide(cls, paragraphs: List[str], result: AnalysisResult):
        """Parse translation style guide"""
        current_section = None
        
        for para in paragraphs:
            lower = para.lower()
            
            # Skip decorative lines
            if para.startswith('─') or para.startswith('---'):
                continue
            
            # Detect section changes
            if any(x in lower for x in ['language specifications', '4. language', 'style']):
                current_section = 'language_specs'
                continue
            elif any(x in lower for x in ['gender', 'inclusivity', '5. gender']):
                current_section = 'gender'
                continue
            elif 'do not translate' in lower or 'dnt' in lower:
                current_section = 'dnt'
                continue
            elif any(x in lower for x in ['formatting', 'locale', '7. formatting']):
                current_section = 'formatting'
                continue
            elif any(x in lower for x in ['quality assurance', 'qa tools', '9. quality']):
                current_section = 'qa'
                continue
            elif any(x in lower for x in ['scope', 'purpose', 'audience', 'domain']):
                current_section = 'intro'
                continue
            
            # Extract content
            if current_section == 'intro':
                # Try to extract domain from style guide intro
                if 'domain:' in lower and not result.domain:
                    result.domain = para.split(':', 1)[1].strip() if ':' in para else None
                    
            elif current_section == 'language_specs':
                # Extract actionable style rules
                if cls._is_actionable_rule(para):
                    result.style_rules.append(para)
                    
            elif current_section == 'gender':
                if cls._is_actionable_rule(para):
                    result.gender_inclusivity.append(para)
                    
            elif current_section == 'dnt':
                if para.startswith('•') or para.startswith('-') or para.startswith('*'):
                    result.do_not_translate.append(para.lstrip('•-* '))
                    
            elif current_section == 'formatting':
                if cls._is_actionable_rule(para):
                    result.formatting_rules.append(para)
    
    @classmethod
    def _parse_generic(cls, paragraphs: List[str], result: AnalysisResult):
        """Parse unknown document format - extract what we can"""
        for para in paragraphs:
            # Look for domain indicators
            if 'domain' in para.lower() and ':' in para and not result.domain:
                result.domain = para.split(':', 1)[1].strip()
            
            # Collect bullet points as potential rules
            if para.startswith('•') or para.startswith('-') or para.startswith('*'):
                if cls._is_actionable_rule(para):
                    result.style_rules.append(para.lstrip('•-* '))
    
    @staticmethod
    def _extract_domain_summary(text: str) -> str:
        """Extract a concise domain description from text"""
        # Take first sentence or first 100 chars
        sentences = text.split('.')
        if sentences:
            first = sentences[0].strip()
            if len(first) > 150:
                return first[:150] + "..."
            return first
        return text[:100] + "..." if len(text) > 100 else text
    
    @staticmethod
    def _is_actionable_rule(text: str) -> bool:
        """Check if text is an actionable translation rule"""
        # Skip headers, short text, and metadata
        if len(text) < 20:
            return False
        if text.startswith('#'):
            return False
        if text.lower().startswith(('note:', 'example:', 'version:', 'last updated')):
            return False
        
        # Look for action words that indicate a rule
        action_indicators = [
            'use ', 'avoid ', 'maintain ', 'preserve ', 'translate ', 
            'keep ', 'do not ', 'don\'t ', 'should ', 'must ', 'prefer ',
            'capitalize', 'lowercase', 'format', 'adapt', 'convert'
        ]
        
        lower = text.lower()
        return any(ind in lower for ind in action_indicators) or ':' in text


class PromptGenerator:
    """
    Generates translation prompts from analysis results.
    """
    
    DEFAULT_TEMPLATE = """You are a senior %SOURCELANG%-to-%TARGETLANG% translator with deep domain knowledge and fluency in %DOMAIN% terminology. You will be provided with a text in %SOURCELANG% which you need to translate into %TARGETLANG%. Your translation must strictly preserve the original layout, formatting, and tags (e.g., {{1}}, {{2}}, etc.), while ensuring contextual and stylistic consistency across all lines.

Translate with accuracy, fluency, and brand consistency.
- Maintain the exact structure of the original document, including all specified tags such as {{1}}, {{2}} and any others exactly as they appear.
- Do not add or remove any spaces, line breaks, paragraphs, or formatting elements.

%STYLE_INSTRUCTIONS%

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
%NUMBER_FORMAT_RULES%
PER-SEGMENT CONTEXT:
Some segments below include ">>> TERMS:" lines listing the approved termbase terms that appear in that segment. Every one of these terms MUST be used in your translation of that segment — all of them, without exception, with only the grammatical adaptations described above.
Some segments also include ">>> TM MATCH (X%):" lines showing an existing translation memory match, followed by ">>> TM SOURCE:" showing the source that produced it. ADAPT the TM match to the current segment instead of translating from scratch:
- 90%+ match: minimal edits, preserve almost everything
- 70-89% match: adapt freely but reuse compatible portions
- Below 70%: treat as reference only
- Always apply all mandatory terminology rules, even when adapting a TM match.

OUTPUT FORMAT (MANDATORY):
Return one translation per line in this exact format:
[ID] Translated text
Do not include explanations, comments, quotation marks, or any text outside this format. Preserve every tag exactly: {{1}}, {{2}}, etc. Do not merge or split segments.

SEGMENTS TO TRANSLATE:
%SEGMENTS%"""

    @classmethod
    def generate(cls, 
                 analysis: Optional[AnalysisResult] = None,
                 style_guide: Optional[AnalysisResult] = None,
                 source_lang: str = "English",
                 target_lang: str = "Turkish",
                 base_template: Optional[str] = None,
                 forbidden_terms: Optional[List[str]] = None) -> Tuple[str, Dict]:
        """
        Generate a prompt from analysis results.
        
        Args:
            analysis: AnalysisResult from analysis report
            style_guide: AnalysisResult from style guide
            source_lang: Source language name
            target_lang: Target language name
            base_template: Optional custom base template
            forbidden_terms: Optional list of terms to avoid in translation
            
        Returns:
            Tuple of (generated_prompt, metadata_dict)
        """
        template = base_template or cls.DEFAULT_TEMPLATE
        metadata = {
            'domain': None,
            'style_rules_count': 0,
            'terminology_categories': 0,
            'formatting_rules_count': 0,
            'forbidden_terms_count': 0
        }
        
        # Extract domain
        domain = "product-related"
        if analysis and analysis.domain:
            domain = analysis.domain
        elif analysis and analysis.executive_context:
            domain = analysis.executive_context[:100]
        elif style_guide and style_guide.domain:
            domain = style_guide.domain
        metadata['domain'] = domain
        
        # Build style instructions
        style_instructions = []
        
        # From analysis report
        if analysis:
            if analysis.decimal_format:
                style_instructions.append(analysis.decimal_format)
            if analysis.unit_conversion:
                style_instructions.append(analysis.unit_conversion)
            if analysis.cultural_adaptation:
                style_instructions.append(analysis.cultural_adaptation)
            if analysis.geographic_handling:
                style_instructions.append(analysis.geographic_handling)
            if analysis.tone_guide:
                style_instructions.append(analysis.tone_guide)
                
            # Add terminology categories as hints
            for category, terms in analysis.terminology_categories.items():
                style_instructions.append(f"{category}: {terms}")
            metadata['terminology_categories'] = len(analysis.terminology_categories)
            
            # Add critical numbers note
            if analysis.critical_numbers:
                numbers_str = ", ".join(analysis.critical_numbers[:10])
                style_instructions.append(f'Evidence: "Precise measurements like \'{numbers_str}\'"')
        
        # From style guide
        if style_guide:
            # Add key style rules (limit to avoid token overflow)
            for rule in style_guide.style_rules[:15]:
                style_instructions.append(rule)
            metadata['style_rules_count'] = len(style_guide.style_rules)
            
            # Add formatting rules
            for rule in style_guide.formatting_rules[:10]:
                style_instructions.append(rule)
            metadata['formatting_rules_count'] = len(style_guide.formatting_rules)
            
            # Add gender/inclusivity rules
            for rule in style_guide.gender_inclusivity[:5]:
                style_instructions.append(rule)
        
        # Format style block
        style_block = ""
        if style_instructions:
            unique_rules = list(dict.fromkeys(style_instructions))  # Remove duplicates, preserve order
            style_block = "\nAdditional Style Guidelines:\n" + "\n".join(f"- {r}" for r in unique_rules) + "\n"
        
        # Build forbidden terms block
        forbidden_block = ""
        all_forbidden = []
        
        # Collect from style guide DNT
        if style_guide and style_guide.do_not_translate:
            all_forbidden.extend(style_guide.do_not_translate)
        
        # Add explicitly provided forbidden terms
        if forbidden_terms:
            all_forbidden.extend(forbidden_terms)
        
        # Remove duplicates and format
        if all_forbidden:
            unique_forbidden = list(dict.fromkeys(all_forbidden))
            forbidden_block = "\n".join(f"- {term}" for term in unique_forbidden)
            metadata['forbidden_terms_count'] = len(unique_forbidden)
        
        # Build number format rules based on target language
        # Locales using comma as decimal separator, dot/space as thousand separator
        comma_decimal_langs = {
            'turkish', 'german', 'french', 'spanish', 'italian', 'portuguese',
            'dutch', 'polish', 'russian', 'czech', 'danish', 'finnish',
            'swedish', 'norwegian', 'hungarian', 'romanian', 'bulgarian',
            'greek', 'croatian', 'slovak', 'slovenian', 'ukrainian', 'serbian'
        }
        tgt_lower = (target_lang or "").lower()
        if any(lang in tgt_lower for lang in comma_decimal_langs):
            decimal_sep = "comma ','"
            thousand_sep = "dot '.' or non-breaking space"
            example = "1.234,56 or 1 234,56 (not 1,234.56)"
        else:
            decimal_sep = "dot '.'"
            thousand_sep = "comma ','"
            example = "1,234.56"

        number_rules = (
            "NUMBER FORMAT RULES (MANDATORY):\n"
            f"- Target language decimal separator: {decimal_sep}\n"
            f"- Target language thousand separator: {thousand_sep}\n"
            f"- Example: {example}\n"
            "- Convert source numbers to target locale format when they are regular numeric values.\n"
            "- EXCEPTION: Do NOT modify parameter codes, error codes, model numbers, version strings, "
            "or any alphanumeric identifiers (e.g., P1.05, E001, v2.3.1). Preserve their exact character sequence.\n"
            "- When a parameter code appears at the end of a sentence, preserve it exactly as in the source; "
            "do not append or remove a trailing period/dot.\n"
        )

        # Replace placeholders
        prompt = template
        prompt = prompt.replace("%SOURCELANG%", source_lang)
        prompt = prompt.replace("%TARGETLANG%", target_lang)
        prompt = prompt.replace("%DOMAIN%", domain)
        prompt = prompt.replace("%STYLE_INSTRUCTIONS%", style_block)
        prompt = prompt.replace("%FORBIDDENTERMS%", forbidden_block)
        prompt = prompt.replace("%NUMBER_FORMAT_RULES%", number_rules)

        # Keep CAT tool placeholders intact
        # %EXAMPLES%, %TERMS% stay as-is for the translation system

        return prompt, metadata
