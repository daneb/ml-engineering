# ml-engineering

Pulumi (Python) infrastructure for working through *Machine Learning
Engineering on AWS — Second Edition* (Joshua Arvin Lat, Packt) on a $100
free-tier credit budget, without hand-clicking IAM and SageMaker Studio
setup every time you rotate accounts.

It provisions a SageMaker Studio environment, a Bedrock knowledge base for
the Chapter 2 RAG exercise, and on-demand GPU model endpoints, behind two
cost guardrails (an instance-type IAM gate and an AWS Budget), plus scripts
to check spend and shut everything billable down at the end of a session.

---

## Key usage

Everyday commands, grouped by when you need them. Pulumi commands run from
inside the named project folder; scripts run from the repo root.

### Every session

| When | Where | Command |
| --- | --- | --- |
| Start of session | repo root | `venv/bin/python scripts/open_studio.py --profile mle-aws` (or console: SageMaker AI → Domains → User profiles → dane → Launch → Studio) |
| Check what's billing right now | repo root | `venv/bin/python scripts/check_credits.py --profile mle-aws` |
| **End of session (always)** | repo root | `venv/bin/python scripts/stop_all_apps.py --profile mle-aws --region us-east-1` |
| Confirm nothing is left running | repo root | re-run `check_credits.py` |

`stop_all_apps.py` stops Studio apps, then asks before deleting endpoints,
stopping classic notebook instances, or terminating EMR clusters. It lists
in-progress jobs without stopping them. See
[End-of-session cleanup](#end-of-session-cleanup) for details.

### GPU endpoints (the expensive part)

| Task | Where | How |
| --- | --- | --- |
| Start an endpoint | `endpoints/` | Add an entry to `ml-engineering-endpoints:endpoints` in `Pulumi.dev.yaml`, then `pulumi up` |
| Stop one endpoint | `endpoints/` | Remove its entry from `Pulumi.dev.yaml`, then `pulumi up` |
| After `stop_all_apps.py` deleted a Pulumi endpoint | `endpoints/` | Remove its entry, then `pulumi refresh --yes && pulumi up` |
| See what's declared | `endpoints/` | `pulumi stack output endpoint_names` |

Billing starts the moment `pulumi up` creates the endpoint and continues
24/7 until it's deleted.

### Base infrastructure

| Task | Where | Command |
| --- | --- | --- |
| Preview any change | `base/` | `pulumi preview --diff` |
| Apply a change | `base/` | `pulumi up` |
| Show IDs (bucket, role, KB, domain) | `base/` | `pulumi stack output` |
| Resync state after console changes | `base/` | `pulumi refresh` |

`base/` is safe to `pulumi up` at any time; it never launches compute. Don't
`pulumi destroy` it between sessions (see
[Pausing and resuming](#pausing-and-resuming-across-days)).

### Bedrock knowledge base (Chapter 2)

| Task | Where | How |
| --- | --- | --- |
| Get the KB ID for a notebook | `base/` | `pulumi stack output knowledge_base_id` |
| Query it from a notebook | Studio | `bedrock-agent-runtime` `retrieve()` with `managedSearchConfiguration` (not `vectorSearchConfiguration`) — see [Querying](#querying-it-from-notebooks) |
| Re-sync after adding files to S3 | console | Bedrock → Knowledge bases → data source → **Sync** |

### Tip: skip the passphrase prompt

```bash
export PULUMI_CONFIG_PASSPHRASE_FILE=~/.pulumi/mle-aws.passphrase   # file with chmod 600
```

---

## Project structure

```
ml-engineering/
├── README.md
├── requirements.txt              # shared deps for both Pulumi projects (pulumi-aws >=7.46,<8)
├── venv/                         # shared virtualenv, referenced by each Pulumi.yaml
│
├── base/                         # Pulumi project: cheap, always-on — safe to `pulumi up` anytime
│   ├── Pulumi.yaml
│   ├── Pulumi.dev.yaml           # region, profile, budget email, allowed instance types
│   ├── __main__.py               # wires the infrastructure/ modules together; stack outputs
│   └── kb-import.json            # record of the one-time `pulumi import` of the console-built KB
│
├── endpoints/                    # Pulumi project: GPU-billed — deliberate, opt-in `pulumi up`
│   ├── Pulumi.yaml
│   ├── Pulumi.dev.yaml           # endpoints: [...] — THE file to edit to start/stop GPU billing
│   └── __main__.py               # StackReference into base/ for execution_role_arn
│
├── infrastructure/               # resource-builder modules, imported by both projects
│   ├── networking.py             # default VPC + subnets lookup
│   ├── storage.py                # the project S3 bucket
│   ├── iam.py                    # Studio execution role + knowledge-base retrieve access
│   ├── sagemaker_studio.py       # Studio domain + user profile
│   ├── guardrails.py             # Guardrail #1: IAM Deny on oversized instance types
│   ├── budget.py                 # Guardrail #2: AWS Budget, email at 50/80/100%
│   ├── bedrock_knowledge_base.py # managed KB, data source, KB role/policies (imported, see ADR 0001)
│   └── model_endpoints.py        # config-driven SageMaker model endpoints (vLLM DLC)
│
├── scripts/
│   ├── open_studio.py            # opens Studio via a fresh presigned URL (IAM-auth domain)
│   ├── stop_all_apps.py          # end-of-session cleanup — run this, not `pulumi destroy`
│   └── check_credits.py          # credit balance + what's billing right now
│
└── docs/
    └── adr/
        └── 0001-bedrock-knowledge-base-in-pulumi.md
```

### Two projects, one cost boundary

The repo is two separate Pulumi projects sharing one `infrastructure/`
module folder and one `venv/`:

- **`base/`** covers networking, IAM, the S3 bucket, the Studio domain, the
  Bedrock knowledge base, and both guardrails. It's cheap and long-lived
  (fractions of a cent a day when idle).
- **`endpoints/`** covers GPU-backed SageMaker real-time endpoints, which
  bill by the hour until deleted. It reads `execution_role_arn` from `base`
  through a `StackReference` instead of creating its own IAM.

Of the book's nine chapters, real-time endpoints (Chapter 2, and later
Chapters 3, 7 and 8) are the only resources that are both persistent and
GPU-billed. Training jobs, SageMaker Processing (Chapter 6), EMR/Iceberg
(Chapter 4), Feature Store/Athena/Lake Formation (Chapter 5) and Pipelines
(Chapter 9) are run ad hoc from Studio notebooks, as the book does, and
aren't tracked by this IaC. So this split is the whole cost boundary: a
`pulumi up` in one project can never diff or recreate anything in the other.

The knowledge base lives in `base/` because a managed KB has no instance
behind it, only storage and per-query cost (ADR 0001, decision 4).

### What each stack exports

| Stack | Output | Used for |
| --- | --- | --- |
| `base` | `studio_domain_id`, `studio_user_profile_name` | Read by `open_studio.py` to generate a presigned Studio URL |
| `base` | `studio_domain_url` | Reference only — opening it directly fails with "Invalid or Expired Auth Token" |
| `base` | `bucket_name` | Notebook data paths |
| `base` | `execution_role_arn` | Consumed by `endpoints/` via `StackReference` |
| `base` | `knowledge_base_id`, `data_source_id` | Chapter 2 notebooks |
| `base` | `allowed_instance_patterns` | What the instance gate currently permits |
| `endpoints` | `endpoint_names` | Endpoint names to call from notebooks |

---

## First-time setup

### 1. Install prerequisites

```bash
brew install pulumi/tap/pulumi awscli
pulumi version && aws --version
```

Activate the shared virtualenv before running scripts or any manual
`python`/`pip`. Pulumi itself doesn't need this, since each `Pulumi.yaml`
points at `../venv`:

```bash
source venv/bin/activate
```

### 2. Configure the AWS CLI profile

```bash
aws configure --profile mle-aws
# Access Key ID / Secret Access Key: from the account's IAM user
# Default region: us-east-1
# Default output format: json
```

Use an IAM user with programmatic access, not root credentials. If the
account doesn't have one yet, create a user in IAM, attach
`AdministratorAccess` (Pulumi needs to create IAM roles and policies), and
create an access key.

### 3. Log in to a local Pulumi backend

No Pulumi Cloud account is needed; state lives on disk:

```bash
pulumi login --local
```

### 4. Initialize both stacks

```bash
cd base
pulumi stack init dev
pulumi config set aws:profile mle-aws
pulumi config set ml-engineering-base:budget_alert_email you@example.com
pulumi install

cd ../endpoints
pulumi stack init dev
pulumi config set aws:profile mle-aws
pulumi install
```

`budget_alert_email` is required, so `base` won't deploy without it. The
`endpoints` stack needs only the AWS profile.

### 5. Deploy the base stack

```bash
cd base && pulumi preview && pulumi up
```

Don't run `pulumi up` in `endpoints/` until you reach an exercise that needs
a hosted model. That step starts GPU billing immediately.

### 6. One-time Bedrock model enablement

Bedrock enables third-party models (such as Claude) the first time someone
in the account invokes them, which creates an AWS Marketplace subscription.
The Studio execution role deliberately lacks `aws-marketplace:Subscribe`, so
do this once as your admin user: in the Bedrock console, open **Playgrounds
→ Chat**, pick the model, submit Anthropic's use-case form if prompted, and
send one message. After about two minutes, notebooks can call that model.

If model calls then fail with throttling or "tokens per day" errors, that's
an account-level Bedrock quota, which needs a Service Quotas request or a
support case, not an IAM change.

---

## End-of-session cleanup

```bash
venv/bin/python scripts/stop_all_apps.py --profile mle-aws --region us-east-1
```

It queries the live account, not Pulumi state, so it catches resources no
matter what created them:

| Resource | Action |
| --- | --- |
| Studio apps (JupyterLab/CodeEditor kernels) | Stopped automatically; files survive |
| Real-time endpoints, in any billing state (including `Creating`) | Asks, then deletes |
| Classic notebook instances | Asks, then stops |
| EMR clusters (Chapter 4), including idle `WAITING` ones | Asks, then terminates |
| Training, processing, batch transform, tuning jobs | Listed only, never stopped |
| `base` stack resources (domain, role, bucket, KB, guardrails) | Left alone on purpose |

If it deletes an endpoint that's still declared in
`endpoints/Pulumi.dev.yaml`, it says so. Remove that entry and run
`pulumi refresh --yes && pulumi up` in `endpoints/`; otherwise the next
refresh recreates the endpoint and GPU billing restarts.

### Pausing and resuming across days

Safe to leave running for days at close to $0: the whole `base` stack. The
domain has no idle charge, and the bucket, Studio storage and knowledge base
cost fractions of a cent a day at this scale. Don't `pulumi destroy` it
between sessions; that saves nothing and costs you recreation time.

What bills while idle: running Studio apps and real-time endpoints, both of
which `stop_all_apps.py` handles.

When you come back, open Studio with a fresh presigned URL:

```bash
venv/bin/python scripts/open_studio.py --profile mle-aws
```

or use the console's **Launch** button. Useful flags: `--print-only` prints
the link instead of opening it, `--session-hours` sets the session length
(0.5 to 12, default 12), and `--domain-id`/`--user` skip the Pulumi lookup
and its passphrase prompt.
The domain uses IAM auth, so the bare domain URL, bookmarks and old
presigned links all fail with "Invalid or Expired Auth Token". A presigned
link must be opened within 5 minutes, and the session it starts lasts up to
12 hours. Your files and the packages installed in your home directory are
where you left them. No `pulumi up` is needed unless you've done a full
`pulumi destroy`.

---

## Checking credits

```bash
venv/bin/python scripts/check_credits.py --profile mle-aws
```

It reports two numbers on two different clocks:

- **Free Tier credit balance** is the real $100 figure, from AWS's
  `GetAccountPlanState` API. It's authoritative but can lag by hours.
- **Live SageMaker resources** are the Studio apps and endpoints running
  right now, with an estimated cost so far. This part is real time and is
  your actual "did something change" signal.

A good habit is to run it after deploying anything and again after
`stop_all_apps.py` at the end of a session.

---

## Cost guardrails

### Guardrail #1: the instance-type gate

An IAM **Deny** on the execution role rejects any training job,
processing/transform job, hosted endpoint, or classic notebook instance
using an instance type outside the allow-list, regardless of what the book's
screenshots suggest. The default allow-list lives in `base/Pulumi.dev.yaml`:

```
ml.t3.*, ml.t2.*, ml.m5.*, ml.c5.*, ml.g4dn.xlarge, ml.g5.xlarge, ml.g5.2xlarge
```

`ml.g5.2xlarge` (one A10G GPU, 32 GB RAM) is the ceiling, enough for any
small-to-mid open model this book uses. The `ml.g5.24xlarge` from the first
Chapter 2 attempt (4× A10G, about $10/hr) is hard-blocked.

**The gap, which is an AWS limitation:** AWS extends the
`sagemaker:InstanceTypes` condition key to job and endpoint creation, but
not to `CreateApp`, the call Studio makes when you pick a compute size from
the JupyterLab/CodeEditor dropdown
([aws/amazon-sagemaker-examples#1499](https://github.com/aws/amazon-sagemaker-examples/issues/1499)).

- **Hard-blocked:** hosted endpoints, training/processing/transform jobs,
  classic notebook instances. This is what cost the first $100.
- **Needs your judgment:** the kernel size picked inside Studio. The domain
  defaults to `ml.t3.medium` as a nudge, but the dropdown allows anything.

If a later exercise genuinely needs more than `ml.g5.2xlarge`, raise
`allowed_instance_patterns` for that exercise and lower it again afterward.

### Guardrail #2: the budget

An AWS Budget emails `budget_alert_email` at 50%, 80% and 100% of the
monthly limit (`monthly_budget_usd`, default 100).

### Instance and model sizing

| Chapter type | What you actually need | Why |
| --- | --- | --- |
| RAG / agents (Ch 2) hosting your own LLM | `ml.g5.xlarge` + a 1B–8B model | Fits in 24 GB VRAM on one A10G. JumpStart defaults to the largest variant; pick the smallest. |
| RAG / agents where self-hosting isn't required | Bedrock (Claude Haiku, Nova Micro/Lite) | No idle cost, pay per token, nothing to forget to delete. |
| Embeddings for RAG | Managed KB, Bedrock Titan, or a small CPU model | Embedding models never need a GPU. |
| Classic ML (XGBoost/sklearn) | `ml.m5.xlarge` or `ml.c5.xlarge` | No GPU workload. |
| Deep learning training (Ch 7–8) | `ml.g5.xlarge` | One GPU is enough for book-scale runs. Check the OOM error before going bigger. |
| Studio kernel | `ml.t3.medium` (default) | You write code in the kernel; models run in the jobs and endpoints you launch. |

Rough on-demand rates in us-east-1: `ml.t3.medium` ~$0.05/hr,
`ml.m5.xlarge` ~$0.23, `ml.g5.xlarge` ~$1.21, `ml.g5.2xlarge` ~$1.52,
`ml.g5.24xlarge` ~$10.18. At ~$1.20–1.50/hr, $100 buys 65–80 GPU hours if
you delete endpoints between sessions.

---

## Bedrock knowledge base

A fully managed knowledge base (`type: MANAGED`: AWS owns the embedding
model and vector store) with an S3 connector data source pointed at the
project bucket. It was built with the console's quick-create flow and then
adopted into Pulumi. [ADR 0001](docs/adr/0001-bedrock-knowledge-base-in-pulumi.md)
records why and how.

- `infrastructure/bedrock_knowledge_base.py` hardcodes the console-generated
  names (`..._isltx`). These are immutable, so changing them would force a
  destroy-and-recreate of the ingested KB. Treat that file's literals as
  fixed.
- `base/kb-import.json` is the record of the one-time `pulumi import`. It's
  only needed again when adopting the KB into a fresh stack.
- The data source's `connectorParameters` JSON uses compact separators on
  purpose, to match the string AWS stores and avoid a spurious update on
  every `pulumi up`.

### Access

`iam.py`'s `grant_knowledge_base_access()` adds an inline policy on the
Studio execution role allowing `bedrock:Retrieve` and
`bedrock:RetrieveAndGenerate` on this one KB. Neither
`AmazonSageMakerFullAccess` nor `AmazonBedrockReadOnly` grants those
data-plane actions. Model invocation is covered by the role's main inline
policy.

### Querying it from notebooks

Managed KBs reject the book's `vectorSearchConfiguration`; use
`managedSearchConfiguration` instead, or omit `retrievalConfiguration` to
get managed reranking by default:

```python
response = runtime.retrieve(
    knowledgeBaseId=KNOWLEDGEBASE_ID,
    retrievalQuery={"text": query},
    retrievalConfiguration={"managedSearchConfiguration": {"numberOfResults": 3}},
)
```

Apply the same substitution inside
`knowledgeBaseConfiguration.retrievalConfiguration` for
`retrieve_and_generate()`. If boto3 reports `managedSearchConfiguration` as
an unknown parameter, upgrade it in the notebook:
`%pip install -U boto3 botocore`, then restart the kernel.

---

## Model endpoints

Endpoints are declared as data in `endpoints/Pulumi.dev.yaml`, one entry per
chapter exercise:

```yaml
ml-engineering-endpoints:endpoints:
  - name: ch2
    hf_model_id: TinyLlama/TinyLlama-1.1B-Chat-v1.0
    instance_type: ml.g5.xlarge
    image_uri: 763104351884.dkr.ecr.us-east-1.amazonaws.com/vllm:<tag>-sagemaker
```

`pulumi up` in `endpoints/` creates a `Model`, `EndpointConfiguration` and
`Endpoint` per entry, named `mle-aws-2e-<name>-endpoint`. Removing an entry
and running `pulumi up` destroys only that endpoint.

### The container image

- **AWS's own vLLM DLC**, configured with the `SM_VLLM_MODEL` env var in
  `model_endpoints.py`. The plain Hugging Face inference container only
  accepts `{"inputs": "...", "parameters": {...}}` and breaks when an agent
  framework (such as Strands' `SageMakerAIModel`) sends an OpenAI-style
  `{"messages": [...]}` payload, which vLLM handles natively.
- **Must be the AWS-owned ECR image** (`763104351884.dkr.ecr.<region>.amazonaws.com/...`),
  not the `public.ecr.aws/deep-learning-containers/...` mirror, which
  SageMaker's `CreateModel` rejects as non-ECR.
- **Hardcoded, not resolved via the `sagemaker` SDK.** That SDK's lookup
  API broke under version churn three separate times. Check
  [AWS's DLC image list](https://aws.github.io/deep-learning-containers/reference/available_images/)
  for a current tag before reusing an old one, since tags get removed.

---

## Extending as you go through the book

- **Chapters 3, 7, 8 (more real-time endpoints):** add entries to
  `endpoints/Pulumi.dev.yaml`. Same pattern as Chapter 2, no new code.
- **New cheap, long-lived resources:** add a module to `infrastructure/`
  and wire it into `base/__main__.py`.
- **Anything created ad hoc from a notebook that bills while idle:** add it
  to `scripts/stop_all_apps.py` so the end-of-session routine still covers
  everything.
- **Adopting something built in the console:** follow the knowledge-base
  pattern: write the code to match the live resource exactly,
  `pulumi import --file <file>.json --generate-code=false`, confirm
  `pulumi preview --diff` shows no replaces, and record the decision as a
  new ADR in `docs/adr/`.

## Known leftovers

- `BucketV2`, `BucketVersioningV2` and
  `BucketServerSideEncryptionConfigurationV2` deprecation warnings come from
  `storage.py`. It's a future cleanup, unrelated to the KB work (ADR 0001).
- `pulumi preview` in `base/` shows a harmless recurring update on the
  Studio domain (`studioWebPortalSettings` with all-null fields). It's a
  provider quirk; it can be silenced with `ignore_changes` if the noise
  bothers you.
