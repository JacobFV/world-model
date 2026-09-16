# companies_house_uk

UK Companies House Basic Company Data and People with Significant Control (PSC) snapshot.

**Status**: activated after the shared budget rose to 50 GiB (previously deferred with zero budget).

**Source**:
- `https://download.companieshouse.gov.uk/BasicCompanyDataAsOneFile-2026-09-01.zip` (492,551,925 bytes; the file name changes monthly).
- `https://download.companieshouse.gov.uk/persons-with-significant-control-snapshot-2026-09-15.zip` (2,213,176,181 bytes; the file name changes daily, and older snapshots are removed).

Sizes were verified on 2026-09-15.

**Credential**: none. **Licence**: Open Government Licence v3.0, with attribution: "Contains public sector information licensed under the Open Government Licence v3.0". The PSC register holds personal data published under statutory rules. Normalization keeps only name, nationality, country of residence and control facts; birth dates and addresses stay in raw.

**Evidence**:
- Companies: `gb:companies_house:<number>` business entities with category, status, incorporation and dissolution dates, and post town/postcode. Also company-number identifiers, `classified_as` `uksic2007:<code>`, and previous `legal_name` values valid until their change date.
- PSC entries: one entity per register entry, `gb:psc:<psc id>`, typed person, organization or agent (super-secure, protected label). Each entry has `significant_control_over` to the company, valid from `notified_on` to `ceased_on`. It carries `natures_of_control` and parsed `share_bands`, for example `ownership_of_shares: [0.25, 0.5]`.
- PSC ids are specific to one company, so one person or firm that controls several companies is **not** merged.
- Corporate PSCs keep their published registry number. A UK registration number also yields `registered_as` to `gb:companies_house:<number>`.
- PSC statements and exemptions become `psc_statement` and `psc_exemption` claims on the company.

Implementation: [pipeline.py](pipeline.py); declaration: [dataset.json](dataset.json).

## Rebuild

```sh
wm acquire companies_house_uk --dry-run
wm acquire companies_house_uk --allow-network        # --resume after interruptions
WORLD_MODEL_RAW_VERIFY=size wm run companies_house_uk
wm verify companies_house_uk
```

Tests: `python3 -m unittest tests.test_companies_markets_research_datasets`.
`artifacts/` and `scratch/` are ignored by Git. Cross-dataset identity notes: [docs/data/companies-markets-research.md](../../docs/data/companies-markets-research.md).
