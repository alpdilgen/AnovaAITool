"""
Embedding-based Reference Matcher
Uses OpenAI embeddings for semantic similarity matching between
source segments and target-language reference text.
"""

import json
import hashlib
import os
from typing import List, Dict, Tuple, Optional
import numpy as np
from dataclasses import dataclass


@dataclass
class ReferenceMatch:
    """A matched reference chunk with similarity score"""
    text: str
    similarity: float
    index: int


class EmbeddingMatcher:
    """
    Semantic reference matching using OpenAI embeddings.
    
    Features:
    - Caches embeddings to avoid re-computation
    - Batch embedding for efficiency
    - Cosine similarity for matching
    """
    
    EMBEDDING_MODEL = "text-embedding-3-small"  # Fast and cheap
    EMBEDDING_DIM = 1536
    CACHE_DIR = "cache/embeddings"
    
    def __init__(self, api_key: str):
        """
        Initialize EmbeddingMatcher.
        
        Args:
            api_key: OpenAI API key
        """
        self.api_key = api_key
        self.client = None
        self._init_client()
        
        # Ensure cache directory exists
        os.makedirs(self.CACHE_DIR, exist_ok=True)
        
        # Reference data
        self.reference_chunks: List[str] = []
        self.reference_embeddings: Optional[np.ndarray] = None
        self.reference_hash: Optional[str] = None
    
    def _init_client(self):
        """Initialize OpenAI client"""
        try:
            from openai import OpenAI
            self.client = OpenAI(api_key=self.api_key)
        except Exception as e:
            raise RuntimeError(f"Failed to initialize OpenAI client: {e}")
    
    def _compute_hash(self, chunks: List[str]) -> str:
        """Compute hash for reference chunks to enable caching"""
        content = "\n".join(chunks)
        return hashlib.md5(content.encode('utf-8')).hexdigest()[:16]
    
    def _get_cache_path(self, ref_hash: str) -> str:
        """Get cache file path for a reference hash"""
        return os.path.join(self.CACHE_DIR, f"ref_{ref_hash}.json")
    
    def _load_cached_embeddings(self, ref_hash: str) -> Optional[np.ndarray]:
        """Load embeddings from cache if available"""
        cache_path = self._get_cache_path(ref_hash)
        if os.path.exists(cache_path):
            try:
                with open(cache_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                return np.array(data['embeddings'])
            except:
                pass
        return None
    
    def _save_embeddings_cache(self, ref_hash: str, embeddings: np.ndarray, chunks: List[str]):
        """Save embeddings to cache"""
        cache_path = self._get_cache_path(ref_hash)
        try:
            with open(cache_path, 'w', encoding='utf-8') as f:
                json.dump({
                    'hash': ref_hash,
                    'chunk_count': len(chunks),
                    'embeddings': embeddings.tolist()
                }, f)
        except Exception as e:
            print(f"Warning: Could not save embeddings cache: {e}")
    
    def _get_embeddings_batch(self, texts: List[str]) -> np.ndarray:
        """
        Get embeddings for a batch of texts.
        
        Args:
            texts: List of texts to embed
            
        Returns:
            numpy array of embeddings (N x EMBEDDING_DIM)
        """
        if not texts:
            return np.array([])
        
        # Clean texts (remove empty, truncate long ones)
        clean_texts = []
        for t in texts:
            t = t.strip()
            if t:
                # Truncate to ~8000 tokens (~32000 chars) to stay within limits
                if len(t) > 30000:
                    t = t[:30000]
                clean_texts.append(t)
        
        if not clean_texts:
            return np.array([])
        
        try:
            response = self.client.embeddings.create(
                model=self.EMBEDDING_MODEL,
                input=clean_texts
            )
            
            embeddings = [item.embedding for item in response.data]
            return np.array(embeddings)
            
        except Exception as e:
            raise RuntimeError(f"Embedding API call failed: {e}")
    
    def load_reference(self, chunks: List[str], progress_callback=None) -> Tuple[int, bool]:
        """
        Load reference chunks and compute/load embeddings.
        
        Args:
            chunks: List of reference text chunks
            progress_callback: Optional callback(current, total) for progress
            
        Returns:
            Tuple of (chunk_count, was_cached)
        """
        if not chunks:
            return 0, False
        
        self.reference_chunks = chunks
        self.reference_hash = self._compute_hash(chunks)
        
        # Try to load from cache
        cached = self._load_cached_embeddings(self.reference_hash)
        if cached is not None and len(cached) == len(chunks):
            self.reference_embeddings = cached
            return len(chunks), True
        
        # Compute embeddings in batches
        batch_size = 50  # OpenAI allows up to 2048, but smaller batches are safer
        all_embeddings = []
        
        for i in range(0, len(chunks), batch_size):
            batch = chunks[i:i + batch_size]
            
            if progress_callback:
                progress_callback(i, len(chunks))
            
            batch_embeddings = self._get_embeddings_batch(batch)
            all_embeddings.append(batch_embeddings)
        
        if progress_callback:
            progress_callback(len(chunks), len(chunks))
        
        self.reference_embeddings = np.vstack(all_embeddings)
        
        # Cache for future use
        self._save_embeddings_cache(self.reference_hash, self.reference_embeddings, chunks)
        
        return len(chunks), False
    
    def find_similar_batch(self,
                           source_texts: List[str],
                           top_k: int = 3,
                           min_similarity: float = 0.3) -> Dict[int, List[ReferenceMatch]]:
        """
        Find similar references for multiple source texts efficiently.
        
        Args:
            source_texts: List of source texts
            top_k: Number of top matches per text
            min_similarity: Minimum similarity threshold
            
        Returns:
            Dict mapping text index to list of ReferenceMatch
        """
        if self.reference_embeddings is None or len(self.reference_chunks) == 0:
            return {}
        
        # Get embeddings for all source texts in one call
        source_embeddings = self._get_embeddings_batch(source_texts)
        if len(source_embeddings) == 0:
            return {}
        
        results = {}
        
        for i, source_vec in enumerate(source_embeddings):
            similarities = self._cosine_similarity(source_vec, self.reference_embeddings)
            top_indices = np.argsort(similarities)[::-1][:top_k]
            
            matches = []
            for idx in top_indices:
                sim = similarities[idx]
                if sim >= min_similarity:
                    matches.append(ReferenceMatch(
                        text=self.reference_chunks[idx],
                        similarity=float(sim),
                        index=int(idx)
                    ))
            
            if matches:
                results[i] = matches
        
        return results
    
    def _cosine_similarity(self, vec: np.ndarray, matrix: np.ndarray) -> np.ndarray:
        """
        Compute cosine similarity between a vector and each row of a matrix.
        
        Args:
            vec: 1D vector
            matrix: 2D matrix (N x dim)
            
        Returns:
            1D array of similarities
        """
        # Normalize
        vec_norm = vec / (np.linalg.norm(vec) + 1e-10)
        matrix_norms = np.linalg.norm(matrix, axis=1, keepdims=True) + 1e-10
        matrix_normalized = matrix / matrix_norms
        
        # Dot product
        similarities = np.dot(matrix_normalized, vec_norm)
        
        return similarities
    
    def format_reference_context(self, 
                                  matches: List[ReferenceMatch],
                                  max_chars: int = 1500) -> str:
        """
        Format reference matches for inclusion in prompt.
        
        Args:
            matches: List of ReferenceMatch objects
            max_chars: Maximum total characters
            
        Returns:
            Formatted string
        """
        if not matches:
            return ""
        
        lines = []
        total_chars = 0
        
        for m in matches:
            text = m.text
            # Truncate long texts
            if len(text) > 300:
                text = text[:300] + "..."
            
            if total_chars + len(text) > max_chars:
                break
            
            lines.append(f"• {text}")
            total_chars += len(text)
        
        return "\n".join(lines)


def get_embedding_cost_estimate(chunk_count: int, segment_count: int) -> dict:
    """
    Estimate API cost for embeddings.
    
    text-embedding-3-small: $0.00002 per 1K tokens
    Average chunk: ~50 tokens
    Average segment: ~30 tokens
    
    Args:
        chunk_count: Number of reference chunks
        segment_count: Number of segments to translate
        
    Returns:
        Dict with cost estimates
    """
    # Reference embeddings (one-time)
    ref_tokens = chunk_count * 50
    ref_cost = (ref_tokens / 1000) * 0.00002
    
    # Segment embeddings (per translation)
    seg_tokens = segment_count * 30
    seg_cost = (seg_tokens / 1000) * 0.00002
    
    total_cost = ref_cost + seg_cost
    
    return {
        'reference_chunks': chunk_count,
        'segment_count': segment_count,
        'reference_cost': round(ref_cost, 6),
        'segment_cost': round(seg_cost, 6),
        'total_cost': round(total_cost, 6),
        'total_cost_formatted': f"${total_cost:.4f}"
    }
