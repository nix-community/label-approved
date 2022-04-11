# Label approved

adds one of 3 labels to a approved commit

```
"12.approvals: 1"
"12.approvals: 2"
"12.approvals: 3+
```

https://github.com/NixOS/nixpkgs/labels?q=approvals+


## Usage

set `GITHUB_BOT_TOKEN` or `GITHUB_TOKEN` to a github token with permissions to add labels

```bash
nix run
```


## Reasoning

github doesn't have a way to show approvals made by non-committers.

i created this to give non-committers a way to help committers.
