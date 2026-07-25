// MetadataFormView.swift
//
// The manifest form, filled in while still standing at the subject. Fields
// mirror the PC pipeline's manifest.yaml (see ScanMetadata); the export step
// writes them as manifest.json inside the scan bundle. The asset type picker
// only appears in asset mode, matching how the PC configs branch.

import SwiftUI

struct MetadataFormView: View {
    @EnvironmentObject private var appState: AppState
    @ObservedObject var manager: ARSessionManager
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        Form {
            Section("Scan") {
                TextField("Name", text: $appState.metadata.name)
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()
                LabeledContent("Mode", value: appState.metadata.mode.title)
                if appState.metadata.mode == .asset {
                    Picker("Asset type", selection: $appState.metadata.assetType) {
                        ForEach(AssetType.allCases) { type in
                            Text(type.title).tag(type)
                        }
                    }
                    TextField("Condition (e.g. good, weathered, damaged)",
                              text: $appState.metadata.condition)
                }
            }

            Section("Notes") {
                TextField("Anything the PC operator should know",
                          text: $appState.metadata.notes, axis: .vertical)
                    .lineLimit(3...6)
            }

            Section("Links") {
                TextField("Places link id (e.g. public-point-lookout-skate-park)",
                          text: optionalBinding($appState.metadata.links.placesLinkId))
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()
                TextField("Sensorium tile id (optional)",
                          text: optionalBinding($appState.metadata.links.sensoriumTile))
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()
            }

            Section("Permissions") {
                TextField("Captured by", text: $appState.metadata.permissions.capturedBy)
                TextField("Consent note (who agreed to what)",
                          text: $appState.metadata.permissions.consentNote, axis: .vertical)
                    .lineLimit(2...4)
            }

            Section {
                LabeledContent("Captured at", value: appState.metadata.capture.capturedAt)
                LabeledContent("Raw frames", value: "\(manager.recordedFrameCount)")
                LabeledContent("Mesh chunks", value: "\(manager.meshAnchorCount)")
                LabeledContent("Control points", value: "\(manager.controlPoints.count)")
            } header: {
                Text("Session")
            } footer: {
                Text("Stop recording before exporting; the export step freezes the AR session.")
            }
        }
        .navigationTitle("Scan details")
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            ToolbarItem(placement: .cancellationAction) {
                Button("Back to capture") { dismiss() }
            }
            ToolbarItem(placement: .confirmationAction) {
                NavigationLink("Export") {
                    ExportView(manager: manager)
                }
                .disabled(manager.isRecording)
            }
        }
    }

    /// Bridge an optional string field to a TextField; empty text stores nil
    /// so the manifest omits the key instead of writing an empty string.
    private func optionalBinding(_ source: Binding<String?>) -> Binding<String> {
        Binding(
            get: { source.wrappedValue ?? "" },
            set: { source.wrappedValue = $0.isEmpty ? nil : $0 }
        )
    }
}
