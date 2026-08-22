# Notices and Attribution

[简体中文](NOTICE.md) | English

Smart Fridge Magnet is an independent open-source project. Product and company
names are not required to build or operate this repository.

Unless a file or subdirectory states otherwise, source code, documentation, and
project assets in this repository are provided under GPL-3.0-or-later.

## FrameFilm

Parts of `embedded/smart_fridge_magnet/` and the hardware snapshot under
`hardware/` are derived from the FrameFilm project:

- Upstream repository: <https://github.com/kirmisaki/FrameFilm>
- Firmware baseline: `562f3300774ee512061abe1e8589a4477e39afb0`
- Hardware snapshot import: `514b0361a570452f50d320a41ca61d6a9a30bf82`
- License: GNU General Public License v3.0

The upstream attribution and detailed modification notes remain in
`embedded/smart_fridge_magnet/NOTICE.md`. The corresponding license copies are
kept in `embedded/smart_fridge_magnet/LICENSE` and `hardware/LICENSE`.

`backend/data/nutrition/china_food_composition.json` is intentionally included
in the public snapshot. Keep the file and this notice when redistributing it;
independent products should verify the table's original source and applicable
redistribution terms before reuse. The data is for prototype lookup and menu
hints, not medical advice or a nutritional diagnosis.

`Icon/` retains the food/category SVGs, derived Android PNGs, and role PNGs
that the project owner confirmed come from public sources. `Icon/README.md` and
`food_map.json` maintain the mapping. This snapshot does not yet have a
per-asset provenance table, so future redistribution should add the original
page, attribution, and license for each asset. Third-party dependencies remain
governed by their package licenses and metadata.
