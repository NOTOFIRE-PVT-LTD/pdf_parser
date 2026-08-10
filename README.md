# Tender Parser

Offline desktop/web app that extracts structured data from Tender / NIT PDFs and exports JSON, Excel, and CSV.

**V1 uses Streamlit + rule-based extraction. No paid AI APIs required.** OCR uses local Tesseract. An AI enrichment module can be plugged in later (Ollama / OpenAI / Anthropic / Gemini) without changing the pipeline.

---

## Features

- Upload one or many PDFs (drag & drop)
- Auto-detect text vs scanned PDFs
- OCR scanned pages with Tesseract + OpenCV preprocessing
- Extract tender info, financials, dates, eligibility, products/BOQ, documents, clauses, contacts
- Multi-page table merge for BOQ / product lists
- Collapsible results UI, search, dark theme
- Download **JSON / Excel / CSV**
- Encrypted PDF → password prompt
- OCR failure → clear error message
- Missing fields → `null`

---

## Project layout

```
app/
  api/           # Optional FastAPI HTTP API
  services/      # Pipeline, export, search, AI factory
  ocr/           # Tesseract OCR service
  parser/        # PDF + table parsers
  extractor/     # Field / product / clause extractors
  models/        # Pydantic schemas
  utils/         # Patterns + text helpers
  templates/     # Jinja2 (optional HTML)
  static/        # CSS
  streamlit_app.py
tests/
data/uploads|exports|cache/
```

---

## Requirements

1. **Python 3.12+**
2. **Tesseract OCR** (for scanned PDFs)
   - Windows: https://github.com/UB-Mannheim/tesseract/wiki  
   - After install, either add to PATH or set `TESSERACT_CMD` in `.env`
3. Optional: Ollama (only if you enable AI later)

---

## Quick start (Windows)

```bat
run_streamlit.bat
```

Or manually:

```bat
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
streamlit run app\streamlit_app.py
```

API (optional):

```bat
run_api.bat
```

Then open http://localhost:8000/docs

---

## JSON export shape

```json
{
  "tender_information": {},
  "financial": {},
  "dates": {},
  "eligibility": {},
  "products": [],
  "documents_required": [],
  "important_clauses": [],
  "contact_details": {}
}
```

---

## Architecture

```
PDF upload
   → PdfParser (text / scanned / encrypted)
   → OcrService (if scanned/mixed)
   → TableParser (detect + merge multi-page tables)
   → InformationExtractor (fields + products + clauses + docs)
   → ExportService (JSON / Excel / CSV)
```

AI is behind `AIExtractionService`. Default is `NoOpAIExtractionService` (fully offline, free, instant). Enable it by editing `.env` — **there is no API key field in the app itself**; configuration is `.env`-only, read once at startup:

```env
AI_ENABLED=true
AI_PROVIDER=gemini
GEMINI_API_KEY=...
```

(or `AI_PROVIDER=ollama` / `OLLAMA_MODEL=llama3.2` for a local model, no key needed). Restart the app after editing — the sidebar's "Online AI extraction" section will show the provider as already configured, with nothing further to type or save in the browser.

When enabled, AI extraction reads the **whole document**, not just a prefix: the
extracted text is split into page-group chunks (`AI_CHUNK_MAX_PAGES` /
`AI_CHUNK_MAX_CHARS` in `.env`), each chunk is sent to the model with an
extraction prompt, and every chunk's findings are merged into the result —
header fields fill in the first time they're found, products/documents/clauses
are unioned across every chunk. The rule-based result is always the starting
point, so a chunk that fails or a model that returns nothing never loses
offline data; it's purely additive. This means large tenders (hundreds of
pages) take proportionally longer and cost proportionally more per document
when AI is on — offline mode remains instant and free for everyday use.

---

## Tests

```bat
pytest -q
```

---

## Notes

- Rule-based extraction works best on clearly labeled NITs; messy layouts may leave some fields as `null`.
- Product extraction prefers BOQ tables; paragraph BOQs are converted to structured rows.
- For production hardening, add auth, persistent job storage, and worker queues around the FastAPI layer.
