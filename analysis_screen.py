"""
ANALYSIS SCREEN - Post-translation TM match analysis
Displays accurate segment and word counts by fuzzy match level.
"""

import streamlit as st
import pandas as pd


def show_analysis_screen(analysis_results):
    """Show TM match analysis after translation completes."""

    total_segments = analysis_results['total_segments']
    total_words = analysis_results['total_words']
    by_level = analysis_results['by_level']

    # --- Summary metrics ---
    bypass_segs = (
        by_level.get('101% (Context)', {}).get('segments', 0) +
        by_level.get('100%', {}).get('segments', 0) +
        by_level.get('95%-99%', {}).get('segments', 0)
    )
    fuzzy_segs = sum(
        by_level.get(level, {}).get('segments', 0)
        for level in ['85%-94%', '75%-84%', '50%-74%']
    )
    no_match_segs = by_level.get('No match', {}).get('segments', 0)

    bypass_words = (
        by_level.get('101% (Context)', {}).get('words', 0) +
        by_level.get('100%', {}).get('words', 0) +
        by_level.get('95%-99%', {}).get('words', 0)
    )
    fuzzy_words = sum(
        by_level.get(level, {}).get('words', 0)
        for level in ['85%-94%', '75%-84%', '50%-74%']
    )
    no_match_words = by_level.get('No match', {}).get('words', 0)

    tm_seg_cov = (bypass_segs + fuzzy_segs) / max(total_segments, 1) * 100
    tm_word_cov = (bypass_words + fuzzy_words) / max(total_words, 1) * 100

    st.markdown("### TM Match Analysis")

    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric("📊 Total Segments", total_segments)
    with col2:
        st.metric("📝 Total Words", total_words)
    with col3:
        st.metric("🎯 TM Segment Coverage", f"{tm_seg_cov:.1f}%")
    with col4:
        st.metric("📖 TM Word Coverage", f"{tm_word_cov:.1f}%")

    st.markdown("---")
    st.markdown("#### Match Distribution")

    LEVELS = [
        '101% (Context)',
        '100%',
        '95%-99%',
        '85%-94%',
        '75%-84%',
        '50%-74%',
        'No match',
    ]

    breakdown_data = []
    for level in LEVELS:
        data = by_level.get(level, {'segments': 0, 'words': 0})
        segs = data['segments']
        words = data['words']
        seg_pct = segs / max(total_segments, 1) * 100
        word_pct = words / max(total_words, 1) * 100
        breakdown_data.append({
            'Match Level': level,
            'Segments': segs,
            'Seg %': f"{seg_pct:.1f}%",
            'Words': words,
            'Word %': f"{word_pct:.1f}%",
        })

    # Totals row
    breakdown_data.append({
        'Match Level': 'TOTAL',
        'Segments': total_segments,
        'Seg %': '100.0%',
        'Words': total_words,
        'Word %': '100.0%',
    })

    df = pd.DataFrame(breakdown_data)
    st.dataframe(df, width="stretch", hide_index=True)

    st.markdown("---")
    st.markdown("#### Processing Summary")

    summary_data = [
        {
            'Category': 'Leveraged (≥95% — used verbatim from TM)',
            'Segments': bypass_segs,
            'Seg %': f"{bypass_segs / max(total_segments, 1) * 100:.1f}%",
            'Words': bypass_words,
            'Word %': f"{bypass_words / max(total_words, 1) * 100:.1f}%",
        },
        {
            'Category': 'Fuzzy (50–94% — TM context sent to LLM)',
            'Segments': fuzzy_segs,
            'Seg %': f"{fuzzy_segs / max(total_segments, 1) * 100:.1f}%",
            'Words': fuzzy_words,
            'Word %': f"{fuzzy_words / max(total_words, 1) * 100:.1f}%",
        },
        {
            'Category': 'No match (<50% — LLM only)',
            'Segments': no_match_segs,
            'Seg %': f"{no_match_segs / max(total_segments, 1) * 100:.1f}%",
            'Words': no_match_words,
            'Word %': f"{no_match_words / max(total_words, 1) * 100:.1f}%",
        },
    ]

    summary_df = pd.DataFrame(summary_data)
    st.dataframe(summary_df, width="stretch", hide_index=True)



