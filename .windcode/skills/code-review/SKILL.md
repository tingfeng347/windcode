---
name: code-review
description: Review code changes for correctness defects, behavioral regressions, security risks, and missing tests. Use when asked to inspect a diff, branch, pull request, commit, or uncommitted changes before merge.
---

# Code Review

Review the requested change as a senior engineer. Prioritize behavior and evidence over style.

## Workflow

1. Establish the review scope from the user's request. If no scope is given, inspect the current
   working-tree diff and relevant surrounding code without modifying files.
2. Read repository instructions and identify the contracts, callers, and tests affected by each
   change.
3. Verify suspicious behavior against concrete code paths. Run focused, non-mutating checks when
   they materially increase confidence.
4. Look specifically for incorrect state transitions, incomplete error handling, async or resource
   lifecycle issues, security boundary violations, compatibility breaks, and missing regression
   coverage.
5. Report only actionable findings. Do not invent a finding to make the review look complete.

## Output

Lead with findings ordered by severity. For every finding:

- State the observable failure or risk, not merely the implementation detail.
- Cite the exact file and line.
- Explain the triggering conditions and likely impact.
- Suggest the smallest reasonable direction for correction when it is not obvious.

After the findings, list open questions or assumptions only when they affect the verdict. End with a
brief summary and remaining test gaps. If no findings exist, say so clearly and state any residual
risk or validation that was not performed.

Do not edit code unless the user explicitly asks for fixes after or as part of the review.
