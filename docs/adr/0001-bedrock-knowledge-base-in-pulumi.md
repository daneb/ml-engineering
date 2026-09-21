# 1. Capture the Bedrock Knowledge Base in Pulumi

## Status

Accepted — in progress (resource import underway).

## Context

Chapter 2's RAG exercise was set up through the Bedrock console's "Quick
create" flow: a fully-managed knowledge base (AWS owns the embedding model
and the vector store internally — no OpenSearch Serverless or S3 Vectors
bucket to provision) plus an S3-connector data source pointed at the
project's existing bucket, plus a console-generated IAM role
(`AmazonBedrockExecutionRoleForKnowledgeBase_isltx`) with two
console-generated managed policies. All of this existed only in the AWS
account, not in this repo.

Inspecting the installed `pulumi-aws` (6.83.4, pinned `<7.0.0` in
`requirements.txt`) showed its `bedrock.AgentKnowledgeBase` /
`AgentDataSource` schema only supports the older `VECTOR` knowledge-base
type (bring-your-own vector store) — there is no
`managed_knowledge_base_configuration` field to express what's actually
live. Downloading and inspecting `pulumi-aws==7.46.0` directly confirmed it
adds full support for `type: MANAGED` on the knowledge base and
`MANAGED_KNOWLEDGE_BASE_CONNECTOR` on the data source, matching the live
resources field-for-field.

## Decisions

1. **Bump `pulumi-aws` to `>=7.46.0,<8.0.0`.** This is a shared dependency
   (one `venv/` used by both the `base/` and `endpoints/` Pulumi projects —
   see the base/endpoints split), so the bump was verified safe for both
   before adding anything new: `pulumi preview` in each showed only
   additive, non-replacing diffs (`+tagsAll`, `+region` — new provider
   bookkeeping fields) plus deprecation warnings for the `*V2` S3 bucket
   resources (`BucketV2` → `Bucket`, etc. — left as-is, a separate future
   cleanup, not part of this change).

2. **Adopt the console-generated IAM role and its two policies as-is**,
   via `pulumi import`, rather than authoring a fresh Pulumi-owned role.
   Zero risk (no live `UpdateKnowledgeBase`/role-swap call needed), at the
   cost of keeping the console's auto-generated `_isltx` name suffix instead
   of this project's usual naming convention. `infrastructure/bedrock_knowledge_base.py`
   hardcodes these exact literal names (role, both policies, the knowledge
   base, the data source) rather than deriving them from `name_prefix` —
   IAM role/policy names and Bedrock resource names are immutable, so any
   drift from the live literal string would force a destroy-and-recreate of
   an already-ingested knowledge base on the next `pulumi up`.

3. **Policy attachment via `managed_policy_arns` on the `Role`, not
   separate `RolePolicyAttachment` resources.** `pulumi import`'s own
   generated code for the imported role used this field directly; matching
   it avoids a spurious "detach all managed policies" diff that would
   otherwise show up because the field defaults to unmanaged when omitted.

4. **Lives in `base/`, not `endpoints/`.** A managed knowledge base has no
   continuously-billed GPU/compute instance — storage and per-query cost
   only, the same profile as the S3 bucket and Studio domain. This follows
   the cost-boundary rule established when the project was split into
   `base/` (cheap, always-on) and `endpoints/` (GPU-billed, opt-in): see
   `README.md` → "Two projects, one cost boundary".

## Consequences

- The knowledge base, data source, and their IAM role/policies are now
  reproducible from code (`infrastructure/bedrock_knowledge_base.py`,
  wired into `base/__main__.py`), not console-only state.
- `base/`'s `requirements.txt` pin now requires `pulumi-aws>=7.46.0` —
  any future `base/`/`endpoints/` change inherits this floor.
- The adopted role/policies keep AWS's auto-generated `_isltx` naming
  rather than this project's `mle-aws-2e-*` convention. If ever recreated
  from scratch (not adopted from an existing console resource), give it a
  proper `mle-aws-2e-kb-*`-style name instead — the `_isltx` suffix is a
  one-off consequence of importing this specific already-live resource, not
  a pattern to repeat.
- The `*V2` S3 bucket deprecation warnings surfaced by the same version
  bump were deliberately left unaddressed — noted here so they aren't
  mistaken for fallout of this change if seen later.
