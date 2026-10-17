# Stable lib32 Mesa and LLVM updates

The four LLVM consumers built by `local/lib32-mesa/PKGBUILD` pin the runtime
provider's `epoch:pkgver`: `lib32-mesa`, `lib32-opencl-mesa`,
`lib32-vulkan-radeon`, and `lib32-vulkan-swrast`. Provider `pkgrel` is excluded.
An upstream LLVM version change, including a patch release or rollback, therefore
requires rebuilding the complete split family with a new Mesa package release.

`scripts/check-lib32-mesa-llvm.py` checks the actual published database and Arch's
multilib provider. The ordinary update detector runs this check before its
same-version and `FORCE_REBUILD` branches. A stale pin produces an actionable
GitHub warning and a blocked-package entry in the final detection summary.
That package is not queued, including when forced; unrelated packages continue
through the normal workflow. It is never reported as up to date.
An explicitly skipped `lib32-mesa` remains skipped.
The detector also checks its release-asset version snapshot against the database;
a partial publication cannot masquerade as an up-to-date package.

The local build entrypoint repeats the check using the builder's synchronized
`/var/lib/pacman/sync/multilib.db`. It does not query installed LLVM before pikaur
has installed the dependencies. A package release already newer than every
published LLVM split is allowed to rebuild through the existing build/publish
workflow. Partial split metadata can be repaired only with a newer version;
failed downloads, malformed databases and ambiguous versions block this package.
An asset/database disagreement requires inspecting and reconciling the partial
publication first; it is not automatically repaired by this guard.

When the check reports a mismatch:

1. Bump `pkgrel` in `local/lib32-mesa/PKGBUILD` above the published release.
2. Run the existing tests and build/publish the complete split family normally.
3. Verify the four `.PKGINFO`/database pins match the current provider, then retry
   dependent builds such as `lib32-mesa-git`.

The guard does not choose or edit `pkgrel`, create commits, or publish packages.
`FORCE_REBUILD` alone does not authorize overwriting an unchanged version with
a different LLVM dependency. Same-provider forced rebuild behavior is unchanged.
This is a dependency freshness guard, not an atomic publication lock: existing
overlapping runs and the interval between asset uploads and database publication
still require the repository's normal release coordination.

Offline regression tests cover metadata failures, all four splits, LLVM upgrades
and rollbacks, explicit release bumps, forced and skipped builds, provider changes
between detection and building, single-package isolation in detection, and hard
build-entrypoint failures. They do not replace a clean Arch build or a pacman
upgrade test.
