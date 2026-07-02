# FinTablo Read-Only Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the first safe FinTablo integration layer that reads accounts, categories, directions, deals, partners, employees, and transactions without writing to FinTablo.

**Architecture:** Keep FinTablo access isolated in a small API client and expose a diagnostic CLI that prints counts and sample metadata. This phase does not change Google Sheets, MAX parsing, invoice matching, or FinTablo data.

**Tech Stack:** Python standard library `urllib.request`, existing `.env` loader, pytest with monkeypatched HTTP transport.

---

### Task 1: Store Configuration

**Files:**
- Modify: `.env`
- Test: manual key presence check without printing token

- [x] **Step 1: Add `FINTABLO_API_TOKEN` to `.env`**

The token is local-only and `.env` is ignored by git.

### Task 2: FinTablo Client

**Files:**
- Create: `payment_processor/fintablo_client.py`
- Test: `tests/test_fintablo_client.py`

- [ ] **Step 1: Add dataclasses and client methods**

Implement `FinTabloClient`, `FinTabloError`, `FinTabloSettings`, `load_fintablo_settings`, `list_transactions`, `list_moneybags`, `list_categories`, `list_partners`, `list_directions`, `list_deals`, `list_employees`.

- [ ] **Step 2: Add tests with fake transport**

Verify Authorization header, pagination, date filters, and error handling.

### Task 3: Diagnostic CLI

**Files:**
- Create: `scripts/fintablo_readonly_check.py`
- Test: `tests/test_fintablo_readonly_check.py`

- [ ] **Step 1: Print safe summary only**

The script reads reference lists and transactions for a period, prints counts, and never prints the API token.

- [ ] **Step 2: Add tests for summary formatting**

Use a fake client object and assert counts are shown.

### Task 4: Verification

**Files:**
- Run tests and read-only live check

- [ ] **Step 1: Run targeted tests**

Run `pytest tests/test_fintablo_client.py tests/test_fintablo_readonly_check.py -q`.

- [ ] **Step 2: Run full tests**

Run `pytest -q`.

- [ ] **Step 3: Run live read-only check**

Run `python scripts/fintablo_readonly_check.py --start 2026-07-01 --end 2026-07-01` and report only counts/samples, no token.
