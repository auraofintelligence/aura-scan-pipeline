// AuraScanCaptureApp.swift
// App entry: pick a capture mode, then hand over to the capture screen.
// SwiftUI lifecycle; state that outlives the AR session lives in AppState.

import SwiftUI

@main
struct AuraScanCaptureApp: App {
    @StateObject private var appState = AppState()

    var body: some Scene {
        WindowGroup {
            if let mode = appState.mode {
                CaptureView(mode: mode)
                    .environmentObject(appState)
                    .preferredColorScheme(.dark)
            } else {
                ModeSelectView()
                    .environmentObject(appState)
            }
        }
    }
}

/// Session-wide state: chosen mode, scan folder on disk, manifest metadata.
final class AppState: ObservableObject {
    @Published var mode: CaptureMode? {
        didSet {
            if let mode { metadata.mode = mode }
        }
    }
    @Published var metadata = ScanMetadata()

    /// Folder name doubles as the default scan name, e.g. scan-20260724-153012.
    let scanID: String

    /// Everything for this scan lives under Documents/<scanID>/ so USB file
    /// sharing and the Files app can reach it without extra plumbing.
    let scanFolderURL: URL

    /// The raw depth package the PC pipeline ingests; see RawFrameRecorder for
    /// the format contract.
    var rawPackageURL: URL { scanFolderURL.appendingPathComponent("raw", isDirectory: true) }

    init() {
        let formatter = DateFormatter()
        formatter.dateFormat = "yyyyMMdd-HHmmss"
        formatter.locale = Locale(identifier: "en_US_POSIX")
        scanID = "scan-" + formatter.string(from: Date())
        let documents = FileManager.default.urls(for: .documentDirectory, in: .userDomainMask)[0]
        scanFolderURL = documents.appendingPathComponent(scanID, isDirectory: true)
        metadata.name = scanID
        metadata.capture.capturedAt = ISO8601DateFormatter().string(from: Date())
    }
}

/// First screen: choose how this capture will be processed on the PC side.
struct ModeSelectView: View {
    @EnvironmentObject private var appState: AppState

    var body: some View {
        NavigationStack {
            List(CaptureMode.allCases) { mode in
                Button {
                    appState.mode = mode
                } label: {
                    VStack(alignment: .leading, spacing: 6) {
                        Text(mode.title)
                            .font(.headline)
                        Text(mode.summary)
                            .font(.subheadline)
                            .foregroundStyle(.secondary)
                    }
                    .padding(.vertical, 4)
                }
            }
            .navigationTitle("Aura Scan")
        }
    }
}
