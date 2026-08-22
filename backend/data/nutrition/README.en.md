# Nutrition Data

[简体中文](README.md) | English

`china_food_composition.json` is the backend prototype's complete nutrition
table with 1,657 records. When the database has no nutrition rows,
`backend/scripts/setup_demo_data.py` imports this file first and falls back to
the small synthetic records embedded in the script only when the file is
absent.

`backend/src/database.py::import_nutrition_data` parses food identifiers,
names, edible portions, energy, macronutrients, vitamins, and minerals. The
data is for prototype lookup and menu hints only; it is not medical advice, a
diagnosis, or an individualized nutrition prescription.

This table is intentionally included in the public snapshot. Independent
products should verify the table's original source and applicable redistribution
terms before reuse. See the root [`NOTICE.en.md`](../../../NOTICE.en.md) for
project attribution.
