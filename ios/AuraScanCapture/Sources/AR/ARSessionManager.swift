// ARSessionManager.swift
// Owns the ARKit session: world tracking with LiDAR scene reconstruction and
// scene depth, mesh anchor bookkeeping, stride-based raw frame recording, and
// tap-to-raycast control points. UI state publishes on the main queue; the
// session delegate runs on its own serial queue.

import ARKit
import Combine
import Foundation
import RealityKit
import UIKit
import simd

struct ControlPoint: Identifiable, Equatable {
    let id: UUID
    var note: String
    var position: SIMD3<Float>   // ARKit world frame, metres
}

final class ARSessionManager: NSObject, ObservableObject {

    // MARK: Published UI state (main queue only)

    @Published private(set) var isRecording = false
    @Published private(set) var isPaused = false
    @Published private(set) var recordedFrameCount = 0
    @Published private(set) var meshAnchorCount = 0
    @Published private(set) var statusMessage = "Sweep the phone to start mapping"
    @Published var controlPoints: [ControlPoint] = []

    /// Record every Nth ARFrame; mirrors ingest.frame_stride on the PC side.
    var frameStride = 3

    // MARK: Internals

    private(set) weak var arView: ARView?
    private let sessionQueue = DispatchQueue(label: "earth.gajra.aurascan.arsession", qos: .userInitiated)
    private let stateLock = NSLock()
    private var recorder: RawFrameRecorder?
    private var recordingActive = false
    private var recordingPaused = false
    private var frameCounter = 0
    private var meshAnchors: [UUID: ARMeshAnchor] = [:]
    private var lastPublishedAnchorCount = -1   // touched on sessionQueue only

    // MARK: Session lifecycle

    func attach(to arView: ARView) {
        self.arView = arView
        arView.automaticallyConfigureSession = false
        arView.session.delegate = self
        arView.session.delegateQueue = sessionQueue
        arView.environment.sceneUnderstanding.options.insert(.occlusion)
        arView.debugOptions.insert(.showSceneUnderstanding)   // live mesh visualisation
        runConfiguration()
    }

    func runConfiguration(reset: Bool = false) {
        guard let session = arView?.session else { return }
        let config = ARWorldTrackingConfiguration()
        if ARWorldTrackingConfiguration.supportsSceneReconstruction(.mesh) {
            config.sceneReconstruction = .mesh
        } else {
            publish { self.statusMessage = "No LiDAR on this device: mesh capture unavailable" }
        }
        if ARWorldTrackingConfiguration.supportsFrameSemantics(.sceneDepth) {
            config.frameSemantics.insert(.sceneDepth)
        }
        config.environmentTexturing = .automatic
        config.planeDetection = [.horizontal, .vertical]
        let options: ARSession.RunOptions = reset
            ? [.resetTracking, .removeExistingAnchors, .resetSceneReconstruction]
            : []
        session.run(config, options: options)
        if reset {
            stateLock.lock()
            meshAnchors.removeAll()
            stateLock.unlock()
            publish {
                self.controlPoints.removeAll()
                self.statusMessage = "Session reset, sweep to remap"
            }
        }
    }

    func pauseSession() { arView?.session.pause() }
    func resumeSession() { runConfiguration() }

    // MARK: Recording

    func startRecording(into packageURL: URL) throws {
        let newRecorder = try RawFrameRecorder(packageURL: packageURL, writeRGB: true)
        stateLock.lock()
        recorder = newRecorder
        recordingActive = true
        recordingPaused = false
        frameCounter = 0
        stateLock.unlock()
        publish {
            self.isRecording = true
            self.isPaused = false
            self.recordedFrameCount = 0
            self.statusMessage = "Recording raw frames"
        }
    }

    func pauseRecording() {
        stateLock.lock(); recordingPaused = true; stateLock.unlock()
        publish {
            self.isPaused = true
            self.statusMessage = "Recording paused"
        }
    }

    func resumeRecording() {
        stateLock.lock(); recordingPaused = false; stateLock.unlock()
        publish {
            self.isPaused = false
            self.statusMessage = "Recording raw frames"
        }
    }

    /// Finalises poses.csv on the session queue so the close serialises with
    /// any in-flight frame write.
    func stopRecording(completion: ((Error?) -> Void)? = nil) {
        stateLock.lock()
        let finished = recorder
        recorder = nil
        recordingActive = false
        recordingPaused = false
        stateLock.unlock()
        sessionQueue.async {
            var failure: Error?
            do { try finished?.finish() } catch { failure = error }
            DispatchQueue.main.async {
                self.isRecording = false
                self.isPaused = false
                self.statusMessage = failure == nil ? "Recording saved" : "Recording finalise failed"
                completion?(failure)
            }
        }
    }

    // MARK: Mesh anchors

    /// Snapshot of the latest mesh chunks, safe to read from an export task.
    func currentMeshAnchors() -> [ARMeshAnchor] {
        stateLock.lock()
        defer { stateLock.unlock() }
        return Array(meshAnchors.values)
    }

    // MARK: Control points (main thread)

    /// Raycast from a screen tap onto the reconstructed scene and mark a point.
    func addControlPoint(at screenPoint: CGPoint) {
        guard let arView else { return }
        guard let hit = arView.raycast(from: screenPoint, allowing: .estimatedPlane, alignment: .any).first else {
            statusMessage = "No surface under that tap yet, keep scanning"
            return
        }
        let column = hit.worldTransform.columns.3
        let point = ControlPoint(
            id: UUID(),
            note: "P\(controlPoints.count)",
            position: SIMD3<Float>(column.x, column.y, column.z)
        )
        controlPoints.append(point)
        placeMarker(for: point)
        statusMessage = String(format: "Marked %@ at (%.2f, %.2f, %.2f)",
                               point.note, column.x, column.y, column.z)
    }

    func removeControlPoints(at offsets: IndexSet) {
        let removed = offsets.map { controlPoints[$0] }
        controlPoints.remove(atOffsets: offsets)
        guard let scene = arView?.scene else { return }
        for point in removed {
            if let anchor = scene.anchors.first(where: { $0.name == point.id.uuidString }) {
                scene.removeAnchor(anchor)
            }
        }
    }

    private func placeMarker(for point: ControlPoint) {
        guard let arView else { return }
        let sphere = ModelEntity(
            mesh: .generateSphere(radius: 0.012),
            materials: [SimpleMaterial(color: .magenta, isMetallic: false)]
        )
        let anchor = AnchorEntity(world: point.position)
        anchor.name = point.id.uuidString
        anchor.addChild(sphere)
        arView.scene.addAnchor(anchor)
    }

    // MARK: Helpers

    private func publish(_ change: @escaping () -> Void) {
        if Thread.isMainThread { change() } else { DispatchQueue.main.async(execute: change) }
    }
}

// MARK: - ARSessionDelegate (called on sessionQueue)

extension ARSessionManager: ARSessionDelegate {

    func session(_ session: ARSession, didUpdate frame: ARFrame) {
        stateLock.lock()
        let active = recordingActive && !recordingPaused
        let target = recorder
        let stride = max(1, frameStride)
        let index = frameCounter
        if active { frameCounter += 1 }
        stateLock.unlock()
        guard active, let target, index % stride == 0 else { return }
        do {
            let wrote = try target.record(frame: frame)
            if wrote {
                publish { self.recordedFrameCount += 1 }
            }
        } catch {
            publish { self.statusMessage = "Frame write failed: \(error.localizedDescription)" }
        }
    }

    func session(_ session: ARSession, didAdd anchors: [ARAnchor]) { ingest(anchors) }

    func session(_ session: ARSession, didUpdate anchors: [ARAnchor]) { ingest(anchors) }

    func session(_ session: ARSession, didRemove anchors: [ARAnchor]) {
        stateLock.lock()
        for anchor in anchors where anchor is ARMeshAnchor {
            meshAnchors.removeValue(forKey: anchor.identifier)
        }
        let count = meshAnchors.count
        stateLock.unlock()
        publishAnchorCount(count)
    }

    private func ingest(_ anchors: [ARAnchor]) {
        stateLock.lock()
        for anchor in anchors {
            if let mesh = anchor as? ARMeshAnchor {
                meshAnchors[mesh.identifier] = mesh
            }
        }
        let count = meshAnchors.count
        stateLock.unlock()
        publishAnchorCount(count)
    }

    private func publishAnchorCount(_ count: Int) {
        guard count != lastPublishedAnchorCount else { return }
        lastPublishedAnchorCount = count
        publish { self.meshAnchorCount = count }
    }
}
