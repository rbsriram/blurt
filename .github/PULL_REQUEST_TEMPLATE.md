<!-- Keep PRs focused. One change, one PR. -->

## What and why

<!-- What does this change, and what problem does it solve? -->

## Scope check

- [ ] This does not add organization burden to capture (no tags/folders/etc.)
- [ ] No new runtime dependency, or I explained why one is needed

## Verification

- [ ] `ruff check blurt main.py tests` passes
- [ ] `pytest tests/test_unit.py` passes
- [ ] Integration suite passes locally (`pytest docs/test_suite.py`) or N/A
