# Flatpak of the Rust app

`io.github._0wn3r.AlphaLinkCAM.json` builds the Rust app offline from `cargo-sources.json`, which lists every crate
in `rust/Cargo.lock`. Regenerate it whenever Cargo.lock changes, with flatpak-builder-tools' generator:

    python3 flatpak-cargo-generator.py rust/Cargo.lock -o rust/build-aux/cargo-sources.json

(https://github.com/flatpak/flatpak-builder-tools/tree/master/cargo; it needs aiohttp, PyYAML and tomlkit.)
