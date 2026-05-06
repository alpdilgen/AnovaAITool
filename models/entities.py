"""
Data models for translation segments, TM matches, and TB matches
"""

from dataclasses import dataclass
from typing import List, Optional, Dict, Any


@dataclass
class TranslationSegment:
    """Represents a single translation segment"""
    id: str
    source: str
    target: str = ""
    tag_map: Optional[dict] = None
    preceding_source: Optional[str] = None
    following_source: Optional[str] = None
    match_rate: int = 0   # mq:percent from pretranslated XLIFF (0 = no match)
    status: str = ""      # mq:status from pretranslated XLIFF


@dataclass
class TMMatch:
    """Universal TM Match object - works with memoQ API"""
    source_text: str
    target_text: str
    similarity: int
    match_type: str = "FUZZY"
    source_file: Optional[str] = None
    project: Optional[str] = None
    domain: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None

    def __post_init__(self):
        """Clean up text"""
        self.source_text = self.source_text.strip() if self.source_text else ""
        self.target_text = self.target_text.strip() if self.target_text else ""
        if self.metadata is None:
            self.metadata = {}

    def __repr__(self):
        src = self.source_text[:40] if self.source_text else "empty"
        tgt = self.target_text[:40] if self.target_text else "empty"
        return f"TMMatch('{src}...' → '{tgt}...' [{self.match_type} {self.similarity}%])"

    def __hash__(self):
        return hash((self.source_text, self.target_text, self.similarity))

    def __eq__(self, other):
        if not isinstance(other, TMMatch):
            return False
        return (self.source_text == other.source_text and
                self.target_text == other.target_text and
                self.similarity == other.similarity)


@dataclass
class TermMatch:
    """Represents a termbase (terminology) match"""
    source: str
    target: str
    context: Optional[str] = None
    part_of_speech: Optional[str] = None
    field: Optional[str] = None
    definition: Optional[str] = None
    source_language: Optional[str] = None
    target_language: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None

    def __post_init__(self):
        """Clean and validate term data"""
        self.source = self.source.strip() if self.source else ""
        self.target = self.target.strip() if self.target else ""
        if self.metadata is None:
            self.metadata = {}

    def is_valid(self) -> bool:
        """Check if term has valid source and target"""
        return bool(self.source and self.target)

    def __repr__(self):
        return f"TermMatch({self.source} = {self.target})"

    def __hash__(self):
        return hash((self.source, self.target))

    def __eq__(self, other):
        if not isinstance(other, TermMatch):
            return False
        return self.source == other.source and self.target == other.target
