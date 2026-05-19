"""Eval harness assets — Phase D6.1.

Each subdirectory of `golden/` is one curated case:

    golden/
    ├── case_001_clean_approval/
    │   ├── claim.pdf        # the input the agent sees
    │   └── expected.json    # what we expect the agent to produce
    └── case_002_outside_vigencia/
        ├── claim.pdf
        └── expected.json

`expected.json` shape:

    {
      "name":  "Clean approval — Carlos Rodríguez rear-end",
      "notes": "Why this case exists (e.g., regression we hit on YYYY-MM-DD)",
      "expected": {
        "decision":      "APPROVED",   // APPROVED | REJECTED | MANUAL_REVIEW
        "severity":      "LOW",        // LOW | HIGH | TOTAL_LOSS
        "must_flag":     [],           // substrings that MUST appear in reasoning
        "must_not_flag": ["policy_expired"]
      }
    }

The runner (observability/evaluation.py) discovers cases by listing
this folder. Add a new case by creating a folder with the two files.
"""
