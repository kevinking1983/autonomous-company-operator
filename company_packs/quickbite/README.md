# QuickBite Company Pack

Everything the operator knows about how QuickBite works. It is stored as
**data, not prompts**, so you can change company behaviour without changing
code.

| File | What it holds |
|---|---|
| `company.yaml` | The company and the role the operator plays |
| `systems.yaml` | The internal systems, their URLs, and credential references (passwords come from environment variables) |
| `permissions.yaml` | Every action the operator may take, with its side effect (`read` / `write` / `money` / `irreversible`), plus hard "never" rules |
| `policies/compensation.yaml` | The remedy for each issue type: refund percentages and late-delivery coupon tiers |
| `policies/approvals.yaml` | When a human must approve, as machine-checkable conditions |
| `sops/*.md` | One procedure per issue type. A YAML header (which categories it applies to, the systems and actions it uses, and **checkable success criteria**) followed by human-readable steps |
| `guides/tone.md` | How replies to customers should read |
| `records.yaml` | Record types (ticket, order, refund…), how to recognise their ids, and the read-only pages that show them; used by the independent verifier |
| `facts.yaml` | Durable company facts; learned facts are added here later |

The pack is loaded and cross-checked by `company_operator.company_pack`.
Unknown systems, unknown actions or malformed rules fail fast; see
`tests/company_pack/`.

**Adding a new issue type** takes three things and no runtime code: a new
SOP file, an entry in `compensation.yaml` if money is involved, and approval
rules if needed.
