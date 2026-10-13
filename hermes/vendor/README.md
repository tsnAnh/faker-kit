# Vendored Python packages

The team-kit commands put this directory last on `sys.path`, so they use an installed copy of a package
first and fall back to the one here. With it, the commands need only a Python 3.9+ and its standard
library: no pip, no virtual environment, and no network, so upgrading the machine's Python cannot leave
them unable to import a dependency. Files here are copied unchanged from the release named below and are
never edited by hand; ruff and mypy skip this directory.

| Package | Version | Source | sha256 of the source | Files |
|---|---|---|---|---|
| PyYAML (MIT, `yaml/LICENSE`) | 6.0.3 | https://files.pythonhosted.org/packages/05/8e/961c0007c59b8dd7729d542c61a4d537767a59645b82a0b521206e1e25c2/pyyaml-6.0.3.tar.gz | d76623373421df22fb4cf8817020cbb7ef15c725b9d5e45f17e189bfc384190f | `lib/yaml/` of the source release, as `yaml/`, plus its `LICENSE`. The pure-Python package: without the libyaml C extension, `yaml.CSafeLoader` is absent and `yaml.safe_load` works. |

To update: download the new source release from PyPI, check its sha256 against PyPI's, replace `yaml/` with
its `lib/yaml/` and `LICENSE`, and update this table.
