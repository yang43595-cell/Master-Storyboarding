# Windows transfer through an existing local Linux runtime

## Scope

This procedure records a successfully exercised recovery route when the official file-receiving helper cannot preserve extended attributes on Windows. It is a transport and provenance procedure, not a new production stage or a replacement for the official file service.

It uses an already installed, trusted Docker runtime and an already present Python-capable image pinned by its actual digest. The image used on one machine is not a required product dependency. Do not install another product or pull an image merely to follow this note.

Starting Docker or a container requires the user's authorization for that task. This successful run does not grant standing permission for later starts, uploads, cleanup or security changes. If the required runtime is unavailable or authorization is absent, report that boundary.

## Failure and supported recovery

The Windows receiving helper failed while calling `os.setxattr`. A file appearing on Windows is therefore not sufficient proof that the official receipt, including source/version metadata, completed.

Use the original official helper and its complete official preparation/transfer response in a Linux task directory that demonstrably supports extended attributes. Do not remove metadata checks, patch out the failing operation, reconstruct signed download URLs, extract credentials or substitute a hand-built transfer object.

### 1. Establish the bounded runtime

After authorization to start the existing runtime:

- Select an existing trusted Python-capable image by its verified immutable digest.
- Use `--pull never` and override the entrypoint to Python for this task.
- Use `--cap-drop ALL` and `--security-opt no-new-privileges`.
- Do not attach business-data volumes, the Docker socket, host ports or privileged mode.
- Do not launch the image's usual application services.
- Use a dedicated Linux task directory. Test an extended-attribute write and read there before transferring the package; filesystem support must be observed rather than assumed from the image.

Keep runtime names, digests, task directories and concrete commands in the local task record. Do not copy machine-specific paths or identifiers into general project documentation.

### 2. Receive and verify the original package

Use the official file-preparation flow and pass its complete transfer object unchanged to the original receiving helper inside the Linux runtime. The receiving environment owns this materialization.

Verify all of the following before calling the receipt complete:

- The original package is readable in the Linux task directory.
- Its byte count and cryptographic hash match the source evidence.
- The helper's full source-file ID and version metadata can be read back.
- No metadata-required failure is being disguised as a successful receipt.

Retain the original package in Linux together with its verified receipt metadata. A Windows copy that lost that metadata is not a fully received original and must not be labeled as one.

### 3. Produce a bounded Windows derivative

Extract only the required source-media entry from the verified package. Check the archive entry before extraction; reject traversal paths and do not unpack unrelated project contents.

Record derivative provenance locally:

- Original package source-file ID and version.
- Original package byte count and SHA-256.
- Exact archive entry selected.
- Derived media byte count and SHA-256.
- The extraction and transport relationship, explicitly identifying the media as a derivative.

Use `docker cp` to copy that derived media file to the intended Windows task directory. Independently calculate its size and hash in Linux and Windows and require exact equality. The Windows file is usable task input with recorded provenance; this does not promote it to a metadata-complete copy of the original package.

Do not place actual file IDs, personal paths, media identifiers, signed URLs or original media in the source repository.

### 4. Run local transcription

The exercised route used an already present large-v3 model on the Windows GPU after transfer verification. Reuse an available, verified local model; this procedure does not authorize another model download or software installation.

Where the installed GPU runtime requires additional DLL directories, configure them only for the transcription process. Do not modify system PATH or unrelated application environments. Record actual model/runtime versions and preserve the raw machine output. Machine transcription remains unreviewed evidence, not human listening verification or approved dialogue.

### 5. Return results without duplicate uploads

The small result package was successfully created in the official file service during the exercised run. Upload completion and local metadata writeback are separate outcomes:

- Verify the remote file's returned identity and version through the official creation result or readback.
- If remote creation succeeded but Windows extended-attribute writeback failed, report that local failure separately.
- Do not repeat the upload solely because local writeback failed.
- If remote creation is uncertain, inspect the official result/readback before deciding whether a retry is necessary.

Send only the verified result identity through the supported official delivery route. Do not substitute private download URLs.

## Retention and cleanup

The Linux container and original package are intentionally retained until the user authorizes cleanup. Do not automatically delete the container, its original package or provenance records after obtaining the Windows derivative or returning a result.

A later cleanup request must identify its scope. Preserve any original or provenance record still required by the project and verify the intended deletion behavior before proceeding.

## Verification boundary

This route was exercised successfully: Linux extended attributes, official package receipt and metadata readback, single-entry extraction, independent cross-platform size/hash agreement, existing local GPU transcription, and official result-package creation.

It does not imply that arbitrary images, filesystems, model/runtime combinations or future transfers will work. Repeat the relevant checks for the selected environment and current task authorization. No new software installation or global security-setting change is required by this procedure.
