# AUD-28 — lint dependency remediation

## Candidate and scope

The previous stop remains valid for an upstream upgrade: `braces@3.0.3` is affected
by GHSA-vfj7-8cjw-p6xm / CVE-2026-93687, with no published compatible patch. Both
web and marketing introduced it through `eslint-config-next` →
`@next/eslint-plugin-next` → `fast-glob@3.3.1` → `micromatch@4.0.8`. These were dev
dependencies, but CI/build denial of service is still relevant.

This candidate removes the chain instead of suppressing it. Neither application's
runtime dependencies, Next.js version, application source, or security thresholds
change. No override, fork, advisory exception, or forced Next.js downgrade is used.

## Alternatives evaluated

- Upgrading the Next ESLint integration: latest compatible published integration
  still imports fast-glob and resolves affected braces; insufficient.
- Removing an unused integration: the existing presets supply important React,
  hooks, accessibility, TypeScript and Next rules; simply dropping them is unsafe.
- Supported replacement: Oxlint 1.88.0 supplies 21 of the 22 current Next rules
  natively, without the npm glob chain. Retain ESLint and the same versions of
  React 7.37.5, hooks 7.1.1, import 2.32.0, accessibility 6.10.2,
  typescript-eslint 8.71.1, and globals 16.4.0 directly. This is the chosen option.
- Patched fork: unnecessary; none implemented. A fork would require independent
  security/code review, recursion-bound tests, reproducible provenance, a named
  maintenance owner and ongoing upstream/advisory tracking before adoption.

## Lint coverage and developer workflow

`npm run lint` runs `eslint . && oxlint .` in each project. Existing commands for
unit tests, TypeScript and webpack builds are unchanged. Editor ESLint integration
still covers React/hooks/accessibility/import/TypeScript; enable the maintained
Oxlint editor integration to display Next-specific diagnostics interactively.
The CLI and CI always run both tools.

ESLint preserves the React and hooks recommended configurations, original React
exceptions, six accessibility rules and anonymous-default-export warning. Web
retains TypeScript recommended rules and its two warning overrides. Marketing
retains TypeScript parsing without adding a previously absent recommended preset.
Generated files retain their existing ignores.

Oxlint explicitly enables every original Next rule except
`no-location-assign-relative-destination`, retaining each core-web-vitals severity.
Web retains its existing `no-img-element` exception; marketing retains that warning.
Oxlint defaults are disabled to avoid unrelated new rule categories.

The missing navigation rule is covered by supported ESLint `no-restricted-syntax`
selectors for `location.assign()` and writes to `location.href`, including computed
properties and window/globalThis/document/self access. This deliberately stricter
rule also forbids external/dynamic destinations and similarly named local objects.
Neither project currently uses those operations. Reading location.href (password
recovery) remains allowed. Use Next redirect/router APIs for navigation. This is
configuration of maintained tooling, not a vendored/forked Next plugin.

`npm run test:lint` runs five coverage/security tests in each project, exercising
actual unsafe React, hook, accessibility, import, TypeScript, navigation and script
fixtures, checking available Next rules and absence of the entire vulnerable chain.
Frontend CI also runs marketing lint/coverage/build/TypeScript, previously covered
only by dependency audit.

Sources: [Next ESLint configuration](https://nextjs.org/docs/app/api-reference/config/eslint),
[Oxlint built-in plugins](https://oxc.rs/docs/guide/usage/linter/plugins.html),
[advisory](https://github.com/advisories/GHSA-vfj7-8cjw-p6xm).

## Verification and integration

Both lockfiles resolve without braces, micromatch, fast-glob, eslint-config-next or
@next/eslint-plugin-next. Installation reports zero vulnerabilities. Every existing
production dependency retains its exact locked version. Initial coverage tests
pass five cases in each project. Full regression and actual Actions certification
are pending at candidate creation; results will be recorded after completion.

No merge, deploy, production migration, production database access, paid provider
call or billing change is part of this task. Security remains blocking on npm
high/critical advisories including development dependencies, all Python advisories,
secret scans and workflow validation. Main's eight required contexts and strict
up-to-date requirement remain unchanged.

Safe future order after both PR heads pass all eight checks: integrate CI PR #28;
update bridge PR #27 from the resulting main; verify no runtime diff from certified
4e8514a18a5eb54b81a76d5e53f682c759d239c7; rerun all eight contexts on that exact new
head; then consider the separately authorized controlled bridge-only release.
Current PR checks do not certify a future merge commit or production deployment.
