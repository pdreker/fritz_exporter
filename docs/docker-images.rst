Docker images
=============

Docker images for this exporter are available from `Docker Hub <https://hub.docker.com/r/pdreker/fritz_exporter>`_. The images are built for amd64, arm6, arm7 and arm8/64, so they should run on almost all relevant platforms (Intel, Raspberry Pi (basically any version) and Apple Silicon).

Tags
----

+----------------+----------------------------------------------------------------------------------+
| Tag            | Description                                                                      |
+================+==================================================================================+
| develop        | Latest build from develop branch. **This may be unstable and change at any       |
|                | time without notice or regards for compatibility!**                              |
+----------------+----------------------------------------------------------------------------------+
| latest         | Latest released version. This might automatically upgrade through major          |
|                | releases, which may be incompatible with currently running versions.             |
|                | Can be used, if you don't mind the occasional breakage. Major releases are rare. |
+----------------+----------------------------------------------------------------------------------+
| full version   | Specific released versions. Will not update your images without you explicity    |
| (e.g.          | changing the image tag in Docker.                                                |
| 2.1.1)         |                                                                                  |
+----------------+----------------------------------------------------------------------------------+
| major          | Specific major version. E.g. "2" will install any 2.x.y release thus             |
| (e.g. "2")     | avoiding unexpected major upgrades which may be incompatible or contain          |
|                | breaking changes. **Recommended**                                                |
+----------------+----------------------------------------------------------------------------------+
| major.minor    | Specific major/minor version. E.g. "2.1" will install the latest 2.1.x release   |
| (e.g. 2.1)     | thus avoiding unexpected major upgrades which may be incompatible or contain     |
|                | breaking changes. This will effectively only update patch releases.              |
+----------------+----------------------------------------------------------------------------------+
