# Repository index safety and recovery

Ordinary updates must download **both** existing indexes successfully and validate
them before running `repo-add`. HTTP failures (including 404), corrupt/empty
archives and mismatched database/file lists now stop publication. They are never
interpreted as permission to create a new repository. All work occurs in staging;
only a successful paired update replaces the publishable files.

Package downloads for snapshots use release-provided size and SHA256 metadata,
bounded retries and temporary files. HTTP error responses cannot become packages.
The package asset manifest must remain unchanged through the download.

## Explicit index-only recovery

Use the `Repair Repository Index` workflow on master and select an existing
complete dated snapshot. It restores metadata from that snapshot and downloads
only packages whose assets have changed, using `repo-add` to update both indexes.
Every final entry must match a current release asset's filename, size and SHA256;
package and file indexes must agree and include all current package assets.
Removed/renamed packages or duplicate asset versions which cannot be reconciled
safely cause a failure rather than silently removing history.

The workflow can also be deliberately triggered by a master push containing
`[repair-index]` in the commit message. Such commits skip ordinary package builds.
This is an explicit publishing operation, not a normal infrastructure commit.
All publishing/cleanup workflows share a non-cancelling concurrency group. Repair
also acquires the existing database lock, checks the branch head before publishing,
and refuses publication if any release asset changed since preparation.

Before replacing indexes, CI retains the original four indexes, release manifests,
proposed indexes and recovery report as an artifact for 30 days. Only the four
`skorion.db*`/`skorion.files*` assets are uploaded. Packages, historical releases,
release descriptions and tags are not modified. Published indexes are downloaded
again and checked byte-for-byte plus against the package asset manifest.

GitHub cannot replace four release assets atomically. An interrupted upload can
leave a mixed pair; inspect the artifact and current manifest before retrying.
The workflow reports this as failure, not successful recovery. Never bypass the
asset/head/lock checks or restore an old snapshot over newer packages blindly.

Tests: `bash tests/test-bootc-dependencies.sh` and
`python3 -m unittest discover -s tests -v`. The recovery CI additionally runs the
real Arch `repo-add`; offline fixtures do not replace post-publication verification.
