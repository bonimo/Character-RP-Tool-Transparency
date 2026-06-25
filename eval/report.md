# Eval Report

**Model:** `igorls/gemma-4-12B-it-heretic-GGUF:latest`  
**Date:** 2026-06-23 20:38

## Logic Tests

7/7 passed

| Test | Result | Detail |
|------|--------|--------|
| parser_basic_fields | PASS | 16 fields parsed, pick fields correct |
| parser_tension_collision | PASS | TENSION LEVEL parsed before TENSION, no collision |
| parser_think_strip | PASS | think block stripped, fields parsed cleanly |
| parser_multiline_continuation | PASS | multi-line field continuation joined correctly |
| assertiveness_distributions | PASS | 5 dispositions × 4 levels all within 5pp at N=5000 |
| id_validation_regex | PASS | 6 valid + 8 invalid IDs verified |
| config_placeholder_validation | PASS | all 7 required placeholders present in config |
