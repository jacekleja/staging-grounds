"""Shared URL-regex constants for web citation grammar (Form 5).

Both bin/test_citation_anchor_lint.py and bin/test_knowledge_audience_lint.py
import from here to avoid cross-script URL-grammar desync.

Form 5 canonical grammar:
  [verified: web:<url> @ <YYYY-MM-DD> (tier:T<N>, classifier:<reason>)]

Where <url> matches _WEB_URL_FRAGMENT.
"""

# URL fragment used in both the Form 5 pattern and the web_provenance extraction regex.
# Excludes whitespace (the space before '@') AND literal '@' (the separator character).
# URLs containing literal '@' (e.g. auth-bearing or per-tenant URLs) are unsupported --
# the '@' is the Form 5 date separator, so including it in the URL would be ambiguous.
# Use a redacted/canonical URL in citations instead. See sketch-A-2.md § Unknowns U6.
_WEB_URL_FRAGMENT = r"https?://[^\s@]+"
