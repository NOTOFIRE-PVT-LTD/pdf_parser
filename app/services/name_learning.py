"""Learn product-name corrections across PDFs."""

from __future__ import annotations

from functools import lru_cache

from app.config import Settings, get_settings
from app.utils.product_name import ProductNameLearning


@lru_cache
def get_name_learning() -> ProductNameLearning:
    settings = get_settings()
    return ProductNameLearning(settings.cache_dir / "product_name_learnings.json")
