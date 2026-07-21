---
name: investigate
description: "Use when: investigating a code problem, error, or unexpected behavior. Reports root cause and fix plan, then waits for user approval before modifying code."
---

# Investigate: Root-Cause Analysis + Approval-Gated Fix

## When to Use
- User reports an error, bug, or unexpected behavior.
- A test fails and the reason is unclear.
- The code compiles but produces wrong results.
- Before making any invasive refactoring or fix — get approval first.

## Workflow

### Step 1: Gather Context
1. Collect the error message / symptom from the user's query or terminal output.
2. Identify the relevant file(s):
   - If the error includes a stack trace, read the offending file around the failing line.
   - If no trace, ask the user for reproduce steps or the exact file.
3. Read the affected code sections and any related dependencies.
4. Check config / environment if the issue looks environment-specific.

### Step 2: Identify Root Cause
1. Analyze the code to determine **why** the symptom occurs.
2. Distinguish between:
   - **Logic error**: incorrect algorithm or condition.
   - **Type mismatch**: wrong types being passed/returned.
   - **Missing null/edge-case handling**: crash on empty input, None, etc.
   - **API change**: a called function changed signature or behavior.
   - **Config / environment**: wrong settings, missing dependencies.
3. **Do not** propose a fix yet — just record the cause clearly.
4. You can draw a graph to explain the problem. 

### Step 3: Propose Fix Plan (No Code Changes Yet)
1. Describe the fix approach in plain language.
2. List the exact files and lines that need to change.
3. For each change, state what the new code should do and why it resolves the root cause.
4. Use a format the user can scan quickly:

```
## Root Cause
(1–3 sentences explaining the real reason)
(graph)

## Fix Plan
| File | Line(s) | Change |
|------|---------|--------|
| src/foo.py | 42–50 | Replace `x` with `y` because ... |
| src/bar.py | 120 | Add None check before accessing `.name` |
```

5. **Stop here.** Do NOT read, write, or edit any files after step 2. Wait for the user's explicit approval.

### Step 4: Apply Fix (After Approval)
Only after the user responds with "approve", "yes", "go ahead", or an equivalent confirmation:
1. Apply each change from the plan.
2. After each edit, verify syntax (e.g., `py_compile` for Python).
3. Run relevant tests if available.
4. Summarize what was changed and the verification result.

## Quality Criteria
- Every fix plan includes **root cause**, **affected files**, and **exact line ranges**.
- No code is written or edited before user approval.
- After applying, at least syntax verification is done — and tests if they exist.
- If multiple root causes exist, list them all in priority order.
