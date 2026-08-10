"""Parser package."""

from app.parser.pdf_parser import PdfDocument, PdfParser, PageContent
from app.parser.table_parser import ExtractedTable, TableParser

__all__ = ["PdfDocument", "PdfParser", "PageContent", "ExtractedTable", "TableParser"]
