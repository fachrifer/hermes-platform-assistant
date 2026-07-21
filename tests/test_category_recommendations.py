import json

from core.category_recommendations import CategoryRecommendationLookup
from core.spreadsheet_finance import CategoryReference


def test_category_recommendation_lookup_uses_keywords(tmp_path):
    categories = tmp_path / "categories.xlsx"
    from openpyxl import Workbook
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["Kategori Pengeluaran", "Subkategori"])
    sheet.append(["Makanan", "Cafe", "Grocery"])
    workbook.save(categories)

    payload = {
        "version": 1,
        "phrases": [
            {
                "match": "dja go kopi reman ols",
                "category": "Makanan",
                "subcategory": "Cafe",
                "count": 23,
                "confidence": 1.0,
            }
        ],
        "keywords": [
            {
                "match": "americano",
                "category": "Makanan",
                "subcategory": "Cafe",
                "count": 5,
                "total": 5,
                "confidence": 1.0,
            },
            {
                "match": "familymart",
                "category": "Makanan",
                "subcategory": "Grocery",
                "count": 8,
                "total": 10,
                "confidence": 0.8,
            },
        ],
    }
    path = tmp_path / "category_recommendations.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    lookup = CategoryRecommendationLookup(
        str(path),
        category_reference=CategoryReference(str(categories)),
    )

    assert lookup.recommend("Kopi Reman", "QRIS") == ("Makanan", "Cafe")
    assert lookup.recommend("Family Mart", "cranberry americano") == ("Makanan", "Cafe")
    assert lookup.recommend("FamilyMart Kepu", "belanja") == ("Makanan", "Grocery")
