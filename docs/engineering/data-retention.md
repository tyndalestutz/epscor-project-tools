# Scientific data retention and local records

This policy guides new records and a future, separately authorized migration.
Existing tracked evidence stays intact. See the [inventory](data-inventory.md)
and [experiment index](../../experiments/README.md). Generated, old, failed and
physically discrepant records are not disposable by default.

## What belongs where

Git primarily holds source, tests, reusable recipes/configuration examples,
instrument procedures, models, validation methods, documentation, measurement
indexes/provenance references and small deliberately curated regression fixtures.
Large raw acquisitions, routine run directories, long logs, large derived reports
and historical archives normally belong in separately backed-up scientific
storage. Keep each measurement with its metadata and processing dependencies.
Do not split a bundle solely by file extension or keep a PDF instead of raw data.

No archival service, replication, storage platform or migration is configured
here. The owner must select a storage location, custodian, access policy, capacity,
retention period and recovery procedure before any removal from Git is proposed.
No existing records may be untracked or moved under this policy alone.

## Traceability and verification

For each new run, retain the producer's original schema and record:

- Unique run ID, acquisition timestamps/time zones and completion/failure/cleanup
  status; identify retries and parent/related runs without overwriting them.
- Exact Git revision, dirty-state evidence and a retained patch/source snapshot
  when needed; a revision/hash alone cannot reproduce uncommitted source.
- Resolved recipe, units, wavelength, channel/routing assignments, gains,
  calibration references, software/driver versions and relevant hardware setup.
- Raw readings/buffers, device and host timestamps/events, clock assumptions,
  commanded versus measured quantities, interruptions and validity limitations.
- Processing commands/configuration/version, source record references and explicit
  transformations/exclusions for each derived artifact.

For external bundles, maintain a small reviewed index in Git with run ID, owner,
non-sensitive archive locator, access/retrieval instructions, relative file names,
byte sizes and SHA-256 checksums. Record manifest version and creation method.
Verify checksums after copying and when retrieving; preserve original manifests
and append corrections rather than silently rewriting history. Never publish
credentials or signed access URLs. A locator must identify an actual retained
copy; do not fabricate one for records still only on a workstation.

## Backup and recovery

Checksums detect changes; they are not backups. Ignoring a directory is not a
backup either. The designated custodian should maintain independently recoverable
copies across separate failure domains, including an off-machine copy, and define
backup frequency, retention/versioning, access and incident recovery. Verify
scheduled backups and test restoration of complete bundles with checksum checks.
Until these arrangements and a restored copy are verified, do not remove the
source copy. Record who verified recovery and when in the archive handoff.

Retain failed/incomplete acquisitions with partial buffers, event/log/status and
cleanup evidence. Never fabricate completion metadata or discard disagreement
with a model. New interpretation belongs in separate derived outputs.

## Curated fixtures and offline work

A fixture PR must identify source/run IDs, permission to publish, selection method,
raw/derived status, units, provenance, limitations, expected behavior and justified
tolerances. Prefer small deterministic synthetic records where scientifically
appropriate; label them synthetic. Never silently trim or normalize measurements.
Retain source-to-subset mapping and processing commands for measured subsets.
Existing archived inputs required by tests remain in Git until a reviewed
replacement preserves coverage. Fixture qualification is later campaign work.

Before going offline, retrieve needed external bundles plus manifests, source
revision, recipes and processing dependencies; verify them locally. Keep approved
fixtures accessible from a fresh checkout for the standard regression command.
Document restricted/offline retrieval alternatives and missing-data limitations;
never substitute model output for unavailable measurements.

## Disclosure review

Before staging records, inspect recipes, logs, metadata, notebook outputs and
embedded report contents for credentials, tokens, private keys, account details,
private network topology, personal paths and unnecessary instrument identifiers.
Preserve necessary calibration/channel mapping and scientific provenance. Keep
sensitive originals in access-controlled backed-up storage; publish only an
explicitly documented reviewed derivative when appropriate, with traceability
that does not itself expose sensitive values. Review third-party redistribution
rights. Do not silently redact or rewrite existing scientific originals.

## Optional local-only workspace

`experiments/_local/` is an opt-in ignored location for future local records.
The root rule is anchored to that directory only. It creates no directory, moves
no records and changes no application defaults or existing subsystem rules.
Keep reusable code, recipes and documentation outside it so they can be reviewed.

An operator may explicitly choose a unique subdirectory there using an existing
supported output setting (for example a recipe's `results_directory`) after
checking the subsystem instructions. Use an absolute path to avoid working-directory
ambiguity. Some menus, including the high-speed PAX recipe loader, restore their
own output default; verify the resolved destination before acquisition. This
convention does not promise every interface accepts an output override.

Back up local records as complete bundles under the recovery policy above before
relying on them. `git status` will not show their contents. Check explicitly with
`git check-ignore -v experiments/_local/example/record.csv`; review intended
files by path before staging anything. Never force-add bulk or sensitive records
as a substitute for an archive decision. Existing experiment indexes remain the
entry point for historical tracked records.
