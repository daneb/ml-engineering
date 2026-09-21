# ml-engineering

Pulumi (Python) infrastructure for working through *Machine Learning
Engineering on AWS — Second Edition* (Joshua Arvin Lat, Packt) on a $100
free-tier credit budget, without hand-clicking IAM/SageMaker Studio setup
every time you rotate accounts.

Covers:

- Default VPC networking (no custom VPC needed)
- One S3 bucket for datasets/models/pipeline artifacts
- A scoped SageMaker Studio execution role (SageMaker + Bedrock + bucket access)
- A SageMaker Studio domain + your user profile
- **Guardrail #1**: a hard IAM Deny that blocks any SageMaker training job,
  processing job, hosted endpoint, or notebook instance from using an
  instance type outside an approved small-instance list
- **Guardrail #2**: an AWS Budget that emails you at 50/80/100% of a $100
  monthly spend

## Two projects, one cost boundary

This repo is two separate Pulumi projects, not one:

- **`base/`** — networking, IAM, the S3 bucket, the Studio domain, both
  guardrails. Cheap and long-lived (fractions of a cent/day idle). Safe to
  `pulumi up` anytime.
- **`endpoints/`** — GPU-backed SageMaker real-time model endpoints
  (currently just Chapter 2's vLLM endpoint). Billed by the hour,
  continuously, until deleted. It reads `execution_role_arn` from `base` via
  a `StackReference` rather than creating its own IAM.

Of the book's 9 chapters, real-time endpoints (ch2, and later ch3/ch7/ch8)
are the only kind of resource here that's both persistent *and* GPU-billed —
training jobs, SageMaker Processing (ch6), EMR/Iceberg (ch4), Feature
Store/Athena/Lake Formation (ch5), and Pipelines (ch9) are all run ad hoc
from Studio notebooks per the book, not tracked by this IaC. So this two-way
split is the whole cost boundary that matters: everything expensive lives in
`endpoints/`, everything else lives in `base/`, and a plain `pulumi up` in
one can never diff or recreate a resource in the other.

---

## 1. Install prerequisites

```bash
brew install pulumi/tap/pulumi
pulumi version

brew install awscli
aws --version
```

Activate the shared virtualenv from the repo root before running anything
that needs it directly (`scripts/*.py`, or any manual `python`/`pip` use —
`pulumi` itself doesn't need this, it reads `virtualenv: ../venv` from each
project's `Pulumi.yaml` on its own):

```bash
source venv/bin/activate
```

## 2. Configure the AWS CLI profile for your wife's account

```bash
aws configure --profile mle-aws
# Access Key ID / Secret Access Key: from the new account's IAM user
# Default region: us-east-1
# Default output format: json
```

Use an IAM user with programmatic access, not root credentials. If this
account doesn't have one yet: AWS Console → IAM → Users → Create user →
attach `AdministratorAccess` for now (Pulumi needs to create IAM
roles/policies) → create an access key.

## 3. Log in to a local Pulumi backend

No Pulumi Cloud account needed for a solo project — state lives on disk:

```bash
cd /Users/danebalia/Repos/ml-engineering
pulumi login --local
```

## 4. Initialize the stacks

This is split into **two independent Pulumi projects** so that a routine
infra change can never accidentally touch — or bill for — a GPU endpoint.
See [Two projects, one cost boundary](#two-projects-one-cost-boundary) below
for why.

```bash
cd base
pulumi stack init dev
pulumi config set aws:profile mle-aws
pulumi config set ml-engineering-base:budget_alert_email you@example.com
pulumi install
```

`budget_alert_email` is required — `base` won't deploy without it (that's
intentional; the budget alert is the whole point).

```bash
cd ../endpoints
pulumi stack init dev
pulumi config set aws:profile mle-aws
pulumi install
```

`endpoints` needs no config of its own beyond the AWS profile — it reads
`execution_role_arn` from `base` via a Pulumi `StackReference`, and starts
with an empty endpoint list (nothing deployed, nothing billed).

## 5. Deploy

```bash
cd base && pulumi preview && pulumi up
```

This is the part that's safe to run anytime — bucket, IAM role, Studio
domain, guardrails, budget. Outputs include your bucket name, execution role
ARN, and the SageMaker Studio domain URL.

**Don't `pulumi up` in `endpoints/` yet** — that's a separate, deliberate
step (see [Model endpoints](#model-endpoints--declared-in-config-resolved-in-studio)
below) that launches a real GPU instance and starts billing immediately.

---

## The instance-type gate — what it does and doesn't cover

The gate is an IAM **Deny** on the execution role: any attempt to run a
training job, processing/transform job, hosted endpoint, or classic
notebook instance with an instance type outside the allow-list is rejected
by AWS itself, regardless of what the book's screenshots show. Default
allow-list (in `base/Pulumi.dev.yaml`):

```
ml.t3.*, ml.t2.*, ml.m5.*, ml.c5.*, ml.g5.xlarge, ml.g5.2xlarge
```

`ml.g5.2xlarge` is the ceiling — single A10G GPU, 32GB RAM. That's enough
for any single small-to-mid open model this book uses. The g5.25xlarge from
your Chapter 2 experience (4× A10G, ~$10/hr) is hard-blocked.

**Important gap, and it's an AWS limitation, not a hole in this policy**:
AWS only extends the `sagemaker:InstanceTypes` condition key to job- and
endpoint-creation calls. It does **not** extend it to `CreateApp` — the API
call Studio makes when you pick a compute size from the JupyterLab/
CodeEditor kernel dropdown *inside* Studio. There is no IAM condition that
blocks that dropdown; other people have hit this same wall
([github.com/aws/amazon-sagemaker-examples/issues/1499](https://github.com/aws/amazon-sagemaker-examples/issues/1499)).
So:

- **Covered (hard-blocked)**: deploying a hosted endpoint, launching a
  training/processing/transform job, creating a classic notebook instance —
  this is what actually cost you the $100 in Chapter 2.
- **Not covered (needs your judgment)**: picking a kernel instance size
  inside the Studio IDE itself. The domain's default is set to
  `ml.t3.medium` as a nudge, but the dropdown will still let you pick
  anything if you click it. This is the one place the model list below
  matters most.

## Instance & model sizing for the book

| Chapter type | What you actually need | Why |
| --- | --- | --- |
| RAG / agents (Ch 2) hosting your own LLM | `ml.g5.xlarge` + a 3B–8B parameter model (Llama 3.2 3B Instruct, Phi-3-mini, Mistral 7B Instruct) | Fits in 24GB VRAM on a single A10G. JumpStart's default suggestion is usually the largest variant — pick the smallest one in the dropdown instead. |
| RAG / agents, if the book's exercise doesn't strictly require self-hosting | Amazon Bedrock (Claude Haiku, Amazon Nova Micro/Lite) instead of a SageMaker endpoint | No idle-instance cost at all — pay per token, nothing to forget to delete. Worth checking each chapter before deploying to SageMaker. |
| Embeddings for RAG retrieval | Bedrock Titan Embeddings, or a small CPU-only embedding model | Embedding models are tiny; never needs GPU. |
| Classic ML (SageMaker fundamentals, structured data, XGBoost/sklearn) | `ml.m5.xlarge` or `ml.c5.xlarge` (CPU only) | No GPU workload here — using one just burns credits for nothing. |
| Deep learning training exercises (Ch 7–8) | `ml.g5.xlarge` | Single GPU is enough for book-scale toy training runs. Only go bigger if a job genuinely OOMs — check the error before assuming you need more. |
| SageMaker Studio kernel (interactive coding) | `ml.t3.medium` (default) | You're writing/testing code, not running the model, in the kernel itself — the model runs in the job/endpoint you launch from it. |

Rough hourly rates (us-east-1, on-demand): `ml.t3.medium` ~$0.05,
`ml.m5.xlarge` ~$0.23, `ml.g5.xlarge` ~$1.2–1.5, `ml.g5.2xlarge` ~$1.5,
`ml.g5.24xlarge` ~$10.18 (this is what you had). At $1.2–1.5/hr, $100 buys
you 65–80 hours of GPU time — enough to work through every hands-on GPU
exercise in the book if you delete endpoints between sessions.

---

## Pausing and resuming across days

You said the real pattern is: work through part of a chapter, stop for 2–3
days, come back. Here's what that costs and what to do:

**What's safe to leave running for days at ~$0 cost:**

- The Studio domain, IAM role, S3 bucket (the `base` stack) — no idle
  charge for the domain itself; the bucket and EFS storage cost fractions
  of a cent per day at this scale.
- **Don't `pulumi destroy` the `base` stack between sessions.** It gains you
  nothing (the domain isn't costing you anything idle) and costs you a few
  minutes of recreation next time.

**What actually bills while idle, and what to do before you stop for the day:**

- Any running Studio App (JupyterLab/CodeEditor kernel) — bills by the hour
  whether you're typing or not.
- Any real-time SageMaker endpoint — bills 24/7 until deleted. This is what
  cost you the $100.

Run this before closing your laptop:

```bash
venv/bin/python scripts/stop_all_apps.py --profile mle-aws --region us-east-1
```

It stops every running Studio app and lists any live endpoints, asking
before deleting them. When you come back in 2–3 days: just reopen Studio
from the domain URL — everything (files, installed packages in your EFS
home directory) is exactly where you left it. No `pulumi up` needed unless
you've done a full `pulumi destroy`.

To stop an endpoint's billing specifically (rather than via the script
above), remove its entry from `endpoints/Pulumi.dev.yaml` and run
`pulumi up` from `endpoints/` — this never touches `base`.

## Checking credits and what's actually running

```bash
venv/bin/python scripts/check_credits.py --profile mle-aws
```

Two numbers, on two different clocks:

- **Free Tier credit balance** — the real `$100` figure, pulled via AWS's
  `GetAccountPlanState` API. This is authoritative but not necessarily
  instant — AWS doesn't publish a refresh interval, so don't expect it to
  move the second you run `pulumi up` (creating the domain/role/bucket
  costs ~$0 anyway — there's nothing for it to reflect yet).
- **Live SageMaker resources** — Studio apps and endpoints that are
  actually running right now, with an estimated running cost based on
  their instance type. This part IS real-time, since it's asking "what's
  live", not "what have I been billed" — this is your actual "did
  something change" signal.

A reasonable habit: run it once after `pulumi up` (expect the live-resource
section to say "nothing running" — the stack itself doesn't launch
compute), then again whenever you deploy something, and once more after
`stop_all_apps.py` at the end of a session to confirm nothing's left
running.

One terminology note: there's no `pulumi down` — the command is
`pulumi destroy`. But per the section above, you generally won't need it
between sessions; `stop_all_apps.py` + a `check_credits.py` confirmation
that nothing's running is the actual end-of-session routine.

---

## Model endpoints — declared in config, resolved in Studio

This is the **`endpoints/` project**, separate from `base/` so it can never
be touched by a routine base `pulumi up` — see
[Two projects, one cost boundary](#two-projects-one-cost-boundary).

Endpoints are declared as data in `endpoints/Pulumi.dev.yaml` under
`ml-engineering-endpoints:endpoints`, one entry per chapter exercise:

```yaml
ml-engineering-endpoints:endpoints:
  - name: ch2
    hf_model_id: TinyLlama/TinyLlama-1.1B-Chat-v1.0
    instance_type: ml.g5.xlarge
    image_uri: <resolved container URI>
```

`cd endpoints && pulumi up` creates a `Model` + `EndpointConfiguration` +
`Endpoint` for every entry — the `Endpoint` is what actually launches the
GPU instance and starts billing, continuously, until you delete it. To
retire one, delete its entry and `pulumi up` again — Pulumi destroys just
that endpoint, nothing in `base` is affected either way.

### The container image URI

`image_uri` must be the AWS-account-owned ECR image
(`763104351884.dkr.ecr.<region>.amazonaws.com/vllm:...`), sourced from AWS's
own published DLC image list
([aws.github.io/deep-learning-containers/reference/available_images](https://aws.github.io/deep-learning-containers/reference/available_images/)) —
**not** the `public.ecr.aws/deep-learning-containers/...` gallery mirror.
SageMaker's `CreateModel` rejects non-ECR images outright ("Using non-ECR
image ... without Vpc repository access mode is not supported"); the public
gallery URI doesn't count as an ECR image from SageMaker's point of view.
Re-check that page for the current tag before reusing an old one — DLC tags
get deprecated and removed, not just superseded.

It's not resolved dynamically via the `sagemaker` SDK, for the same reason
as before: that SDK's API for this is a moving target (we hit
version-mismatch failures under three different attempts). A hardcoded,
sourced URI sidesteps all of it, at the cost of needing a manual check
against that page periodically.

We're using AWS's own vLLM DLC (env var `SM_VLLM_MODEL`, set in
`infrastructure/model_endpoints.py`) rather than the plain Hugging Face
PyTorch Inference Toolkit container. The plain toolkit's `/invocations`
handler only accepts `{"inputs": "plain string", "parameters": {...}}` —
it breaks the moment an agent framework (e.g. Strands' `SageMakerAIModel`)
sends an OpenAI-style chat payload (`{"messages": [...], "max_tokens": ...}`).
vLLM's `/invocations` speaks that flat OpenAI chat-completions shape
natively, no wrapper needed.

If you change `hf_model_id` to a different architecture that this container
doesn't support well, check the same source page for a newer tag before
assuming the deploy failure is something else.

## Project layout

```
ml-engineering/
├── requirements.txt
├── venv/                        # shared by both projects below
├── infrastructure/              # resource-builder modules, imported by both projects
│   ├── networking.py
│   ├── iam.py
│   ├── storage.py
│   ├── sagemaker_studio.py
│   ├── guardrails.py            # IAM Deny — the instance-type gate
│   ├── budget.py                # AWS Budget — email alerts at 50/80/100%
│   └── model_endpoints.py       # Config-driven SageMaker model endpoints
├── base/                         # cheap, always-on — safe to `pulumi up` anytime
│   ├── Pulumi.yaml
│   ├── Pulumi.dev.yaml           # region, profile, budget email, allowed instances
│   └── __main__.py
├── endpoints/                    # GPU-billed — deliberate, opt-in `pulumi up`
│   ├── Pulumi.yaml
│   ├── Pulumi.dev.yaml           # endpoints: [...]
│   └── __main__.py               # StackReference into base/ for execution_role_arn
└── scripts/
    ├── stop_all_apps.py          # end-of-session cleanup (run this, not pulumi destroy)
    └── check_credits.py
```

## Extending this as you go through the book

- **Chapter 2 (Bedrock Knowledge Bases)**: add a `bedrock_kb.py` module to
  `infrastructure/`, wired into `base/__main__.py`, for the vector store when
  you get there — don't pre-build it now.
- **Chapters 3, 7, 8 (more real-time endpoints)**: add entries to
  `endpoints/Pulumi.dev.yaml`'s `endpoints:` list — same pattern as ch2,
  nothing new to wire up in code.
- If a later chapter's exercise genuinely needs more than `ml.g5.2xlarge`,
  raise `allowed_instance_patterns` in `base/Pulumi.dev.yaml` deliberately,
  for that one exercise, and lower it back afterward — don't leave the gate
  wide open for the rest of the book.
