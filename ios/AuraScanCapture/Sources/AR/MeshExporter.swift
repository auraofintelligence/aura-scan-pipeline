// MeshExporter.swift
//
// Merges every ARMeshAnchor into one mesh in the world frame and writes:
//   - mesh.obj  in the pipeline's frame (Z up, metres). The PC pipeline does
//     not rotate OBJ inputs (docs/formats.md "Axis conventions"), so the
//     rotation from ARKit's Y-up world happens here at write time.
//   - mesh.usdz via SceneKit, kept in ARKit's Y-up frame because Y up is the
//     USD convention and Quick Look previews expect it.
//
// ARMeshAnchor geometry lives in anchor-local coordinates inside MTLBuffers;
// each vertex is transformed by its anchor's transform to land in world
// space before merging.

import ARKit
import Foundation
import SceneKit
import UIKit
import simd

enum MeshExporter {

    enum ExportError: LocalizedError {
        case noMeshAnchors
        case objWriteFailed(String)
        case usdzWriteFailed

        var errorDescription: String? {
            switch self {
            case .noMeshAnchors:
                return "No reconstructed mesh yet. Sweep the scene before exporting."
            case .objWriteFailed(let reason):
                return "OBJ export failed: \(reason)"
            case .usdzWriteFailed:
                return "USDZ export failed (SceneKit declined to write the scene)"
            }
        }
    }

    struct MergedMesh {
        var vertices: [SIMD3<Float>] = []
        var triangles: [UInt32] = []   // three indices per face
        var anchorCount = 0
    }

    // MARK: - Merge

    /// Merge all anchors into a single indexed triangle soup in world space.
    /// zUp true rotates ARKit's Y-up world into the pipeline's Z-up frame.
    static func merge(anchors: [ARMeshAnchor], zUp: Bool) -> MergedMesh {
        var merged = MergedMesh()
        merged.anchorCount = anchors.count
        for anchor in anchors {
            let geometry = anchor.geometry
            let worldTransform = zUp
                ? RawFrameRecorder.yUpToZUp * anchor.transform
                : anchor.transform
            let baseIndex = UInt32(merged.vertices.count)

            let vertexSource = geometry.vertices
            merged.vertices.reserveCapacity(merged.vertices.count + vertexSource.count)
            for i in 0..<vertexSource.count {
                let local = vertex(in: vertexSource, at: i)
                let world = worldTransform * SIMD4<Float>(local.x, local.y, local.z, 1)
                merged.vertices.append(SIMD3<Float>(world.x, world.y, world.z))
            }

            let faces = geometry.faces
            guard faces.indexCountPerPrimitive == 3 else { continue }
            let indexCount = faces.count * 3
            merged.triangles.reserveCapacity(merged.triangles.count + indexCount)
            let contents = faces.buffer.contents()
            if faces.bytesPerIndex == MemoryLayout<UInt32>.size {
                let pointer = contents.assumingMemoryBound(to: UInt32.self)
                for i in 0..<indexCount {
                    merged.triangles.append(baseIndex + pointer[i])
                }
            } else if faces.bytesPerIndex == MemoryLayout<UInt16>.size {
                let pointer = contents.assumingMemoryBound(to: UInt16.self)
                for i in 0..<indexCount {
                    merged.triangles.append(baseIndex + UInt32(pointer[i]))
                }
            }
        }
        return merged
    }

    /// Read one vertex from an ARGeometrySource as three packed Floats.
    /// Deliberately not SIMD3<Float> loads: SIMD3 has 16-byte storage and a
    /// direct bind could over-read past the buffer on the final vertex.
    private static func vertex(in source: ARGeometrySource, at index: Int) -> SIMD3<Float> {
        let pointer = source.buffer.contents()
            .advanced(by: source.offset + index * source.stride)
            .assumingMemoryBound(to: Float.self)
        return SIMD3<Float>(pointer[0], pointer[1], pointer[2])
    }

    // MARK: - OBJ

    /// Write the merged scene mesh as OBJ, Z up, metres. Buffered writes keep
    /// memory flat even when the scene runs to hundreds of thousands of faces.
    static func writeOBJ(anchors: [ARMeshAnchor], to url: URL) throws {
        guard !anchors.isEmpty else { throw ExportError.noMeshAnchors }
        let merged = merge(anchors: anchors, zUp: true)
        guard !merged.vertices.isEmpty, !merged.triangles.isEmpty else {
            throw ExportError.noMeshAnchors
        }

        let fileManager = FileManager.default
        try? fileManager.removeItem(at: url)
        guard fileManager.createFile(atPath: url.path, contents: nil),
              let handle = try? FileHandle(forWritingTo: url) else {
            throw ExportError.objWriteFailed("could not open \(url.lastPathComponent)")
        }
        defer { try? handle.close() }

        var buffer = String()
        buffer.reserveCapacity(1 << 20)
        buffer += "# AuraScanCapture merged scene mesh\n"
        buffer += "# frame: right-handed, Z up, metres (rotated from ARKit Y up)\n"
        buffer += "# \(merged.vertices.count) vertices, \(merged.triangles.count / 3) triangles"
        buffer += " from \(merged.anchorCount) mesh anchors\n"

        func flushIfFull() throws {
            if buffer.utf8.count >= (1 << 20) {
                try handle.write(contentsOf: Data(buffer.utf8))
                buffer.removeAll(keepingCapacity: true)
            }
        }

        for v in merged.vertices {
            buffer += String(format: "v %.5f %.5f %.5f\n", v.x, v.y, v.z)
            try flushIfFull()
        }
        var i = 0
        while i < merged.triangles.count {
            // OBJ indices are 1-based.
            buffer += "f \(merged.triangles[i] + 1) \(merged.triangles[i + 1] + 1) \(merged.triangles[i + 2] + 1)\n"
            i += 3
            try flushIfFull()
        }
        try handle.write(contentsOf: Data(buffer.utf8))
    }

    // MARK: - USDZ

    /// Write the merged mesh as USDZ through SceneKit. Untextured, single
    /// grey material; good enough for Quick Look and AR hand-arounds. The
    /// archival geometry lane is the OBJ plus the raw depth package.
    static func writeUSDZ(anchors: [ARMeshAnchor], to url: URL) throws {
        guard !anchors.isEmpty else { throw ExportError.noMeshAnchors }
        let merged = merge(anchors: anchors, zUp: false)   // USD is Y up
        guard !merged.vertices.isEmpty, !merged.triangles.isEmpty else {
            throw ExportError.noMeshAnchors
        }

        let scnVertices = merged.vertices.map { SCNVector3($0.x, $0.y, $0.z) }
        let vertexSource = SCNGeometrySource(vertices: scnVertices)
        let indices = merged.triangles.map { Int32($0) }
        let element = SCNGeometryElement(indices: indices, primitiveType: .triangles)
        let geometry = SCNGeometry(sources: [vertexSource], elements: [element])

        let material = SCNMaterial()
        material.diffuse.contents = UIColor(white: 0.75, alpha: 1.0)
        material.isDoubleSided = true
        geometry.firstMaterial = material

        let scene = SCNScene()
        scene.rootNode.addChildNode(SCNNode(geometry: geometry))

        try? FileManager.default.removeItem(at: url)
        // SCNScene.write infers USDZ from the .usdz extension on iOS.
        guard scene.write(to: url, options: nil, delegate: nil, progressHandler: nil) else {
            throw ExportError.usdzWriteFailed
        }
    }
}
