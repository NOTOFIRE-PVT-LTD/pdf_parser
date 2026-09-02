# Tender Parser

Offline app: upload tender / NIT PDFs → extract products → download Excel.

## Quick start (Windows)

```bat
run_streamlit.bat
```

Or manually:

```bat
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
streamlit run app\streamlit_app.py
```

Open the URL shown in the terminal (usually http://localhost:8501).

## Project layout

```
app/
  streamlit_app.py      # UI entry point
  config.py             # Settings (.env)
  api/main.py           # Optional REST API (Vercel)
  models/schemas.py     # Data contracts
  parser/               # PDF + table parsing
  ocr/                  # Tesseract OCR
  extractor/            # Field + product + clause extraction
  services/
    pipeline.py         # End-to-end processing
    export_service.py   # Flat Excel / JSON / CSV export
    product_sanitize.py # Product row cleanup
  utils/                # Patterns, text helpers, product names
  static/css/           # Streamlit styles
tests/
```

## Pipeline

```
PDF upload
  → PdfParser (text / scanned / encrypted)
  → OcrService (if scanned)
  → TableParser (BOQ tables)
  → InformationExtractor (fields + products)
  → ExportService (flat Excel)
```

## Tests

```bat
pytest -q
```

## Optional API

```bat
uvicorn app.api.main:app --reload --port 8000
```

Docs at http://localhost:8000/docs
