# Declared purpose and identified persons

Most source restrictions govern how a published artifact may be *used*, and the catalog treats
those as metadata to carry rather than gates to enforce — see `worldmodel/rights.py` and
`wm rights <ref>`, whose policy is deliberately
`metadata_only_no_local_execution_gate`.

One restriction is different, because it decides what a pipeline *writes*: whether identified
natural persons are retained. That cannot be deferred to the reader, because once a build has
aggregated contributors away the names are gone.

## The two declarations

A decision needs both halves, and neither alone is enough.

**The dataset declares its rule**, under `source.person_level_records` in `dataset.json`:

| `policy` | Meaning |
| --- | --- |
| `prohibited` | Never retain identified persons. **The default for any source that declares nothing.** |
| `conditional` | Retain only under a declared non-commercial purpose. Requires `condition: non_commercial_use`. |
| `permitted` | The source places no condition on identified persons. |

```json
"person_level_records": {
  "policy": "conditional",
  "condition": "non_commercial_use",
  "authority": "52 U.S.C. 30111(a)(4)",
  "note": "Contributor names and addresses may not be sold, used to solicit contributions, or used for any commercial purpose. Research use is not restricted, so contributor-level rows are retained only when the deployment declares WM_COMMERCIAL_USE=0."
}
```

**The operator declares the deployment's purpose**, once, in `WM_COMMERCIAL_USE`:

```sh
WM_COMMERCIAL_USE=0    # non-commercial research: conditional sources unlock
WM_COMMERCIAL_USE=1    # commercial: conditional sources stay aggregate-only
```

**Unset means commercial.** A restriction that binds only commercial users has to bind until
someone actually declares otherwise, so a fresh clone gets the conservative path and the
permissive one is always an explicit act. An unparseable value raises rather than defaulting.

## Why not one global switch

Sources restrict for different reasons, and a single flag would conflate them. FEC bars
commercial use of contributor information but says nothing about research. Alpaca restricts
*redistribution* regardless of purpose. ACS PUMS is confidentiality-protected at the source, so
there are no identified persons to retain at any setting. Only a per-source rule can tell those
apart, which is why the flag alone never unlocks anything — it only satisfies a condition a
dataset already declared.

## Reading the decision

```sh
python3 -m worldmodel use-policy                                    # every dataset with a rule
python3 -m worldmodel use-policy --dataset fec_individual_contributions
```

Every record a pipeline emits under this rule carries the decision that produced it, in
`attributes.rights_decision`, including the authority and the declared purpose. A filtered and
an unfiltered build of the same source are therefore distinguishable from their provenance
rather than by inspecting rows — and because the store is content-addressed, they are different
artifacts with different hashes.

## What this is not

It is **not** legal advice, and it does not adjudicate the restriction. It records which rule
was declared, which purpose was declared, and what followed. `52 U.S.C. 30111(a)(4)` also bars
*sale* and *solicitation* at every setting; the flag does not represent those, because no
pipeline does them. Interpreting a source's terms remains the operator's responsibility.

Changing the flag does not rewrite existing artifacts. A dataset built under one purpose keeps
the rows it was built with until it is rebuilt.
