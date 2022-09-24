Docker images
=============

Docker images for this exporter are available from `Docker Hub <https://hub.docker.com/r/pdreker/fritz_exporter>`. The images are built for amd64, arm6, arm7 and arm8/64, so they should run on almost all relevant platforms (Intel, Raspberry Pi (basically any version) and Apple Silicon).

Tags
----

+----------+----------------------------------------------------------------------------------+
| Tag      | Description                                                                      |
+==========+==================================================================================+
| develop  | Latest build from develop branch. **This may be unstable and change at any       |
|          | time without notice or regards for compatibility!**                              |
+----------+----------------------------------------------------------------------------------+
| latest   | Latest released version. This might automatically upgrade through major          |
|          | releases, which may be incompatible with currently running versions.             |
|          | Can be used, if you don't mind the occasional breakage. Major releases are rare. |
+----------+----------------------------------------------------------------------------------+
| Version  | Specific released versions. Will not update your images without you explicity    |
| (e.g.    | changing the image tag in Docker. Currently recommended.                         |
| v2.1.1)  |                                                                                  |
+----------+----------------------------------------------------------------------------------+
