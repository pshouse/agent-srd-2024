# GitHub Pages security

Pages is **not enabled**. The workflow in `.github/workflows/pages.yml` is `workflow_dispatch` only. Do not run it, and do not turn Pages on, until the CTO and Patrick both sign off.

## Threat model

The site is static. There is no application server, no account system, and no browser call to a third party.

- **Untrusted data is our own generated bundles.** `dist/` is produced by this repository's compiler. The browser still treats card Markdown and JSON fields as data that must not become HTML. The page renders Markdown with `html: false` and puts search hits in `textContent`. A bad card must not run a script.
- **Supply chain via Actions.** The workflows pin every action to a full commit SHA. They add no repository secrets. The Pages job's token can write Pages and nothing else; CI can only read the repo.
- **Accidental publication.** The upload is an allowlist (`scripts/site_files.py`), not a copy of the repository. The SQLite database, `src/`, tests, `.git`, design mocks, and `mock-fonts.css` are not on that list. The job refuses the upload if `_site` contains anything else, a file over 5 MB, a `.env` / key / pem-like file, or a manifest whose `dataset_id` or counts do not match the published index and graph.
- **Trademark and attribution.** The published footer keeps the official SRD 5.2 attribution and the "Independent project, not an official product" line. The site does not add a Wizards of the Coast disclaimer beyond that attribution.

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

### Check that it is off

```bash
curl -sI https://pshouse.github.io/agent-srd-2024/ | head -n 1
```

The expected status is **404**. A 200 means the site is still published.

## Rollback

Pages has no push deploy, so a bad run is not replaced by the next commit on `main`. To return to a known tree:

1. If the bad site should disappear immediately, use the kill switch above and confirm the 404.
2. After sign-off to publish again, run the `pages` workflow on the last good commit (Actions → pages → Run workflow, and choose that ref), or:

```bash
gh workflow run pages.yml --ref <good-sha>
```

3. Open the site and confirm the footer dataset line still says `srd-2024-5.2` and the page you rolled back to.

Do not run that command until Pages is supposed to be on. Dispatching does not by itself enable Pages; GitHub will refuse the deploy while Pages is disabled.

## Who can dispatch

`workflow_dispatch` is available to people with write access to `pshouse/agent-srd-2024`. That is not permission to publish. The CTO and Patrick both have to sign off before anyone runs `pages.yml` or turns Pages on in the repository settings.
