# Parallax for DeepSeek Harness

Parallax is a lightweight enhancement layer on top of your coding-agent harness. You own the goal; the agents own the work, context management, and independent acceptance.

This package is the DeepSeek Harness bundle: it registers the Parallax skills (qstart, qgo, bcheck, gclear, rcheck, xwrap, wish) and the native same-session hand-off tool/hook.

## Install

In the DSH web UI: **Plugins → Add plugin → enter `parallax-dsh`** (the plugin list can take ~30 s to load; choose the npm registry), install it, toggle the plugin on, then restart the profile.

Or from the CLI:

    dsh plugin --profile <profile> add parallax-dsh

Requires the Node runtime supplied by dsh and `pnpm` on the dsh process PATH. Restart the profile after installing.

## Links

- Source and documentation: https://github.com/AI4XE/Parallax
- Issues: https://github.com/AI4XE/Parallax/issues
- License: MIT

**GitHub (source, docs, issues): https://github.com/AI4XE/Parallax**
