# Security Policy

This repository is teaching material. It ships a **deliberately vulnerable** agent configuration
behind feature flags (`STOCKROOM_WEAKNESSES=injection_unguarded`, `naive_retry`, ...) and a policy
document in `data/policy_docs/` that contains a **seeded prompt injection**. Those are features of
the Day 3 and Day 4 labs, not vulnerabilities to report. The shipped default has every weakness
fixed, and the red-team suite in `promptfooconfig.yaml` is part of the CI gate.

## What is in scope

- Ways to make the *default* (all-fixed) harness execute an injected instruction, leak the system
  prompt, exceed `MAX_STEPS` / `TOKEN_BUDGET`, or call a tool with arguments that bypass validation.
- Problems in the GitHub Actions workflows or the IAM documents in `docs/aws/` that could expose
  credentials, widen the OIDC trust, or let a pull request trigger Bedrock spend.
- Secrets or personal data accidentally committed to the repository.

## How to report

Please use GitHub's private vulnerability reporting for this repository
(**Security → Report a vulnerability**) rather than a public issue, so the details stay private
until a fix is published. You should hear back within seven days.

If you cannot use that form, email the maintainer listed on the
[Genial Labs organisation page](https://github.com/genial-labs-ai).

## Supported versions

Only the `main` branch is maintained. Fixes land there and are tagged in the next release.
