// ExportView.swift
//
// Builds the final scan bundle and hands it to the share sheet. The bundle
// is one folder under Documents/:
//
//   <scan-id>/
//     manifest.json   ScanMetadata, keys mirroring the PC manifest.yaml
//     mesh.obj        merged scene mesh, Z up, metres
//     mesh.usdz       the same mesh for Quick Look (best effort, non-fatal)
//     raw/            the generic raw depth package (intrinsics.json,
//                     poses.csv, depth/, rgb/, confidence/)
//
// On the PC: drop mesh.obj and the raw/ folder into scans/<name>/raw/, and
// merge manifest.json fields into the scan's manifest.yaml. See ../README.md.
//
// Sharing: the folder can be shared as-is (AirDrop, Save to Files), or as a
// real .zip. The zip comes from NSFileCoordinator's .forUploading path,
// which asks the system to produce a zip archive of a folder with no
// third-party code. AppleArchive was considered and rejected: it writes
// .aar archives, which neither Windows Explorer nor the PC parser's Python
// zipfile can open.

import ARKit
import SwiftUI
import UIKit

struct ExportView: View {
    @EnvironmentObject private var appState: AppState
    @ObservedObject var manager: ARSessionManager

    enum Stage: Equatable {
        case idle
        case building
        case ready
        case failed(String)
    }

    @State private var stage: Stage = .idle
    @State private var buildSummary: [String] = []
    @State private var shareItems: [URL]?
    @State private var zipInProgress = false

    var body: some View {
        List {
            Section {
                LabeledContent("Bundle", value: appState.scanID)
                LabeledContent("Raw frames", value: "\(manager.recordedFrameCount)")
                LabeledContent("Mesh chunks", value: "\(manager.meshAnchorCount)")
                LabeledContent("Control points", value: "\(manager.controlPoints.count)")
            } header: {
                Text("What will be bundled")
            } footer: {
                Text("Building freezes the AR session. Go back to capture more before building if anything is missing.")
            }

            Section {
                switch stage {
                case .idle:
                    Button("Build export bundle") { buildBundle() }
                        .disabled(manager.isRecording)
                case .building:
                    HStack {
                        ProgressView()
                        Text("Building bundle")
                            .padding(.leading, 8)
                    }
                case .ready:
                    ForEach(buildSummary, id: \.self) { line in
                        Label(line, systemImage: "checkmark.circle")
                            .font(.footnote)
                    }
                    Button {
                        shareItems = [appState.scanFolderURL]
                    } label: {
                        Label("Share folder", systemImage: "square.and.arrow.up")
                    }
                    Button {
                        shareAsZip()
                    } label: {
                        if zipInProgress {
                            HStack {
                                ProgressView()
                                Text("Zipping").padding(.leading, 8)
                            }
                        } else {
                            Label("Share as .zip", systemImage: "doc.zipper")
                        }
                    }
                    .disabled(zipInProgress)
                case .failed(let message):
                    Label(message, systemImage: "exclamationmark.triangle")
                        .foregroundStyle(.red)
                        .font(.footnote)
                    Button("Try again") { buildBundle() }
                }
            } header: {
                Text("Export")
            } footer: {
                Text("The bundle also stays in the app's Documents folder, reachable from the Files app and over USB, so a failed share loses nothing.")
            }
        }
        .navigationTitle("Export")
        .navigationBarTitleDisplayMode(.inline)
        .sheet(isPresented: .init(
            get: { shareItems != nil },
            set: { if !$0 { shareItems = nil } }
        )) {
            if let shareItems {
                ActivityView(items: shareItems)
            }
        }
    }

    // MARK: - Bundle build

    private func buildBundle() {
        stage = .building
        // Freeze the world: no new mesh chunks or frames while exporting.
        manager.pauseSession()

        // Snapshot control points into the manifest, converted from ARKit's
        // Y-up world to the pipeline's Z-up frame: (x, y, z) -> (x, -z, y).
        // This matches the frame every pose in raw/poses.csv is written in.
        appState.metadata.control.pickedPoints = manager.controlPoints.map { point in
            [Double(point.position.x), Double(-point.position.z), Double(point.position.y)]
        }
        appState.metadata.control.pickedPointNotes = manager.controlPoints.map(\.note)

        let metadata = appState.metadata
        let folderURL = appState.scanFolderURL
        let anchors = manager.currentMeshAnchors()

        DispatchQueue.global(qos: .userInitiated).async {
            var summary: [String] = []
            do {
                try FileManager.default.createDirectory(
                    at: folderURL, withIntermediateDirectories: true)

                let manifestURL = folderURL.appendingPathComponent("manifest.json")
                try metadata.manifestJSON().write(to: manifestURL, options: .atomic)
                summary.append("manifest.json written")

                let objURL = folderURL.appendingPathComponent("mesh.obj")
                try MeshExporter.writeOBJ(anchors: anchors, to: objURL)
                summary.append("mesh.obj written (Z up, metres)")

                // USDZ is a nice-to-have preview; failure must not sink the
                // bundle, because the OBJ and raw package carry the real data.
                let usdzURL = folderURL.appendingPathComponent("mesh.usdz")
                do {
                    try MeshExporter.writeUSDZ(anchors: anchors, to: usdzURL)
                    summary.append("mesh.usdz written")
                } catch {
                    summary.append("mesh.usdz skipped: \(error.localizedDescription)")
                }

                // Each recording take is its own package folder: raw, raw-2...
                let rawFolders = ((try? FileManager.default.contentsOfDirectory(
                    at: folderURL, includingPropertiesForKeys: nil)) ?? [])
                    .filter { $0.hasDirectoryPath && $0.lastPathComponent.hasPrefix("raw") }
                if rawFolders.isEmpty {
                    summary.append("no raw depth package: nothing was recorded")
                } else {
                    summary.append("\(rawFolders.count) raw depth package(s) included")
                }

                DispatchQueue.main.async {
                    buildSummary = summary
                    stage = .ready
                }
            } catch {
                DispatchQueue.main.async {
                    stage = .failed(error.localizedDescription)
                }
            }
        }
    }

    // MARK: - Zip

    private func shareAsZip() {
        zipInProgress = true
        let folderURL = appState.scanFolderURL
        DispatchQueue.global(qos: .userInitiated).async {
            do {
                let zipURL = try Self.zippedCopy(of: folderURL)
                DispatchQueue.main.async {
                    zipInProgress = false
                    shareItems = [zipURL]
                }
            } catch {
                DispatchQueue.main.async {
                    zipInProgress = false
                    stage = .failed("Zip failed: \(error.localizedDescription). Share the plain folder instead.")
                }
            }
        }
    }

    /// Ask the file coordinator for a zipped stand-in of the folder (the
    /// .forUploading path) and copy it somewhere stable before the
    /// coordinator reclaims it. Produces a genuine .zip.
    static func zippedCopy(of folderURL: URL) throws -> URL {
        var coordinatorError: NSError?
        var copyError: Error?
        var resultURL: URL?
        let coordinator = NSFileCoordinator()
        coordinator.coordinate(
            readingItemAt: folderURL, options: .forUploading,
            error: &coordinatorError
        ) { zippedURL in
            do {
                let destination = FileManager.default.temporaryDirectory
                    .appendingPathComponent(folderURL.lastPathComponent + ".zip")
                try? FileManager.default.removeItem(at: destination)
                try FileManager.default.copyItem(at: zippedURL, to: destination)
                resultURL = destination
            } catch {
                copyError = error
            }
        }
        if let coordinatorError { throw coordinatorError }
        if let copyError { throw copyError }
        guard let resultURL else {
            throw NSError(
                domain: "AuraScanCapture", code: 1,
                userInfo: [NSLocalizedDescriptionKey: "File coordinator produced no zip"])
        }
        return resultURL
    }
}

/// UIKit share sheet bridge; ShareLink handles single files well but folder
/// URLs travel more reliably through UIActivityViewController.
struct ActivityView: UIViewControllerRepresentable {
    let items: [Any]

    func makeUIViewController(context: Context) -> UIActivityViewController {
        UIActivityViewController(activityItems: items, applicationActivities: nil)
    }

    func updateUIViewController(_ uiViewController: UIActivityViewController, context: Context) {}
}
