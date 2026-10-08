# AWS Policies for the Nightly Live-Mode Workflow

Two JSON documents, both with placeholders to replace (`<ACCOUNT_ID>`, `<AWS_REGION>`,
`<S3_BUCKET>`, `<S3_PREFIX>`; the repository `genial-labs-ai/system_agent_harness_aws` is already filled in):

| file | attach to | purpose |
|---|---|---|
| `oidc_trust_policy.json` | the IAM role's **trust policy** | lets GitHub Actions on `main` of this repository assume the role via OIDC; nothing else can (`aud` and `sub` conditions). |
| `iam_policy.json` | the same role's **permissions policy** | least privilege: invoke only the two configured models (via their cross-region inference profiles **and** the underlying foundation models in the profile's destination regions, which Bedrock requires), describe models/profiles, and read/write only under one S3 prefix. |

Notes:

- `bedrock:InvokeModel*` covers the Converse API; there is no separate `bedrock:Converse` action.
  The condition `bedrock:InferenceProfileArn` restricts the foundation-model statement so the
  models can only be used *through* a profile in your account.
- If you change `AGENT_MODEL_ID` / `JUDGE_MODEL_ID`, update both ARN lists. The destination
  regions of a `us.` profile are listed with `aws bedrock get-inference-profile`.
- Anthropic models additionally require the one-time use-case form
  (`aws bedrock put-use-case-for-model-access`) and AWS Marketplace subscription permissions for
  the *first* invocation in an account; see the README.
- The optional Bedrock Evaluations lab (Day 4) needs a separate **service role** for the
  evaluation job (`bedrock.amazonaws.com` trust, S3 read on the dataset prefix and write on the
  output prefix) plus `bedrock:CreateEvaluationJob` / `GetEvaluationJob` / `ListEvaluationJobs`
  and `iam:PassRole` on that service role for the caller. It is deliberately not in
  `iam_policy.json`: the workshop never submits an evaluation job unless
  `STOCKROOM_CONFIRM_AWS_SPEND=1` is set explicitly.
- CloudWatch GenAI observability (Day 2, live) needs the X-Ray / CloudWatch Logs permissions
  listed in the AWS "Add observability to your Amazon Bedrock AgentCore resources" page; they are
  also outside this policy because they are not part of the CI gate.
- The trust policy's `sub` is pinned to `main`. For PR-triggered live runs you would add
  `repo:genial-labs-ai/system_agent_harness_aws:pull_request`; the workshop intentionally keeps live runs off PRs.
