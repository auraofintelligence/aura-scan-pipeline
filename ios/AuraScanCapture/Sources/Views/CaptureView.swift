// CaptureView.swift
//
// The main capture screen: a RealityKit ARView with the LiDAR scene mesh
// drawn live (debugOptions.showSceneUnderstanding), a record / pause / stop
// strip, per-mode coverage hints, and a control-points sub-mode where taps
// raycast onto the reconstructed scene.
//
// The finishing flow (metadata form, then export) presents as a sheet over
// this screen so the AR session and its mesh anchors stay alive underneath.

import ARKit
import RealityKit
import SwiftUI

struct CaptureView: View {
    let mode: CaptureMode

    @EnvironmentObject private var appState: AppState
    @StateObject private var sessionManager = ARSessionManager()

    @State private var showHints = true
    @State private var placingPoints = false
    @State private var showPointsSheet = false
    @State private var showFinishSheet = false
    @State private var errorMessage: String?

    var body: some View {
        ZStack {
            ARViewContainer(manager: sessionManager, placingPoints: placingPoints)
                .ignoresSafeArea()

            VStack(spacing: 0) {
                topBar
                if showHints { hintCard }
                Spacer()
                statusStrip
                controlStrip
            }
            .padding(.horizontal)
            .padding(.bottom, 12)
        }
        .preferredColorScheme(.dark)
        .sheet(isPresented: $showPointsSheet) {
            ControlPointsView(manager: sessionManager)
                .presentationDetents([.fraction(0.35), .medium])
                .presentationBackgroundInteraction(.enabled)
        }
        .sheet(isPresented: $showFinishSheet) {
            NavigationStack {
                MetadataFormView(manager: sessionManager)
            }
        }
        .alert("Capture problem", isPresented: .init(
            get: { errorMessage != nil },
            set: { if !$0 { errorMessage = nil } }
        )) {
            Button("OK", role: .cancel) {}
        } message: {
            Text(errorMessage ?? "")
        }
    }

    // MARK: - Overlay pieces

    private var topBar: some View {
        HStack {
            if !sessionManager.isRecording {
                Button {
                    appState.mode = nil
                } label: {
                    Label("Mode", systemImage: "chevron.backward")
                }
                .buttonStyle(.bordered)
            }
            Spacer()
            Text(mode.title)
                .font(.headline)
            Spacer()
            Button {
                showHints.toggle()
            } label: {
                Image(systemName: showHints ? "lightbulb.fill" : "lightbulb")
            }
            .buttonStyle(.bordered)
        }
        .padding(.top, 8)
    }

    private var hintCard: some View {
        Text(mode.guidance)
            .font(.footnote)
            .padding(10)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(.black.opacity(0.55), in: RoundedRectangle(cornerRadius: 10))
            .padding(.top, 8)
    }

    private var statusStrip: some View {
        VStack(spacing: 4) {
            Text(sessionManager.statusMessage)
                .font(.footnote)
            HStack(spacing: 16) {
                Label("\(sessionManager.recordedFrameCount)", systemImage: "square.stack.3d.up")
                Label("\(sessionManager.meshAnchorCount)", systemImage: "cube.transparent")
                Label("\(sessionManager.controlPoints.count)", systemImage: "mappin.and.ellipse")
            }
            .font(.caption.monospacedDigit())
            .foregroundStyle(.secondary)
        }
        .padding(8)
        .frame(maxWidth: .infinity)
        .background(.black.opacity(0.55), in: RoundedRectangle(cornerRadius: 10))
        .padding(.bottom, 8)
    }

    private var controlStrip: some View {
        HStack(spacing: 12) {
            recordButton

            if sessionManager.isRecording {
                Button {
                    sessionManager.stopRecording { failure in
                        if let failure { errorMessage = failure.localizedDescription }
                    }
                } label: {
                    Label("Stop", systemImage: "stop.fill")
                        .frame(maxWidth: .infinity)
                }
                .buttonStyle(.bordered)
                .tint(.orange)
            }

            Button {
                placingPoints.toggle()
                showPointsSheet = placingPoints
            } label: {
                Label("Points", systemImage: "mappin.and.ellipse")
                    .frame(maxWidth: .infinity)
            }
            .buttonStyle(.bordered)
            .tint(placingPoints ? Color.purple : Color.accentColor)

            Button {
                placingPoints = false
                showFinishSheet = true
            } label: {
                Label("Finish", systemImage: "checkmark")
                    .frame(maxWidth: .infinity)
            }
            .buttonStyle(.bordered)
            .tint(.green)
        }
    }

    @ViewBuilder
    private var recordButton: some View {
        if !sessionManager.isRecording {
            Button {
                do {
                    try sessionManager.startRecording(into: appState.nextRawPackageURL())
                } catch {
                    errorMessage = error.localizedDescription
                }
            } label: {
                Label("Record", systemImage: "record.circle")
                    .frame(maxWidth: .infinity)
            }
            .buttonStyle(.borderedProminent)
            .tint(.red)
        } else if sessionManager.isPaused {
            Button {
                sessionManager.resumeRecording()
            } label: {
                Label("Resume", systemImage: "play.fill")
                    .frame(maxWidth: .infinity)
            }
            .buttonStyle(.borderedProminent)
            .tint(.red)
        } else {
            Button {
                sessionManager.pauseRecording()
            } label: {
                Label("Pause", systemImage: "pause.fill")
                    .frame(maxWidth: .infinity)
            }
            .buttonStyle(.borderedProminent)
            .tint(.red)
        }
    }
}

// MARK: - ARView bridge

/// Hosts the RealityKit ARView, hands it to the session manager, and routes
/// taps to control-point raycasts while point placement is on.
struct ARViewContainer: UIViewRepresentable {
    let manager: ARSessionManager
    let placingPoints: Bool

    func makeUIView(context: Context) -> ARView {
        let arView = ARView(frame: .zero)
        manager.attach(to: arView)
        let tap = UITapGestureRecognizer(
            target: context.coordinator,
            action: #selector(Coordinator.handleTap(_:))
        )
        arView.addGestureRecognizer(tap)
        context.coordinator.arView = arView
        return arView
    }

    func updateUIView(_ uiView: ARView, context: Context) {
        context.coordinator.placingPoints = placingPoints
    }

    func makeCoordinator() -> Coordinator {
        Coordinator(manager: manager)
    }

    final class Coordinator: NSObject {
        let manager: ARSessionManager
        weak var arView: ARView?
        var placingPoints = false

        init(manager: ARSessionManager) {
            self.manager = manager
        }

        @objc func handleTap(_ gesture: UITapGestureRecognizer) {
            guard placingPoints, let arView else { return }
            manager.addControlPoint(at: gesture.location(in: arView))
        }
    }
}
