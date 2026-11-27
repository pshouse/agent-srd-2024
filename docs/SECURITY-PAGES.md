# GitHub Pages security

Pages is **not enabled**. The workflow in `.github/workflows/pages.yml` is `workflow_dispatch` only. Do not run it, and do not turn Pages on, until the CTO and Patrick both sign off.

## Threat model

The site is static. There is no application server, no account system, and no browser call to a third party.

- **Untrusted data is our own generated bundles.** `dist/` is produced by this repository's compiler. The browser still treats card Markdown and JSON fields as data that must not become HTML. The page renders Markdown with `html: false` and puts search hits in `textContent`. A bad card must not run a script.
- **Supply chain via Actions.** The workflows pin every action to a full commit SHA. They add no repository secrets. The build job can only read the repo. The deploy job can write Pages and request an OIDC token. CI can only read the repo.
- **Accidental publication.** The upload is an allowlist (`scripts/site_files.py`), not a copy of the repository. The SQLite database, `src/`, tests, `.git`, design mocks, and `mock-fonts.css` are not on that list. The build job refuses the upload if `_site` contains anything else, a file over 5 MB, a `.env` / key / pem-like file, or a manifest whose `dataset_id` or counts do not match the published index and graph.
- **Trademark and attribution.** The published footer keeps the official SRD 5.2 attribution and the "Independent project, not an official product" line. The site does not add a Wizards of the Coast disclaimer beyond that attribution.

## Permissions

The workflow token starts at `permissions: {}`. A job that does not set its own permissions inherits that empty set.

| Job | Permissions | Environment |
| --- | --- | --- |
| `main-only` | none (inherits `{}`) | none |
| `build` | `contents: read` | none |
| `deploy` | `pages: write`, `id-token: write` | `github-pages` |

`build` checks out the repo, runs the tests, assembles `_site`, runs `scripts/check_site.sh`, and uploads one artifact named `github-pages`. It does not get `pages` or `id-token`.

`deploy` is the only job with the `github-pages` environment, so the required reviewer gates that job and not the build. It has `needs: build`. `actions/configure-pages` runs there because it calls the Pages API. `actions/deploy-pages` runs there because publishing needs `pages: write` and the OIDC token from `id-token: write`.

`actions: read` is not granted. `actions/deploy-pages` v4.0.1 stopped listing artifacts with `GET /repos/{owner}/{repo}/actions/runs/{run_id}/artifacts`, which is the call that needed `actions: read`. v5.0.1 calls `@actions/artifact` `listArtifacts()` with no `findBy` option, so the client uses the Actions results service (`POST {ACTIONS_RESULTS_URL}/twirp/github.actions.results.api.v1.ArtifactService/ListArtifacts`) and the runtime token. The upload in run 45932195143 finalized artifact `github-pages` (id 15169096132, 1,152,392 bytes) at 03:08:28.722Z. At 03:08:29.160Z the deploy step, still in that job, logged `Found 0 artifact(s)` and threw `No artifacts named "github-pages"` from `getArtifactMetadata`. It did not log `Listing artifact metadata failed`. The list call succeeded and returned an empty set. That is the same-job race in [actions/deploy-pages#451](https://github.com/actions/deploy-pages/issues/451), with the same pins (`upload-pages-artifact` v5.0.0, `deploy-pages` v5.0.1). A missing `actions: read` grant would not turn a successful empty list into a hit. The upload name and `artifact_name` are both `github-pages`, which is also the default for both pinned actions.

## Kill switch

Either of these takes the site offline. Do the API call or the Settings steps, then check that the site 404s.

### API

Someone with permission to administer the repository runs:

```bash
gh api -X DELETE repos/pshouse/agent-srd-2024/pages
```

A successful call disables GitHub Pages for this repository. It does not delete the workflow file.

### Settings

1. Open `https://github.com/pshouse/agent-srd-2024/settings/pages`.
2. The person who administers the repo chooses **Unpublish site** (or sets the source to none, if that is what the page shows).
3. Confirm.

### Check that it is on, then that it is off

The site root is `index.html`, which redirects to `web/`. These three URLs are the check. While the site is published, each final status is **200**. After the kill switch, each is **404**.

```bash
base=https://pshouse.github.io/agent-srd-2024
for path in / /web/ /dist/manifest.json; do
  code=$(curl -sL -o /dev/null -w '%{http_code}' "$base$path")
  echo "$code $path"
done
```

`curl -L` follows a directory redirect such as `/web` to `/web/`. A live site prints:

```text
200 /
200 /web/
200 /dist/manifest.json
```

After the kill switch, the same three lines are `404`.

## Rollback

`gh workflow run --ref` accepts a branch or a tag. It does not accept a commit SHA. The Pages job fails immediately unless the ref is `main` or a release tag, and it does not deploy on push.

To put a known tree back on the site:

1. If the bad site should disappear immediately, use the kill switch and confirm the three URLs above are 404.
2. Open a revert pull request onto `main`, review it, and merge it. Then dispatch from `main`:

```bash
gh workflow run pages.yml --ref main
```

3. Or dispatch from a release tag of the good tree:

```bash
gh workflow run pages.yml --ref v1.2.3
```

4. Open `/`, `/web/`, and `/dist/manifest.json` and confirm each is 200, and that the footer still says `srd-2024-5.2`.

Do not dispatch until Pages is supposed to be on. Dispatching does not by itself enable Pages.

## Drill notes

The reviewer approves the `deploy` job. `build` does not use the `github-pages` environment, so the artifact can already be uploaded when the review request appears.

Kill-switch drill, once Pages is allowed to be on:

1. Dispatch from `main` (`gh workflow run pages.yml --ref main`).
2. Approve the `github-pages` environment on the `deploy` job.
3. Confirm `/`, `/web/`, and `/dist/manifest.json` return 200.
4. Run the kill switch above.
5. Confirm those three URLs return 404.

If `Deploy Pages` fails after `build` has uploaded `github-pages`, re-run the `deploy` job only. Re-running the whole workflow runs `build` again and uploads a second artifact of that name into the same run. `actions/deploy-pages` then fails with `Multiple artifacts named "github-pages" were unexpectedly found`, and that run cannot be deployed.

## Pre-enable checklist

Pages stays off until every line below is done.

### Patrick (repository admin)

- [ ] Create the `github-pages` environment. Patrick is a required reviewer. The deployment-branch rule is `main` only.
- [ ] Turn on secret scanning push protection for this repository.
- [ ] Confirm branch protection on `main`.

### CTO

- [ ] Kill-switch drill, as written in Drill notes: enable Pages, dispatch from `main`, approve `deploy`, confirm `/`, `/web/`, and `/dist/manifest.json` return 200, run the kill switch, confirm those three URLs return 404.
- [ ] Sign off, together with Patrick, before the site is left published.

## Who can dispatch

`workflow_dispatch` is available to people with write access to `pshouse/agent-srd-2024`. That is not permission to publish. The CTO and Patrick both have to sign off before anyone runs `pages.yml` or turns Pages on in the repository settings.
