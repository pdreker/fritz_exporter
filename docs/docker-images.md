# Docker images

Docker images for this exporter are available from [Docker Hub](https://hub.docker.com/r/pdreker/fritz_exporter). The images are built for amd64, arm6, arm7 and arm8/64, so they should run on almost all relevant platforms (Intel, Raspberry Pi (basically any version) and Apple Silicon).

## Tags

| Tag | Description |
|----|----|
| develop | Latest build from develop branch. **This may be unstable and change at any time without notice or regards for compatibility!** |
| latest | Latest released version. This might automatically upgrade through major releases, which may be incompatible with currently running versions. Can be used, if you don't mind the occasional breakage. Major releases are rare. |
| full version (e.g. 2.1.1) | Specific released versions. Will not update your images without you explicity changing the image tag in Docker. |
| major (e.g. "2") | Specific major version. E.g. "2" will install any 2.x.y release thus avoiding unexpected major upgrades which may be incompatible or contain breaking changes. **Recommended** |
| major.minor (e.g. 2.1) | Specific major/minor version. E.g. "2.1" will install the latest 2.1.x release thus avoiding unexpected major upgrades which may be incompatible or contain breaking changes. This will effectively only update patch releases. |
