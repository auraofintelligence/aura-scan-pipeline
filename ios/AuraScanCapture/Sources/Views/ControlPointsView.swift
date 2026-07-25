// ControlPointsView.swift
//
// Sheet shown while placing control points. Tap the camera view to raycast a
// point onto the reconstructed scene; this list shows each point with an
// editable note and the live straight-line distance to the previous point in
// metres. In the field you tape-measure one or two of these same segments
// and type the real numbers into manifest.yaml on the PC; the pipeline's
// scale solver compares scan distance against tape distance.
//
// Distances shown here are frame-independent, so it does not matter that the
// points are stored in ARKit's Y-up world; export converts them to the
// pipeline's Z-up frame (see ExportView).

import SwiftUI
import simd

struct ControlPointsView: View {
    @ObservedObject var manager: ARSessionManager
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            Group {
                if manager.controlPoints.isEmpty {
                    ContentUnavailableView(
                        "No control points yet",
                        systemImage: "mappin.slash",
                        description: Text("Tap a surface in the camera view to mark a point. Pick spots you can also reach with a tape measure: table edges, post bases, wall corners.")
                    )
                } else {
                    pointsList
                }
            }
            .navigationTitle("Control points")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .confirmationAction) {
                    Button("Done") { dismiss() }
                }
            }
        }
    }

    private var pointsList: some View {
        List {
            Section {
                ForEach(Array(manager.controlPoints.enumerated()), id: \.element.id) { index, point in
                    VStack(alignment: .leading, spacing: 4) {
                        HStack {
                            TextField("Note", text: noteBinding(for: point.id))
                                .font(.body)
                            Spacer()
                            if index > 0 {
                                Text(distanceLabel(from: index - 1, to: index))
                                    .font(.body.monospacedDigit())
                                    .foregroundStyle(.purple)
                            }
                        }
                        Text(positionLabel(point.position))
                            .font(.caption.monospacedDigit())
                            .foregroundStyle(.secondary)
                    }
                }
                .onDelete { offsets in
                    manager.removeControlPoints(at: offsets)
                }
            } footer: {
                footer
            }
        }
        .listStyle(.insetGrouped)
    }

    @ViewBuilder
    private var footer: some View {
        if manager.controlPoints.count >= 2 {
            Text("Distances are scan units, effectively metres. Tape-measure at least one segment in the real world and note which one; the PC pipeline uses it to verify scale.")
        }
    }

    // MARK: - Helpers

    private func noteBinding(for id: UUID) -> Binding<String> {
        Binding(
            get: {
                manager.controlPoints.first(where: { $0.id == id })?.note ?? ""
            },
            set: { newValue in
                if let index = manager.controlPoints.firstIndex(where: { $0.id == id }) {
                    manager.controlPoints[index].note = newValue
                }
            }
        )
    }

    private func distanceLabel(from a: Int, to b: Int) -> String {
        let points = manager.controlPoints
        guard points.indices.contains(a), points.indices.contains(b) else { return "" }
        let metres = simd_distance(points[a].position, points[b].position)
        return String(format: "%.3f m", metres)
    }

    private func positionLabel(_ position: SIMD3<Float>) -> String {
        String(format: "(%.2f, %.2f, %.2f)", position.x, position.y, position.z)
    }
}
