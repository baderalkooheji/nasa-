---
document_id: "TEST-DOC-1"
title: "TEST FIXTURE: Slope and traversability notes"
source_url: "https://example.invalid/test-fixture-1"
organization: "marswalk-ai test suite"
document_type: "Test fixture"
scientific_topic: "terrain_traversability"
reliability_category: "test_fixture"
---

# Slope derivation

Slope is derived from a digital elevation model with a 3x3 finite-difference kernel. Cell size limits which obstacles are resolved.

# Traversability

Walking cost rises with slope. Project limits for walking routes are configuration values used for planning, not safety certifications.
