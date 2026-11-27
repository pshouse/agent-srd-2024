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

## Pre-enable checklist

Pages stays off until every line below is done.

### Patrick (repository admin)

- [ ] Create the `github-pages` environment. Patrick is a required reviewer. The deployment-branch rule is `main` only.
- [ ] Turn on secret scanning push protection for this repository.
- [ ] Confirm branch protection on `main`.

### CTO

- [ ] Kill-switch drill: enable Pages, dispatch from `main`, confirm `/`, `/web/`, and `/dist/manifest.json` return 200, run the kill switch, confirm those three URLs return 404.
- [ ] Sign off, together with Patrick, before the site is left published.

## Who can dispatch

`workflow_dispatch` is available to people with write access to `pshouse/agent-srd-2024`. That is not permission to publish. The CTO and Patrick both have to sign off before anyone runs `pages.yml` or turns Pages on in the repository settings.
