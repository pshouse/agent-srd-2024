# Vendored Markdown renderer

One library, pinned. Nothing in this directory is loaded from a CDN at runtime.

| | |
|---|---|
| Package | [markdown-it](https://github.com/markdown-it/markdown-it) `15.0.3` |
| File | `markdown-it/markdown-it.esm.min.mjs` |
| Upstream path | `dist/browser/markdown-it.esm.min.mjs` in the published package |
| Release | https://github.com/markdown-it/markdown-it/releases/tag/15.0.3 |
| Package tarball | https://registry.npmjs.org/markdown-it/-/markdown-it-15.0.3.tgz |
| SHA-256 | `d5432491790141da046b1197353705db03264f40b5727f3b4b591f5aba193e30` |
| License | MIT (`markdown-it/LICENSE`) |
| Copyright | 2014 Vitaly Puzrin, Alex Kocharin |

The file is the official self-contained browser ESM build from the npm tarball (`package/dist/browser/markdown-it.esm.min.mjs`). That path is not in the git tag, so the release page is the upstream link and the tarball is the byte source. The SHA-256 is of this file. The app constructs it with `html: false` and enables the built-in `table` rule so pipe tables render. `linkify` and `typographer` stay off.

markdown-it is the markdown-it project on GitHub. It is not a PRC-origin package.

The trailing `sourceMappingURL` comment points at a map that is not vendored. Browsers request source maps only from developer tools; the page does not load it.
