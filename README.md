# memoQ AI Translator

AI-powered translation tool for memoQ workflows. Connects to memoQ Server to leverage TM and TB data, generates translations via OpenAI, and delivers memoQ-compatible XLIFF output.

> **Beta:** Currently in internal testing.

---

## What it does

1. **Upload XLIFF** — load a `.mqxliff` file exported from memoQ
2. **Connect to memoQ Server** — select Translation Memories and Termbases
3. **Translate** — segment-by-segment translation using GPT-4o or GPT-4o-mini
4. **Download** — receive a XLIFF with full memoQ metadata (match score, status) ready to import

---

## Requirements

- OpenAI API key
- memoQ Server access (URL, username, password)
- A `.mqxliff` file exported from memoQ

---

## Settings

| Parameter | Description |
|-----------|-------------|
| Model | `gpt-4o` (quality) or `gpt-4o-mini` (fast / lower cost) |
| Acceptance threshold | Segments at or above this TM match rate are passed through untranslated (default: 95%) |
| TM match threshold | Minimum similarity for TM context inclusion (default: 70%) |

---

## Importing back to memoQ

Open the downloaded XLIFF in memoQ via **Import > Import with options**.  
Segments arrive with `mq:status` and `mq:percent` values — TM matches are marked `ManuallyConfirmed`, AI translations are marked `PartiallyEdited`.
