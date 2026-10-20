# Release installer native dependencies

The host needs Python 3.12 with `venv`. The installer can download PostgreSQL,
NATS Server and, for the selected `test`, `tau` or `provider` dependency profile,
Node.js with npm. Test and TAU tooling require Node.js 20 or newer; the pinned
automatic installation currently supplies Node.js 22.

In the installation target JSON, omit **both** `pg_bin` and `pg_share` to prepare
PostgreSQL, and omit `nats_server` to prepare NATS. `python` may be omitted to use
the Python interpreter running the installer. Omit `node_bin` to prepare Node
when the selected profiles require it. Existing explicit tool paths remain
supported; `node_bin` is the directory containing Node and npm, not an executable.
All other installation identity, data-root and port fields remain required.

`plan` resolves the paths and lists downloads without creating files or contacting
download servers. `install` downloads into the installation's `runtime/native`
directory under its existing installation lock. It checks the downloaded archive's
pinned SHA256, safely extracts it and runs version probes before registering any
services. This archive check does not reintroduce an engineering-tree integrity
scan when `--skip-file-verification` is used. A failed download can be retried with
the same target and engineering bundle. Unowned dependency directories are never
adopted or replaced. Existing installation tool paths are not silently upgraded;
changing a PostgreSQL major version requires a database migration.

The installer does not invoke a system package manager, request elevation, change
global PATH or launch a second system-wide database service. Its existing service
installation phase starts the private PostgreSQL and NATS instances. Node's path
is supplied only to the installation's processes. Python/npm application packages
and TAU browser assets are prepared by the selected dependency profiles; network
access to their package repositories is required too.

Sources and versions are pinned in `config/release-native-dependencies.json`:

| Tool | Version | Binary supplier |
| --- | --- | --- |
| PostgreSQL | 16.15 | theseus-rs/postgresql-binaries, build 16.15.1 (third-party build) |
| NATS Server | 2.15.1 | nats-io/nats-server official releases |
| Node.js, including npm | 22.23.3 | nodejs.org official releases |

PostgreSQL binaries use the PostgreSQL license; NATS uses Apache-2.0; Node.js
and npm include their own license and third-party notices. The downloaded
distribution's files, including available notices and build metadata, are retained.
The PostgreSQL binary supplier is a separate third-party distributor, not the
PostgreSQL project's own binary distribution. Each private tool directory contains
`tfi-native-dependency.json` recording the URL, digest, supplier, pinned version
and actual version-probe results. Installation state records these receipts too.

The catalog covers Linux/glibc, macOS and Windows, on x86_64 and ARM64. Unsupported
platforms are rejected before downloads; non-glibc Linux must supply compatible
tools explicitly. Linux x86_64 binaries and PostgreSQL initialization have been
checked locally. Windows, macOS and ARM64 native execution remain unverified;
catalog availability and controlled tests are not installation success on those
platforms. Existing Windows service-account and host-service requirements still
apply.
