# AuraScanCapture: the custom iOS capture app

Full SwiftUI + ARKit source for the phone side of the pipeline: LiDAR scene
reconstruction, raw depth recording in the generic package format that
`src/aura_scan/io/raw_rgbd.py` ingests, tap-to-raycast control points, a
manifest form, and a one-folder export bundle.

## Honest status

This is a source scaffold that has **never been compiled**. There is no Mac
on the build machine, and iOS apps can only be built with Xcode on a Mac. The
code is written carefully against Apple's documented APIs, but nobody should
pretend that a first build of never-compiled Swift goes clean. Expect a
shakedown session: a handful of small compile errors and one or two runtime
surprises, all of the fixable-in-minutes kind. The sharp edges section below
lists where they are most likely to bite.

Until this app is built, the pipeline's capture lane is the existing free
apps (Polycam, Scaniverse, 3d Scanner App, Stray Scanner); see
`../docs/formats.md`.

## What is in here

```
ios/
├── README.md                    this file
└── AuraScanCapture/
    ├── project.yml              XcodeGen spec (generates the .xcodeproj)
    └── Sources/
        ├── AuraScanCaptureApp.swift    entry point, mode selection
        ├── Models/
        │   ├── CaptureMode.swift       general / asset / projection_surface
        │   └── ScanMetadata.swift      manifest fields, exported as manifest.json
        ├── AR/
        │   ├── ARSessionManager.swift  ARKit session, mesh anchors, recording
        │   ├── RawFrameRecorder.swift  writes the generic raw depth package
        │   └── MeshExporter.swift      merged OBJ (Z up) and USDZ export
        └── Views/
            ├── CaptureView.swift       AR view, record controls, hints
            ├── ControlPointsView.swift picked points with live distances
            ├── MetadataFormView.swift  the manifest form
            └── ExportView.swift        bundle build and share sheet
```

The exported bundle is one folder: `manifest.json` + `mesh.obj` (Z up,
metres) + `mesh.usdz` (preview) + `raw/` (the generic depth package:
`intrinsics.json`, `poses.csv`, `depth/`, `rgb/`, `confidence/`).

## The borrowed-Mac afternoon, step by step

One session on a friend's Mac gets the app onto the phone. Budget two to
three hours the first time, most of it Xcode downloading.

1. **Install Xcode** from the Mac App Store (free, but a very large download;
   start it first and let it run). Open it once so it installs its extra
   components, and accept the licence prompt.
2. **Install Homebrew** if the Mac does not have it: instructions at
   [brew.sh](https://brew.sh), one paste-into-Terminal command.
3. **Install XcodeGen**: `brew install xcodegen`.
4. **Copy this folder onto the Mac** (AirDrop, USB stick, or clone the repo),
   then in Terminal:

   ```sh
   cd path/to/ios/AuraScanCapture
   xcodegen generate
   open AuraScanCapture.xcodeproj
   ```

5. **Signing, with a free Apple ID.** In Xcode: select the AuraScanCapture
   target, open Signing and Capabilities, tick Automatically manage signing,
   and add your Apple ID as the team (Xcode Settings, Accounts, add Apple
   ID; it appears as a Personal Team). No paid account needed.
   - The trade-off: apps signed with a free Personal Team **expire after 7
     days** and then need re-signing on a Mac (plug in, press Run again).
     Fine for field-testing bursts.
   - The paid Apple Developer Program (US$99 a year, about A$150 at July
     2026 rates) extends installs to a year and unlocks TestFlight. It can
     come later; nothing in the code changes.
   - If Xcode complains the bundle id is taken, change
     `au.auraofintelligence.aurascancapture` in `project.yml` to anything
     unique, re-run `xcodegen generate`, and reopen.
6. **Plug in the iPhone 14 Pro Max** with a cable. On the phone: trust the
   computer when asked, and enable Developer Mode (Settings, Privacy and
   Security, Developer Mode, then reboot). Back in Xcode, pick the phone as
   the run destination and press Run.
7. First launch on the phone: Settings, General, VPN and Device Management,
   trust your developer certificate, then launch again.
8. **Field check before leaving the Mac**: record 10 seconds of a desk,
   finish, export, and confirm the bundle contains `poses.csv` rows and
   numbered PNGs in `depth/`. If that works, everything downstream works.

Do not set up CI for this. One phone, one borrowed Mac, seven-day signing:
a pipeline would be theatre.

## Getting captures to the Windows PC

Three lanes, all offline-friendly; the scan bundles sit in the app's
Documents folder, which is exposed to all of them.

- **USB cable**: install the Apple Devices app from the Microsoft Store,
  plug the phone in, open the phone, Files tab, AuraScanCapture, and drag
  the scan folder out. (iTunes file sharing does the same on older setups.)
- **iCloud for Windows**: in the app, share the bundle to Save to Files and
  pick iCloud Drive; it appears in the iCloud Drive folder on the PC once
  both sides sync. Needs internet, so not a field lane.
- **LocalSend** ([localsend.org](https://localsend.org), free and open
  source, both platforms): share the zip from the app to LocalSend on the
  same wifi. Works on the island with no internet at all via a phone
  hotspot.

Then on the PC:

```sh
aura-scan init --name my-scan --mode asset
# drop mesh.obj and the raw folder(s) into scans/my-scan/raw/
# copy fields from manifest.json into scans/my-scan/manifest.yaml
aura-scan run scans/my-scan
```

Each recording take in the bundle is its own package folder (`raw`, `raw-2`
and so on); every one is detected as a generic raw depth package, TSDF-fused
and merged;
`mesh.obj` rides along as a second input. `manifest.json` keys mirror
`manifest.yaml` on purpose, including `control.picked_points` from the
phone's control points; add tape-measured `distance_constraints` on the PC
to trigger scale verification.

## Sharp edges the first build will likely hit

Ordered by likelihood, with the expected fix. Update this list during the
shakedown so the next Mac session starts smarter.

1. **Signing and provisioning.** The classic first wall: "Failed to register
   bundle identifier" or "requires a development team". Fix: unique bundle
   id, correct team selected, Developer Mode on, certificate trusted on the
   phone. All clicking, no code.
2. **Info.plist keys.** If the app crashes at launch on camera access, the
   generated Info.plist is missing `NSCameraUsageDescription`, meaning
   `project.yml`'s `info.properties` block did not survive generation.
   Check the generated Info.plist in Xcode; worst case, paste the key in by
   hand. `UIFileSharingEnabled` and `LSSupportsOpeningDocumentsInPlace` are
   what make Documents visible over USB; without them transfers silently
   lose their easiest lane.
3. **16-bit PNG writing** (`RawFrameRecorder.writeGrey16PNG`). The riskiest
   pure-code contract in the app: CGImage byte order flags are easy to get
   wrong, and a mistake produces byte-swapped depth (a 1.000 m reading
   becomes 59.395 m). Shakedown test: record a frame at a known arm's-length
   distance, open the PNG on the PC with Pillow, check the centre pixel is
   roughly the tape distance in millimetres. If values look wild, flip the
   `byteOrder16Big` flag to `byteOrder16Little` and drop the `.bigEndian`
   map in the same commit; they must change together.
4. **ARSession delegate queue.** `ARSessionManager.attach` sets
   `session.delegateQueue`, a real but less-travelled API. If the compiler
   or runtime objects, delete that line; delegate callbacks then arrive on
   the main thread, which is safe with the existing locking, just slightly
   jankier while recording.
5. **`scene.write(to:)` USDZ export.** SceneKit's USDZ writer is the least
   documented call in the app and has been moody across iOS versions. It is
   deliberately non-fatal: the bundle ships without `mesh.usdz` and says so.
   If it fails, ship anyway; the OBJ and raw package carry the real data.
6. **Zip via NSFileCoordinator.** The `.forUploading` trick produces a real
   zip, but it is an old corner of Foundation. If it misbehaves, share the
   plain folder; that path has no cleverness in it. (AppleArchive is not
   used because it writes `.aar`, which Windows and Python's zipfile cannot
   read.)
7. **Swift concurrency warnings.** The project pins language mode 5 with
   minimal strict concurrency. If a newer Xcode defaults harder and errors
   on `ARFrame` crossing queues, keep `SWIFT_STRICT_CONCURRENCY: minimal`
   in `project.yml` rather than fighting it during a borrowed-Mac session.
8. **SwiftUI availability drift.** `ContentUnavailableView`,
   `presentationBackgroundInteraction`, and `LabeledContent` are all iOS
   16/17 APIs and the target is iOS 17, so they should pass; if one errors
   on an older Xcode, replace it with a plain `VStack` or `HStack` and move
   on. Nothing structural depends on them.

## Format contract, restated

The recorder writes exactly what `src/aura_scan/io/raw_rgbd.py` reads:
`intrinsics.json` with `width, height, fx, fy, cx, cy` at depth resolution;
`poses.csv` with header `frame,x,y,z,qx,qy,qz,qw`, camera-to-world, metres,
quaternion scalar-last; `depth/00000.png` as 16-bit millimetre PNGs where 0
means no data; optional `rgb/00000.jpg`. World frame is Z up (rotated from
ARKit's Y up on the phone), camera frame is OpenCV convention. If the parser
and the recorder ever disagree, the parser wins and the recorder has a bug.
