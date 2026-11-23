# Sky Pilot fixtures

Sanitized captures of the real Synty Store "My Downloads" (Sky Pilot) markup, taken 2026-11-22.

- `sky_pilot_library_page.html`: library page 1 (15 bundles plus the next-page link).
- `sky_pilot_bundle_detail.html`: one bundle page with Icon, Unity, Source Sprites and Unreal files.

- `products_sample.json`: 7 products trimmed from the **public** `syntystore.com/products.json` (2026-11-22).
  These cover Pack Content first, Technical Info first, Animation List, no tabs, and a Bundle. Public data, so no
  sanitizing is needed, but keep it free of account data.

Sanitizing rules, which must be applied before committing any new capture:
- Keep only the `div.sky-pilot` element. Drop `script`, `style`, `form` and `input`.
- Replace every run of 6+ digits (customer, order, item and download IDs) with a fake ID of the same length.
- Replace email addresses (plain or `%40`-encoded) with `user%40example.com`. Download links embed the
  account email.
- Replace the account holder's name.
- Check that the real customer ID, email and name no longer appear.

Raw captures come from `discover_portal.bat`. They contain personal data and stay in the git-ignored
`.data/discovery/`.
