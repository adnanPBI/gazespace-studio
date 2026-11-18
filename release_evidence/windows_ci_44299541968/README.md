# GazeSpace 0.2.1 — verified Windows software evidence

Tested code commit: `52429351784c4d2a317261cca66cad92fc5d6923`.
Source tree: `daa481f9b040332c714818dbd910c7f0bfb7dd4b`.
Successful [run 44299541968](https://github.com/adnanPBI/gazespace-studio/actions/runs/44299541968), [Windows build job 134786705094](https://github.com/adnanPBI/gazespace-studio/actions/runs/44299541968/job/134786705094).

The full SDK-enabled Windows gate passed **225 tests with no failures, errors or skips in 30.963 seconds**. Four Linux/Windows Python 3.11/3.12 matrix jobs each passed 223 tests with two optional SDK skips. The separate full Windows gate exercised those SDK paths.

Frozen CLI and Studio checks passed. Studio painted seven pages and verified 95 printable ASCII characters, 14 UI Unicode characters and 12 distinct nonblank rasters. `visual_review.json` records direct independent inspection of the actual Live workspace screenshot and font proof: readable text and no missing-glyph boxes. It does not claim separate visual inspection of every page or physical monitor acceptance.

Source and frozen synthetic pipelines each processed 60 generated frames with 60 valid screen/spatial outputs and report exports. MediaPipe inferred 478 landmarks and two irises in a reference photograph. Basler emulator checks covered three formats/five frames each; the frozen capture received 60 simulated frames. RealSense enumerated zero devices. All three archive collector runs used inventory-only mode; no physical camera or human gaze accuracy was tested.

The Studio and CLI ZIPs contain 3,837 files each, match their corresponding combined payloads and passed runner verification below 500 MiB. `windows_build.json` and collector `release_identity.json` files record exact bytes/hashes and unsigned status. Both binaries were subsequently downloaded and independently verified: all 7,674 file hashes, exact archive identities, combined-manifest coverage and collector executable identities matched. `downloaded_payload_verification_summary.json` records that separate check; it did not execute the Windows binaries. `windows_wheel_resources.json` records inspection of the actual Windows wheel, including exact font binaries and license text with only CRLF line-ending differences.

These compact files were copied byte-for-byte from the downloaded/hash-verified diagnostics artifact. Large per-file archive inventories, raw logs and complete raw diagnostics remain in the CI artifact and external delivery evidence bundle. Intermediate `.partial.json` receipts are retained as raw records; final results are the corresponding unsuffixed JSON files. Older CI progress/status snapshots are intentionally excluded from this current evidence folder.
